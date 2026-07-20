#!/usr/bin/env python3
"""Back up a Semaphore SQLite database and compare pre-change state."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
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

        with connect_read_only(source) as live:
            source_integrity = integrity_result(live)
            if source_integrity != "ok":
                raise RuntimeError(
                    f"source database integrity check failed: {source_integrity}"
                )
            with sqlite3.connect(destination) as backup:
                live.backup(backup)

        os.chmod(destination, 0o600)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser(
        "backup", help="Create an online backup and verify both databases"
    )
    # CUSTOMIZE: Database locations are positional inputs so deployments never
    # need to edit or hardcode a local Semaphore path in this script.
    backup.add_argument(
        "source", type=Path, help="path to your live Semaphore SQLite database"
    )
    backup.add_argument(
        "destination",
        type=Path,
        help="new backup file to create; the path must not already exist",
    )

    compare = subparsers.add_parser(
        "compare", help="Compare live state with a pre-change backup"
    )
    compare.add_argument(
        "live_database", type=Path, help="path to your current live database"
    )
    compare.add_argument(
        "backup_database", type=Path, help="path to the pre-change backup"
    )
    compare.add_argument(
        "--require-secret-records",
        action="store_true",
        help="Fail if either expected secret-bearing table has no rows",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            backup_database(args.source, args.destination)
            print(f"backup-created={args.destination.resolve()}")
            print("source-sqlite-integrity=ok")
            print("backup-sqlite-integrity=ok")
            print("destination-mode=0600")
            return 0

        report = compare_databases(
            args.live_database,
            args.backup_database,
            require_secret_records=args.require_secret_records,
        )
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
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
