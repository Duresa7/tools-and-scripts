#!/usr/bin/env python3
"""Create ignored local inventory and settings without contacting a host."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from linux_access_config import ALIAS, ROOT, resolve_settings, validate_inventory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, help="explicit non-secret local settings"
    )
    parser.add_argument("--host-alias", default="server-example")
    parser.add_argument("--host", default="192.0.2.20")
    parser.add_argument("--connection-user", default="deploy")
    parser.add_argument("--controller-hostname", help="use guarded local transport")
    parser.add_argument("--automation-user", dest="automation_account_user")
    parser.add_argument("--automation-home", dest="automation_account_home")
    parser.add_argument("--operator-user", dest="operator_user")
    parser.add_argument("--operator-home", dest="operator_home")
    parser.add_argument(
        "--inventory-output", type=Path, default=ROOT / "inventory" / "hosts.yml"
    )
    parser.add_argument("--config-output", type=Path, default=ROOT / "config.local.yml")
    return parser


def install_files(files: tuple[tuple[Path, str], ...]) -> None:
    """Install mode-0600 files, refusing collisions and cleaning up on error."""
    temporary: list[Path] = []
    installed: list[Path] = []
    if len({path.absolute() for path, _ in files}) != len(files):
        raise ValueError("output paths must be distinct")
    try:
        for path, contents in files:
            if path.exists() or path.is_symlink():
                raise FileExistsError("refusing to replace an existing output")
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary.append(Path(handle.name))
                os.fchmod(handle.fileno(), 0o600)
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
        for (path, _), temp in zip(files, temporary, strict=True):
            os.link(temp, path)
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
    try:
        settings = resolve_settings(
            args.config,
            {
                key: getattr(args, key)
                for key in (
                    "automation_account_user",
                    "automation_account_home",
                    "operator_user",
                    "operator_home",
                )
            },
        )
        if not ALIAS.fullmatch(args.host_alias):
            raise ValueError("host alias must be a plain inventory name")
        if not args.host or any(c.isspace() for c in args.host):
            raise ValueError("host must be a non-empty address or resolvable name")
        if args.connection_user in {
            settings["automation_account_user"],
            settings["operator_user"],
        }:
            raise ValueError("connection account must be independent")
        host_vars = {"ansible_host": args.host, "ansible_user": args.connection_user}
        if args.controller_hostname:
            host_vars.update(
                ansible_connection="local",
                baseline_controller_hostname=args.controller_hostname,
            )
        inventory = {
            "all": {
                "children": {
                    "automation_account_targets": {
                        "hosts": {args.host_alias: host_vars}
                    },
                    "automation_account_key_only": {"hosts": {}},
                    "operator_nopasswd_targets": {"hosts": {}},
                }
            }
        }
        validate_inventory(inventory)
        banner = (
            "# CUSTOMIZE: Review every generated value before running a playbook.\n"
        )
        install_files(
            (
                (
                    args.inventory_output,
                    banner + yaml.safe_dump(inventory, sort_keys=False),
                ),
                (
                    args.config_output,
                    banner + yaml.safe_dump(settings, sort_keys=False),
                ),
            )
        )
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        print(
            "error: invalid non-secret settings or output exists/is not writable",
            file=sys.stderr,
        )
        return 1
    print(f"inventory-written: {args.inventory_output}")
    print(f"configuration-written: {args.config_output}")
    print("next-step: review CUSTOMIZE values and install the public-key variables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
