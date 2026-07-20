#!/usr/bin/env python3
"""Create local Semaphore SQLite guard settings without opening the database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from semaphore_sqlite import DEFAULT_FILENAME_TEMPLATE, FILENAME_TEMPLATE_PATTERN


def database_candidates() -> list[Path]:
    candidates: list[Path] = []
    environment_path = os.environ.get("SEMAPHORE_DB_PATH")
    if environment_path:
        candidates.append(Path(environment_path).expanduser())
    candidates.extend(
        (
            Path("/var/lib/semaphore/database.sqlite"),
            Path.home() / ".semaphore" / "database.sqlite",
            Path.cwd() / "database.sqlite",
        )
    )
    if os.name == "nt" and os.environ.get("PROGRAMDATA"):
        candidates.append(
            Path(os.environ["PROGRAMDATA"]) / "Semaphore" / "database.sqlite"
        )
    return candidates


def discover_database() -> Path | None:
    return next(
        (path.resolve() for path in database_candidates() if path.is_file()), None
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument("--database", type=Path, help="live SQLite database path")
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=Path.home() / "semaphore-backups",
    )
    parser.add_argument("--filename-template", default=DEFAULT_FILENAME_TEMPLATE)
    parser.add_argument("--require-secret-records", action="store_true")
    return parser


def toml_string(value: str) -> str:
    return json.dumps(value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1
    if not FILENAME_TEMPLATE_PATTERN.fullmatch(args.filename_template):
        print(
            "error: filename template must contain one {timestamp} and safe characters",
            file=sys.stderr,
        )
        return 1

    database = args.database.expanduser() if args.database else discover_database()
    if database is None:
        checked = ", ".join(str(path) for path in database_candidates())
        print(f"error: no database found; checked {checked}", file=sys.stderr)
        print("error: pass --database with the correct local path", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    backup_directory = args.backup_directory.expanduser().resolve()
    contents = "\n".join(
        (
            "[semaphore]",
            "# CUSTOMIZE: Confirm the discovered live database path.",
            f"database_path = {toml_string(str(database.resolve()))}",
            "# CUSTOMIZE: Set a private directory for new backup files.",
            f"backup_directory = {toml_string(str(backup_directory))}",
            "# CUSTOMIZE: Keep {timestamp} once; edit only the surrounding text.",
            f"filename_template = {toml_string(args.filename_template)}",
            "# CUSTOMIZE: Require records in both secret-bearing tables when true.",
            f"require_secret_records = {str(args.require_secret_records).lower()}",
            "",
        )
    )
    try:
        # Exclusive creation closes the race between the early existence check and
        # this write. A concurrently created local config is never replaced.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    print(f"configuration-written: {args.output}")
    print("discovery: local paths only; the database was not opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
