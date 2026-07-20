#!/usr/bin/env python3
"""Create ignored SSH inventory and identity YAML from public information."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

IDENTITY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PUBLIC_KEY_PATTERN = re.compile(r"^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521))$")


def public_key_data(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8").strip()
    if "PRIVATE KEY" in text or "\n" in text:
        raise ValueError("--public-key-file must contain one public-key line")
    parts = text.split()
    if len(parts) < 2 or not PUBLIC_KEY_PATTERN.fullmatch(parts[0]):
        raise ValueError("unsupported or malformed public key")
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except binascii.Error as exc:
        raise ValueError("public-key data is not valid base64") from exc
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return text, f"SHA256:{digest}"


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-alias", required=True)
    parser.add_argument("--host", required=True, help="address or resolvable name")
    parser.add_argument("--connection-user", required=True)
    parser.add_argument("--managed-owner", required=True)
    parser.add_argument("--authorized-keys-path", required=True)
    parser.add_argument("--platform", choices=("posix", "windows"), required=True)
    parser.add_argument("--become", action="store_true")
    parser.add_argument("--become-user")
    parser.add_argument("--windows-account-type", choices=("standard", "administrator"))
    parser.add_argument("--identity-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument(
        "--inventory-output", type=Path, default=root / "inventory" / "hosts.yml"
    )
    parser.add_argument("--identity-output", type=Path)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not IDENTITY_PATTERN.fullmatch(args.identity_id):
        raise ValueError("identity ID must use lowercase letters, digits, and hyphens")
    if not args.host_alias or any(character.isspace() for character in args.host_alias):
        raise ValueError("host alias must be non-empty and contain no whitespace")
    if args.platform == "windows":
        if args.windows_account_type is None:
            raise ValueError("Windows targets require --windows-account-type")
        if args.become and not args.become_user:
            raise ValueError("elevated Windows writes require --become-user")
    elif args.windows_account_type is not None or args.become_user is not None:
        raise ValueError(
            "Windows account and become-user options need --platform windows"
        )


def inventory_payload(args: argparse.Namespace) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "ansible_host": args.host,
        "ansible_user": args.connection_user,
        "ssh_key_platform": args.platform,
        "ssh_key_owner": args.managed_owner,
        "ssh_authorized_keys_path": args.authorized_keys_path,
        "ssh_key_become": args.become,
        "ssh_key_manage_directory": True,
        "ssh_key_write_enabled": True,
    }
    if args.platform == "windows":
        variables.update(
            {
                "ansible_connection": "ssh",
                "ansible_shell_type": "powershell",
                "ssh_key_windows_account_type": args.windows_account_type,
            }
        )
        if args.become:
            variables["ansible_become_method"] = "runas"
            variables["ansible_become_user"] = args.become_user

    return {
        "all": {
            "children": {
                "ssh_key_supported": {"hosts": {args.host_alias: variables}},
                "ssh_key_unknown": {"hosts": {}},
                "ssh_identity_runtime_targets": {"hosts": {}},
            }
        }
    }


def identity_payload(
    args: argparse.Namespace, public_key: str, fingerprint: str
) -> dict[str, Any]:
    return {
        "ssh_identity": {
            "id": args.identity_id,
            "display_name": args.display_name,
            "fingerprint": fingerprint,
            "current_public_key": public_key,
            "target_hosts": [args.host_alias],
            "rotation": {
                "replacement_public_key": "",
                "operator_verified": False,
            },
        }
    }


def rendered_yaml(payload: dict[str, Any], comments: tuple[str, ...]) -> str:
    header = "---\n" + "\n".join(f"# CUSTOMIZE: {comment}" for comment in comments)
    return header + "\n" + yaml.safe_dump(payload, sort_keys=False, width=1000)


def install_two_files(files: tuple[tuple[Path, str], tuple[Path, str]]) -> None:
    temporary: list[Path] = []
    installed: list[Path] = []
    try:
        for path, contents in files:
            if path.exists():
                raise FileExistsError(f"refusing to replace existing file: {path}")
            if not path.parent.is_dir():
                raise FileNotFoundError(
                    f"output directory does not exist: {path.parent}"
                )
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            temporary.append(temp_path)

        for (path, _), temp_path in zip(files, temporary, strict=True):
            os.link(temp_path, path)
            installed.append(path)
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    identity_output = args.identity_output or (
        Path(__file__).resolve().parent / "identities" / f"{args.identity_id}.yml"
    )
    try:
        validate_args(args)
        public_key, fingerprint = public_key_data(args.public_key_file)
        inventory = rendered_yaml(
            inventory_payload(args),
            (
                "Confirm the host alias and target address.",
                "Confirm the connection account and managed key owner.",
                "Confirm the authorized-key path and elevation setting.",
            ),
        )
        identity = rendered_yaml(
            identity_payload(args, public_key, fingerprint),
            (
                "Confirm the identity ID, display name, and public-key fingerprint.",
                "Confirm the complete public key and exact host allowlist.",
                "Keep operator_verified false until replacement login testing passes.",
            ),
        )
        install_two_files(
            (
                (args.inventory_output, inventory),
                (identity_output, identity),
            )
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"inventory-written: {args.inventory_output}")
    print(f"identity-written: {identity_output}")
    print("private-key-handling=disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
