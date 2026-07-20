#!/usr/bin/env python3
"""Create ignored SSH inventory and identity YAML from public information."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

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


def yaml_scalar(value: str | bool) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    # JSON strings are valid YAML scalars and preserve Windows paths and key comments.
    return json.dumps(value, ensure_ascii=False)


def render_inventory(args: argparse.Namespace) -> str:
    lines = [
        "---",
        "all:",
        "  children:",
        "    ssh_key_supported:",
        "      hosts:",
        "        # CUSTOMIZE: Rename this inventory alias if needed.",
        f"        {yaml_scalar(args.host_alias)}:",
        "          # CUSTOMIZE: Confirm the target address or resolvable name.",
        f"          ansible_host: {yaml_scalar(args.host)}",
        "          # CUSTOMIZE: Confirm the ordinary OpenSSH connection account.",
        f"          ansible_user: {yaml_scalar(args.connection_user)}",
        "          # CUSTOMIZE: Confirm the managed operating-system family.",
        f"          ssh_key_platform: {yaml_scalar(args.platform)}",
        "          # CUSTOMIZE: Confirm the account that owns the key file.",
        f"          ssh_key_owner: {yaml_scalar(args.managed_owner)}",
        "          # CUSTOMIZE: Confirm the absolute authorized_keys path.",
        f"          ssh_authorized_keys_path: {yaml_scalar(args.authorized_keys_path)}",
        "          # CUSTOMIZE: Set true only when the connection account "
        "needs elevation.",
        f"          ssh_key_become: {yaml_scalar(args.become)}",
        "          # CUSTOMIZE: Keep true only when this tool may create "
        "the key directory.",
        "          ssh_key_manage_directory: true",
        "          # CUSTOMIZE: Keep true only when this host may change the key file.",
        "          ssh_key_write_enabled: true",
    ]
    if args.platform == "windows":
        lines.extend(
            (
                "          ansible_connection: ssh",
                "          ansible_shell_type: powershell",
                "          # CUSTOMIZE: Confirm standard or administrator "
                "key-file handling.",
                "          ssh_key_windows_account_type: "
                f"{yaml_scalar(args.windows_account_type)}",
            )
        )
        if args.become:
            lines.extend(
                (
                    "          ansible_become_method: runas",
                    "          # CUSTOMIZE: Confirm the Windows account used "
                    "for elevation.",
                    f"          ansible_become_user: {yaml_scalar(args.become_user)}",
                )
            )
    lines.extend(
        (
            "    ssh_key_unknown:",
            "      hosts: {}",
            "    ssh_identity_runtime_targets:",
            "      hosts: {}",
            "",
        )
    )
    return "\n".join(lines)


def render_identity(args: argparse.Namespace, public_key: str, fingerprint: str) -> str:
    return "\n".join(
        (
            "---",
            "ssh_identity:",
            "  # CUSTOMIZE: Confirm the stable identity ID matches this filename.",
            f"  id: {yaml_scalar(args.identity_id)}",
            "  # CUSTOMIZE: Confirm the human-readable owner or device label.",
            f"  display_name: {yaml_scalar(args.display_name)}",
            "  # CUSTOMIZE: Verify this fingerprint against the public-key file.",
            f"  fingerprint: {yaml_scalar(fingerprint)}",
            "  # CUSTOMIZE: Confirm this is one complete public key, never "
            "a private key.",
            f"  current_public_key: {yaml_scalar(public_key)}",
            "  target_hosts:",
            "    # CUSTOMIZE: Keep the exact inventory aliases that use this identity.",
            f"    - {yaml_scalar(args.host_alias)}",
            "  rotation:",
            "    # CUSTOMIZE: Add a distinct replacement public key when "
            "rotation starts.",
            '    replacement_public_key: ""',
            "    # CUSTOMIZE: Keep false until replacement login succeeds everywhere.",
            "    operator_verified: false",
            "",
        )
    )


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
        inventory = render_inventory(args)
        identity = render_identity(args, public_key, fingerprint)
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
