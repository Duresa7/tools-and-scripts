import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "semaphore_sqlite.py"
SPEC = importlib.util.spec_from_file_location("semaphore_sqlite_guard", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

backup_database = MODULE.backup_database
compare_databases = MODULE.compare_databases
parse_settings = MODULE.parse_settings
resolve_backup_paths = MODULE.resolve_backup_paths
timestamped_destination = MODULE.timestamped_destination

SCHEMA = """
CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT, type TEXT);
CREATE TABLE project__environment (
    id INTEGER PRIMARY KEY,
    project_id INTEGER,
    name TEXT,
    password TEXT,
    json TEXT,
    env TEXT
);
CREATE TABLE project__inventory (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE project__repository (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE project__template (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE project__view (id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE access_key (id INTEGER PRIMARY KEY, name TEXT, secret BLOB);
CREATE TABLE project__template_environment (
    template_id INTEGER,
    environment_id INTEGER
);
"""


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO project VALUES (1, 'Example', 'local')")
        connection.execute(
            "INSERT INTO project__environment "
            "VALUES (1, 1, 'Default', 'pw', '{}', '{}')"
        )
        connection.execute("INSERT INTO project__inventory VALUES (1, 'Hosts')")
        connection.execute("INSERT INTO project__repository VALUES (1, 'Git')")
        connection.execute("INSERT INTO project__template VALUES (1, 'Audit')")
        connection.execute("INSERT INTO project__view VALUES (1, 'Operations')")
        connection.execute("INSERT INTO access_key VALUES (1, 'SSH', ?)", (b"cipher",))
        connection.execute("INSERT INTO project__template_environment VALUES (1, 1)")


def test_backup_and_unchanged_comparison(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    backup = tmp_path / "backup.sqlite"
    create_database(live)

    backup_database(live, backup)
    report = compare_databases(live, backup, require_secret_records=True)

    assert backup.is_file()
    assert report.matches
    assert report.template_environment_links == 1


def test_structural_change_is_detected(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    backup = tmp_path / "backup.sqlite"
    create_database(live)
    backup_database(live, backup)

    with sqlite3.connect(live) as connection:
        connection.execute("UPDATE project SET name = 'Changed' WHERE id = 1")

    report = compare_databases(live, backup)

    assert not report.matches
    assert not report.structure_matches
    assert report.secret_sets_match


def test_secret_change_is_compared_without_exposing_value(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    backup = tmp_path / "backup.sqlite"
    create_database(live)
    backup_database(live, backup)

    with sqlite3.connect(live) as connection:
        connection.execute("UPDATE access_key SET secret = ? WHERE id = 1", (b"new",))

    report = compare_databases(live, backup)

    assert not report.matches
    assert report.structure_matches
    assert not report.secret_sets_match


def test_empty_table_schema_change_is_detected(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    backup = tmp_path / "backup.sqlite"
    create_database(live)
    backup_database(live, backup)

    with sqlite3.connect(live) as connection:
        connection.execute("ALTER TABLE project__inventory ADD COLUMN runner_tag TEXT")

    report = compare_databases(live, backup)

    assert not report.matches
    assert not report.structure_matches


def test_template_environment_link_change_is_detected(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    backup = tmp_path / "backup.sqlite"
    create_database(live)
    backup_database(live, backup)

    with sqlite3.connect(live) as connection:
        connection.execute("DELETE FROM project__template_environment")

    report = compare_databases(live, backup)

    assert not report.matches
    assert not report.structure_matches
    assert report.template_environment_links == 0


def test_explicit_backup_paths_override_local_settings(tmp_path: Path) -> None:
    settings = parse_settings(
        {
            "semaphore": {
                "database_path": "configured.sqlite",
                "backup_directory": "configured-backups",
            }
        },
        tmp_path,
    )
    explicit_source = tmp_path / "explicit.sqlite"
    explicit_destination = tmp_path / "explicit-backup.sqlite"

    source, destination = resolve_backup_paths(
        settings, explicit_source, explicit_destination
    )

    assert source == explicit_source
    assert destination == explicit_destination


def test_timestamped_destination_uses_utc_and_safe_template(tmp_path: Path) -> None:
    settings = MODULE.Settings(
        backup_directory=tmp_path,
        filename_template="before-upgrade-{timestamp}.sqlite",
    )

    destination = timestamped_destination(
        settings, datetime(2026, 7, 20, 14, 5, 9, tzinfo=UTC)
    )

    assert destination == tmp_path / "before-upgrade-20260720T140509Z.sqlite"


def test_invalid_filename_template_is_rejected() -> None:
    with pytest.raises(ValueError, match="filename_template"):
        parse_settings(
            {"semaphore": {"filename_template": "../backup-{timestamp}.sqlite"}}
        )


def test_permission_failure_removes_incomplete_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live.sqlite"
    destination = tmp_path / "backup.sqlite"
    create_database(source)

    def fail_permissions(_path: Path) -> str:
        raise RuntimeError("permission setup failed")

    monkeypatch.setattr(MODULE, "restrict_output_permissions", fail_permissions)

    with pytest.raises(RuntimeError, match="permission setup failed"):
        backup_database(source, destination)

    assert not destination.exists()


def test_configurator_writes_toml_without_credentials(tmp_path: Path) -> None:
    database = tmp_path / "database.sqlite"
    database.touch()
    output = tmp_path / "config.local.toml"
    command = [
        sys.executable,
        str(MODULE_PATH.with_name("configure.py")),
        "--database",
        str(database),
        "--backup-directory",
        str(tmp_path / "backups"),
        "--output",
        str(output),
    ]

    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    settings = MODULE.load_settings(output)
    assert settings.database_path == database
    serialized = output.read_text(encoding="utf-8").lower()
    assert "password" not in serialized
    assert "token" not in serialized


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL check")
def test_windows_backup_acl_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "live.sqlite"
    destination = tmp_path / "backup.sqlite"
    create_database(source)

    backup_database(source, destination)
    completed = subprocess.run(
        ["icacls.exe", str(destination), "/verify"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert MODULE.current_windows_user_sid().startswith("S-1-")
