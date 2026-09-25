#!/usr/bin/env python3
"""Create a private local inventory without contacting hosts or replacing a file."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def install_inventory(source: Path, output: Path) -> None:
    content = source.read_text(encoding="utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Linking publishes a complete mode-0600 file and refuses even dangling links.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "inventory" / "hosts.yml.example",
        help="reviewed inventory template; contains no passwords",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "inventory" / "hosts.yml",
        help="local path; parent must exist; existing files are never replaced",
    )
    args = parser.parse_args(argv)
    try:
        install_inventory(args.source, args.output)
    except OSError as exc:
        print(f"error: inventory not written: {exc}", file=sys.stderr)
        return 1
    print(f"inventory-written: {args.output}")
    print("Review every CUSTOMIZE value, then run tests/validate_project.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
