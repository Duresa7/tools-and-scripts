#!/usr/bin/env python3
"""Validate the SSH key rotation project without contacting any host."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:\\")
WINDOWS_ADMIN_KEYS_PATH = re.compile(
    r"^[A-Za-z]:\\ProgramData\\ssh\\administrators_authorized_keys$",
    re.IGNORECASE,
)
PUBLIC_KEY_TYPE_PATTERN = re.compile(
    r"^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521))$"
)
PLAYBOOKS = (
    "_load-identity.yml",
    "ssh-identity-onboard.yml",
    "ssh-key-audit.yml",
    "ssh-key-retire.yml",
    "ssh-key-stage.yml",
    "ssh-key-verify.yml",
)
TASK_FAMILIES = ("ensure-key-absent", "ensure-key-present", "read-key-state")
POWERSHELL_FILES = ("Manage-AuthorizedKey.ps1", "Read-AuthorizedKeyState.ps1")


def fingerprint(public_key: str) -> str:
    parts = public_key.split()
    if len(parts) < 2:
        raise ValueError("public key has fewer than two fields")
    if not PUBLIC_KEY_TYPE_PATTERN.fullmatch(parts[0]):
        raise ValueError(f"unsupported public key type: {parts[0]}")
    raw = base64.b64decode(parts[1], validate=True)
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


def collect_hosts(
    group: dict[str, Any], inherited: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Flatten host variables inherited through one inventory subtree."""

    if not isinstance(group, dict):
        raise ValueError("inventory group must be an object")
    raw_group_vars = group.get("vars") or {}
    raw_hosts = group.get("hosts") or {}
    raw_children = group.get("children") or {}
    if not all(
        isinstance(value, dict) for value in (raw_group_vars, raw_hosts, raw_children)
    ):
        raise ValueError("group vars, hosts, and children must be objects")

    group_vars = dict(inherited or {})
    group_vars.update(raw_group_vars)
    hosts: dict[str, dict[str, Any]] = {}
    for name, host_vars in raw_hosts.items():
        if host_vars is not None and not isinstance(host_vars, dict):
            raise ValueError(f"host variables must be an object: {name}")
        resolved = dict(group_vars)
        resolved.update(host_vars or {})
        hosts[str(name)] = resolved
    for child in raw_children.values():
        for name, host_vars in collect_hosts(child or {}, group_vars).items():
            if name in hosts:
                raise ValueError(f"duplicate host in inventory subtree: {name}")
            hosts[name] = host_vars
    return hosts


