#!/usr/bin/env python3
"""Create a private local fleet inventory without replacing an existing file."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "fleet_inventory_validator", ROOT / "tests" / "validate_project.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def install_inventory(path: Path, contents: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        # Hard-link installation refuses existing files, including dangling links.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.example.yml",
        help="explicit reviewed inventory YAML; defaults to the annotated example",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "inventory" / "hosts.yml",
        help="local inventory path; existing files are never replaced",
    )
    args = parser.parse_args(argv)
    try:
        contents = args.config.read_text(encoding="utf-8")
        inventory = VALIDATOR.parse_yaml(contents)
        errors = VALIDATOR.validate_inventory(inventory)
        if errors:
            raise ValueError("; ".join(errors))
        install_inventory(args.output, contents)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"inventory-written: {args.output}")
    print("next-step: replace CUSTOMIZE values and validate before contacting hosts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
