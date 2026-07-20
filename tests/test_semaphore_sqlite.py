import sqlite3
from pathlib import Path

from semaphore.semaphore_sqlite import backup_database, compare_databases

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
