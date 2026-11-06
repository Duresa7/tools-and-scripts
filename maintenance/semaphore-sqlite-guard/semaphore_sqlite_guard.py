#!/usr/bin/env python3
"""Back up a Semaphore SQLite database and compare pre-change state."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

SAFE_COLUMNS: dict[str, tuple[str, ...]] = {
    "project": ("id", "name", "type", "default_secret_storage_id"),
    "project__environment": ("id", "project_id", "name", "secret_storage_id"),
    "project__inventory": (
        "id",
        "project_id",
        "type",
        "inventory",
        "ssh_key_id",
        "name",
        "become_key_id",
        "repository_id",
        "runner_tag",
    ),
    "project__repository": (
        "id",
        "project_id",
        "git_url",
        "ssh_key_id",
        "name",
        "git_branch",
    ),
    "project__template": (
        "id",
        "project_id",
        "inventory_id",
        "repository_id",
        "playbook",
        "name",
        "type",
        "view_id",
        "app",
        "git_branch",
        "runner_tag",
    ),
    "project__view": (
        "id",
        "title",
        "project_id",
        "position",
        "hidden",
        "type",
        "filter",
        "sort_column",
        "sort_reverse",
    ),
    "access_key": (
        "id",
        "name",
        "type",
        "project_id",
        "environment_id",
        "user_id",
        "owner",
        "storage_id",
        "source_storage_id",
        "source_storage_key",
        "source_storage_type",
    ),
    "project__template_environment": ("template_id", "environment_id"),
}

SECRET_QUERIES: dict[str, str] = {
    "access-key-secrets": "SELECT id, secret FROM access_key ORDER BY id",
    "environment-payloads": (
        "SELECT id, password, json, env FROM project__environment ORDER BY id"
    ),
}

DEFAULT_FILENAME_TEMPLATE = "semaphore-{timestamp}.sqlite"
FILENAME_TEMPLATE_PATTERN = re.compile(r"^[A-Za-z0-9._-]*\{timestamp\}[A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class SecretDigest:
    digest: str
    rows: int


@dataclass(frozen=True)
class ComparisonReport:
    live_integrity: str
    backup_integrity: str
    structure_matches: bool
    secret_sets_match: bool
    required_secret_sets_present: bool
    template_environment_links: int | None
    object_counts: dict[str, int]

    @property
    def matches(self) -> bool:
        return (
            self.live_integrity == "ok"
            and self.backup_integrity == "ok"
            and self.structure_matches
            and self.secret_sets_match
            and self.required_secret_sets_present
        )


@dataclass(frozen=True)
class Settings:
    database_path: Path | None = None
    backup_directory: Path | None = None
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    require_secret_records: bool = False


def read_only_uri(path: Path) -> str:
    encoded_path = quote(path.resolve().as_posix(), safe="/:")
    return f"file:{encoded_path}?mode=ro"


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"database is not a file: {path}")
    connection = sqlite3.connect(read_only_uri(path), uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def integrity_result(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "no result"


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }


def safe_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    """Read structural columns that do not contain credential payloads."""

    available_tables = table_names(connection)
    missing_tables = sorted(set(SAFE_COLUMNS) - available_tables)
    if missing_tables:
        raise ValueError(
            f"unsupported Semaphore schema; missing tables: {missing_tables}"
        )

    snapshot: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}
    for table, columns in SAFE_COLUMNS.items():
        available_columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        }
        selected = tuple(column for column in columns if column in available_columns)
        if not selected:
            raise ValueError(
                f"unsupported Semaphore schema; {table} has no safe columns"
            )
        order_by = ", ".join(selected)
        query = f"SELECT {order_by} FROM {table} ORDER BY {order_by}"
        rows = [tuple(row) for row in connection.execute(query)]
        snapshot[table] = (selected, rows)
    return snapshot


def _encoded_value(value: Any) -> bytes:
    if value is None:
        return b"N"
    if isinstance(value, bytes):
        return b"B" + len(value).to_bytes(8, "big") + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"S" + len(encoded).to_bytes(8, "big") + encoded
    if isinstance(value, int):
        return f"I{value}".encode()
    if isinstance(value, float):
        return f"F{value!r}".encode()
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def digest_rows(rows: Iterable[tuple[Any, ...]]) -> SecretDigest:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        count += 1
        digest.update(b"R")
        for value in row:
            encoded = _encoded_value(value)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return SecretDigest(digest.hexdigest(), count)


def secret_digests(connection: sqlite3.Connection) -> dict[str, SecretDigest]:
    return {
        name: digest_rows(tuple(row) for row in connection.execute(query))
        for name, query in SECRET_QUERIES.items()
    }


def digests_match(
    live: dict[str, SecretDigest], backup: dict[str, SecretDigest]
) -> bool:
    if live.keys() != backup.keys():
        return False
    return all(
        live[name].rows == backup[name].rows
        and hmac.compare_digest(live[name].digest, backup[name].digest)
        for name in live
    )


def current_windows_user_sid() -> str:
    completed = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("whoami.exe could not determine the current user SID")
    rows = list(csv.reader(completed.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) < 2 or not rows[0][1].startswith("S-1-"):
        raise RuntimeError("whoami.exe returned an invalid current user SID")
    return rows[0][1]


def restrict_output_permissions(path: Path) -> str:
    if os.name == "posix":
        os.chmod(path, 0o600)
        return "0600"
    if os.name == "nt":
        user_sid = current_windows_user_sid()
        command = [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{user_sid}:(F)",
            "/grant:r",
            "*S-1-5-18:(F)",
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError("icacls.exe could not restrict the backup ACL")
        return "current-user-and-system"
    raise RuntimeError(f"unsupported permission model: {os.name}")


def backup_database(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("source and destination must be different paths")
    if not source.is_file():
        raise FileNotFoundError(f"source is not a file: {source}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"destination directory does not exist: {destination.parent}"
        )

    destination_created = False
    try:
        descriptor = os.open(
            destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode=0o600
        )
        os.close(descriptor)
        destination_created = True
        restrict_output_permissions(destination)

        with connect_read_only(source) as live:
            source_integrity = integrity_result(live)
            if source_integrity != "ok":
                raise RuntimeError(
                    f"source database integrity check failed: {source_integrity}"
                )
            with sqlite3.connect(destination) as backup:
                live.backup(backup)

        restrict_output_permissions(destination)
        with connect_read_only(destination) as backup:
            result = integrity_result(backup)
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
    except Exception:
        if destination_created and destination.exists():
            destination.unlink()
        raise


def compare_databases(
    live_path: Path, backup_path: Path, require_secret_records: bool = False
) -> ComparisonReport:
    with connect_read_only(live_path) as live:
        live_integrity = integrity_result(live)
        live_snapshot = safe_snapshot(live)
        live_secrets = secret_digests(live)
        template_links = len(live_snapshot["project__template_environment"][1])

    with connect_read_only(backup_path) as backup:
        backup_integrity = integrity_result(backup)
        backup_snapshot = safe_snapshot(backup)
        backup_secrets = secret_digests(backup)

    required_present = not require_secret_records or all(
        digest.rows > 0 for digest in live_secrets.values()
    )
    counts = {
        table: len(snapshot[1])
        for table, snapshot in live_snapshot.items()
        if table in {"access_key", "project", "project__template", "project__view"}
    }
    return ComparisonReport(
        live_integrity=live_integrity,
        backup_integrity=backup_integrity,
        structure_matches=live_snapshot == backup_snapshot,
        secret_sets_match=digests_match(live_secrets, backup_secrets),
        required_secret_sets_present=required_present,
        template_environment_links=template_links,
        object_counts=counts,
    )


def _config_path(value: Any, field: str, base_directory: Path) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string path")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_directory / path


def parse_settings(payload: Any, base_directory: Path | None = None) -> Settings:
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    section = payload.get("semaphore", {})
    if not isinstance(section, dict):
        raise ValueError("[semaphore] must be a TOML table")
    base = (base_directory or Path.cwd()).resolve()

    filename_template = section.get("filename_template", DEFAULT_FILENAME_TEMPLATE)
    if not isinstance(
        filename_template, str
    ) or not FILENAME_TEMPLATE_PATTERN.fullmatch(filename_template):
        raise ValueError(
            "filename_template must contain one {timestamp} and only safe "
            "filename characters"
        )
    require_secret_records = section.get("require_secret_records", False)
    if not isinstance(require_secret_records, bool):
        raise ValueError("require_secret_records must be true or false")

    return Settings(
        database_path=_config_path(section.get("database_path"), "database_path", base),
        backup_directory=_config_path(
            section.get("backup_directory"), "backup_directory", base
        ),
        filename_template=filename_template,
        require_secret_records=require_secret_records,
    )


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    with path.open("rb") as handle:
        return parse_settings(tomllib.load(handle), path.resolve().parent)


def timestamped_destination(settings: Settings, now: datetime | None = None) -> Path:
    if settings.backup_directory is None:
        raise ValueError(
            "backup destination is required when backup_directory is not configured"
        )
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    filename = settings.filename_template.format(timestamp=timestamp)
    return settings.backup_directory / filename


def resolve_backup_paths(
    settings: Settings, source: Path | None, destination: Path | None
) -> tuple[Path, Path]:
    resolved_source = source or settings.database_path
    if resolved_source is None:
        raise ValueError(
            "backup source is required when database_path is not configured"
        )
    return resolved_source, destination or timestamped_destination(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="local TOML settings copied from config.example.toml",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser(
        "backup", help="Create an online backup and verify both databases"
    )
    backup.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="live Semaphore SQLite path; overrides database_path from config",
    )
    backup.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help="new backup path; overrides timestamped config naming and must not exist",
    )
    backup.add_argument(
        "--dry-run",
        action="store_true",
        help="check source integrity and print the destination without creating it",
    )

    compare = subparsers.add_parser(
        "compare", help="Compare live state with a pre-change backup"
    )
    compare.add_argument(
        "live_database",
        nargs="?",
        type=Path,
        help="current live database; overrides database_path from config",
    )
    compare.add_argument(
        "backup_database",
        nargs="?",
        type=Path,
        help="path to the pre-change backup",
    )
    policy = compare.add_mutually_exclusive_group()
    policy.add_argument(
        "--require-secret-records",
        action="store_true",
        default=None,
        help="Fail if either expected secret-bearing table has no rows",
    )
    policy.add_argument(
        "--allow-empty-secret-records",
        action="store_false",
        dest="require_secret_records",
        help="override config and allow empty secret-bearing tables",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command == "backup":
            source, destination = resolve_backup_paths(
                settings, args.source, args.destination
            )
            if args.dry_run:
                with connect_read_only(source) as live:
                    result = integrity_result(live)
                if result != "ok":
                    raise RuntimeError(f"source integrity check failed: {result}")
                if destination.exists():
                    raise FileExistsError(
                        "destination already exists and will not be replaced: "
                        f"{destination}"
                    )
                print(f"source-sqlite-integrity={result}")
                print(f"planned-backup={destination.resolve()}")
                print("dry-run=true")
                return 0

            backup_database(source, destination)
            print(f"backup-created={destination.resolve()}")
            print("source-sqlite-integrity=ok")
            print("backup-sqlite-integrity=ok")
            permission = "0600" if os.name == "posix" else "current-user-and-system"
            print(f"destination-permissions={permission}")
            return 0

        live_database = args.live_database
        backup_path = args.backup_database
        if backup_path is None and live_database is not None and settings.database_path:
            backup_path = live_database
            live_database = settings.database_path
        live_database = live_database or settings.database_path
        if live_database is None:
            raise ValueError(
                "live database is required when database_path is not configured"
            )
        if backup_path is None:
            raise ValueError("pre-change backup path is required")
        require_secret_records = (
            settings.require_secret_records
            if args.require_secret_records is None
            else args.require_secret_records
        )
        report = compare_databases(
            live_database,
            backup_path,
            require_secret_records=require_secret_records,
        )
    except (
        OSError,
        RuntimeError,
        sqlite3.Error,
        tomllib.TOMLDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"operation-error: {exc}", file=sys.stderr)
        return 1

    print(f"live-sqlite-integrity={report.live_integrity}")
    print(f"backup-sqlite-integrity={report.backup_integrity}")
    print(f"safe-structure-unchanged={str(report.structure_matches).lower()}")
    print(f"secret-records-unchanged={str(report.secret_sets_match).lower()}")
    print(
        "required-secret-records-present="
        f"{str(report.required_secret_sets_present).lower()}"
    )
    print(f"template-environment-links={report.template_environment_links}")
    print(f"object-counts={json.dumps(report.object_counts, sort_keys=True)}")
    return 0 if report.matches else 2


if __name__ == "__main__":
    raise SystemExit(main())