def validate_inventory(
    inventory: dict[str, Any], errors: list[str]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    try:
        children = inventory["all"]["children"]
        supported = collect_hosts(children["ssh_key_supported"])
        unknown = set(collect_hosts(children.get("ssh_key_unknown", {})))
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid inventory structure: {exc}")
        return {}, set()

    overlap = set(supported) & unknown
    if overlap:
        errors.append(f"supported and unknown groups overlap: {sorted(overlap)}")

    for name, variables in supported.items():
        platform = variables.get("ssh_key_platform")
        path = variables.get("ssh_authorized_keys_path")
        connection_user = variables.get("ansible_user")
        owner = variables.get("ssh_key_owner")
        become = variables.get("ssh_key_become", False)
        manage_directory = variables.get("ssh_key_manage_directory", True)
        writable = variables.get("ssh_key_write_enabled", True)
        if platform not in {"posix", "windows"}:
            errors.append(f"{name}: ssh_key_platform must be posix or windows")
        if not isinstance(writable, bool):
            errors.append(f"{name}: ssh_key_write_enabled must be boolean")
        if not isinstance(connection_user, str) or not connection_user:
            errors.append(f"{name}: ansible_user connection account is required")
        if not isinstance(owner, str) or not owner:
            errors.append(f"{name}: ssh_key_owner managed account is required")
        if not isinstance(become, bool):
            errors.append(f"{name}: ssh_key_become must be boolean")
        if not isinstance(manage_directory, bool):
            errors.append(f"{name}: ssh_key_manage_directory must be boolean")
        if not isinstance(path, str) or not path:
            errors.append(f"{name}: ssh_authorized_keys_path is required")
        elif platform == "posix" and not path.startswith("/"):
            errors.append(f"{name}: POSIX authorized-keys path must be absolute")
        elif platform == "windows" and not WINDOWS_ABSOLUTE_PATH.match(path):
            errors.append(f"{name}: Windows authorized-keys path must be absolute")

        if platform == "windows":
            account_type = variables.get("ssh_key_windows_account_type")
            if account_type not in {"standard", "administrator"}:
                errors.append(
                    f"{name}: ssh_key_windows_account_type must be standard "
                    "or administrator"
                )
            elif account_type == "administrator" and (
                not isinstance(path, str) or not WINDOWS_ADMIN_KEYS_PATH.match(path)
            ):
                errors.append(
                    f"{name}: administrator keys must use the ProgramData shared file"
                )
            elif (
                account_type == "standard"
                and isinstance(path, str)
                and WINDOWS_ADMIN_KEYS_PATH.match(path)
            ):
                errors.append(
                    f"{name}: standard users cannot use the administrator shared file"
                )
            if become is True and variables.get("ansible_become_method") != "runas":
                errors.append(
                    f"{name}: elevated Windows writes require "
                    "ansible_become_method=runas"
                )
            if become is True and not variables.get("ansible_become_user"):
                errors.append(
                    f"{name}: elevated Windows writes require ansible_become_user"
                )

        if writable is False:
            writer_name = variables.get("ssh_key_shared_writer")
            writer = supported.get(str(writer_name))
            if writer is None:
                errors.append(f"{name}: shared writer is missing from supported hosts")
            elif writer.get("ssh_key_write_enabled", True) is not True:
                errors.append(f"{name}: shared writer is not writable")
            elif writer.get("ssh_authorized_keys_path") != path:
                errors.append(f"{name}: shared writer uses a different key path")
            elif writer.get("ssh_key_platform") != platform:
                errors.append(f"{name}: shared writer uses a different platform")
            elif writer.get("ssh_key_owner") != owner:
                errors.append(f"{name}: shared writer uses a different managed owner")
            elif writer.get("ssh_key_windows_account_type") != variables.get(
                "ssh_key_windows_account_type"
            ):
                errors.append(f"{name}: shared writer uses a different account type")
    return supported, unknown


def validate_identity(
    path: Path, supported: dict[str, dict[str, Any]], errors: list[str]
) -> str | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("identity file root must be an object")
        identity = payload.get("ssh_identity")
        if not isinstance(identity, dict):
            raise TypeError("ssh_identity must be an object")
    except (OSError, TypeError, KeyError, yaml.YAMLError) as exc:
        errors.append(f"{path.name}: cannot read identity: {exc}")
        return None

    identity_id = identity.get("id")
    if identity_id != path.stem or not isinstance(identity_id, str):
        errors.append(f"{path.name}: id must match the filename")
        return None
    if not IDENTITY_ID_PATTERN.fullmatch(identity_id):
        errors.append(
            f"{path.name}: id must use lowercase letters, digits, and hyphens"
        )

    display_name = identity.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        errors.append(f"{path.name}: display_name is required")

    current_key = identity.get("current_public_key")
    if not isinstance(current_key, str):
        errors.append(f"{path.name}: current_public_key must be a string")
        current_key = ""
    try:
        actual_fingerprint = fingerprint(current_key)
    except (ValueError, binascii.Error) as exc:
        errors.append(f"{path.name}: invalid current public key: {exc}")
    else:
        if actual_fingerprint != identity.get("fingerprint"):
            errors.append(f"{path.name}: fingerprint mismatch")

    targets = identity.get("target_hosts")
    if (
        not isinstance(targets, list)
        or not targets
        or not all(isinstance(target, str) and target for target in targets)
    ):
        errors.append(f"{path.name}: target_hosts must be a non-empty string list")
        targets = []
    elif len(targets) != len(set(targets)):
        errors.append(f"{path.name}: target_hosts contains duplicates")

    extra_targets = set(targets) - set(supported)
    if extra_targets:
        errors.append(f"{path.name}: unsupported targets {sorted(extra_targets)}")
    for target in set(targets) & set(supported):
        variables = supported[target]
        if variables.get("ssh_key_write_enabled", True) is False:
            writer = variables.get("ssh_key_shared_writer")
            if writer not in targets:
                errors.append(
                    f"{path.name}: {target} requires shared writer {writer} "
                    "in target_hosts"
                )

    rotation = identity.get("rotation")
    if not isinstance(rotation, dict):
        errors.append(f"{path.name}: rotation must be an object")
        return identity_id
    replacement = rotation.get("replacement_public_key", "")
    verified = rotation.get("operator_verified")
    if not isinstance(replacement, str):
        errors.append(f"{path.name}: replacement_public_key must be a string")
        replacement = ""
    if not isinstance(verified, bool):
        errors.append(f"{path.name}: operator_verified must be boolean")
    if replacement:
        try:
            fingerprint(replacement)
            current_material = " ".join(current_key.split()[:2])
            replacement_material = " ".join(replacement.split()[:2])
            if current_material == replacement_material:
                errors.append(f"{path.name}: replacement equals the current key")
        except (ValueError, binascii.Error) as exc:
            errors.append(f"{path.name}: invalid replacement public key: {exc}")
    elif verified is True:
        errors.append(
            f"{path.name}: operator_verified cannot be true without a replacement"
        )
    return identity_id


def validate_project(
    root: Path = ROOT, inventory_filename: str = "hosts.yml"
) -> list[str]:
    errors: list[str] = []
    inventory_path = root / "inventory" / inventory_filename
    try:
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot read inventory: {exc}"]
    if not isinstance(inventory, dict):
        return ["inventory root must be an object"]

    supported, _ = validate_inventory(inventory, errors)
    identity_ids: set[str] = set()
    key_materials: set[str] = set()
    for path in sorted((root / "identities").glob("*.yml")):
        identity_id = validate_identity(path, supported, errors)
        if identity_id in identity_ids:
            errors.append(f"{path.name}: duplicate identity id {identity_id}")
        if identity_id:
            identity_ids.add(identity_id)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            identity = payload["ssh_identity"]
            current_key = identity["current_public_key"]
            material = (
                " ".join(current_key.split()[:2])
                if isinstance(current_key, str)
                else ""
            )
        except (OSError, TypeError, KeyError, yaml.YAMLError):
            continue
        if material and material in key_materials:
            errors.append(f"{path.name}: duplicate current public-key material")
        if material:
            key_materials.add(material)

    for playbook in PLAYBOOKS:
        if not (root / "playbooks" / playbook).is_file():
            errors.append(f"missing playbook: {playbook}")
    for family in TASK_FAMILIES:
        for suffix in (".yml", "-posix.yml", "-windows.yml"):
            path = root / "playbooks" / "tasks" / f"{family}{suffix}"
            if not path.is_file():
                errors.append(f"missing task file: {path.name}")
    for filename in POWERSHELL_FILES:
        if not (root / "playbooks" / "files" / filename).is_file():
            errors.append(f"missing PowerShell file: {filename}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        default="hosts.yml.example",
        help="inventory filename under inventory/ (default: hosts.yml.example)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_project(inventory_filename=args.inventory)
    if errors:
        print("SSH key rotation validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    inventory = yaml.safe_load(
        (ROOT / "inventory" / args.inventory).read_text(encoding="utf-8")
    )
    children = inventory["all"]["children"]
    supported = collect_hosts(children["ssh_key_supported"])
    unknown = collect_hosts(children.get("ssh_key_unknown", {}))
    identities = list((ROOT / "identities").glob("*.yml"))
    identity_label = "identity" if len(identities) == 1 else "identities"
    host_label = "host" if len(unknown) == 1 else "hosts"
    print(
        f"Validation passed: {len(identities)} local {identity_label}, "
        f"{len(supported)} supported hosts, and {len(unknown)} unknown {host_label}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
