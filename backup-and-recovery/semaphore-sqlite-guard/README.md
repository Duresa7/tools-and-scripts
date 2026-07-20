# Semaphore SQLite guard

Create an online backup of a Semaphore SQLite database, verify both databases, and compare pre-change state without printing stored credentials or their hashes.

## Contents

- [Use case](#use-case)
- [Prerequisites](#prerequisites)
- [Guided setup](#guided-setup)
- [Manual setup](#manual-setup)
- [Inputs](#inputs)
- [Permissions](#permissions)
- [Dry run](#dry-run)
- [Changes made](#changes-made)
- [Safeguard reasoning](#safeguard-reasoning)
- [Rollback](#rollback)
- [Troubleshooting](#troubleshooting)
- [Exit behavior](#exit-behavior)

## Use case

Use `backup` before a Semaphore upgrade, migration, or database-affecting maintenance task. Use `compare` after the change to check SQLite integrity, non-secret project structure, template-to-environment links, and digests of encrypted secret-bearing records.

The tool runs on Linux, macOS, and Windows with Python 3.11 or newer. It uses SQLite's online backup API, so Semaphore can remain running while the backup is created. Stop Semaphore before replacing its live database during a restore.

## Prerequisites

- Python 3.11 or newer.
- Read access to the live database and its SQLite sidecar files.
- Write access to an existing backup directory.
- `whoami.exe` and `icacls.exe` on Windows.
- A fresh trial backup after a Semaphore upgrade because upstream table and column names can change.

## Guided setup

The configurator checks `SEMAPHORE_DB_PATH`, `/var/lib/semaphore/database.sqlite`, `$HOME/.semaphore/database.sqlite`, the current directory, and a Windows ProgramData path. It doesn't open the database or contact another system.

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/backup-and-recovery/semaphore-sqlite-guard"
python "$TOOL_DIR/configure.py" --database /var/lib/semaphore/database.sqlite
```

PowerShell:

```powershell
$ToolDir = Join-Path $HOME 'tools-and-scripts/backup-and-recovery/semaphore-sqlite-guard'
$DatabasePath = 'C:\ProgramData\Semaphore\database.sqlite'
py (Join-Path $ToolDir 'configure.py') --database $DatabasePath
```

The configurator writes ignored `config.local.toml` and refuses to replace an existing file.

## Manual setup

Bash:

```bash
TOOL_DIR="$HOME/tools-and-scripts/backup-and-recovery/semaphore-sqlite-guard"
CONFIG_PATH="$TOOL_DIR/config.local.toml"
cp "$TOOL_DIR/config.example.toml" "$CONFIG_PATH"
${EDITOR:-vi} "$CONFIG_PATH"
```

PowerShell:

```powershell
$ConfigPath = Join-Path $ToolDir 'config.local.toml'
Copy-Item (Join-Path $ToolDir 'config.example.toml') $ConfigPath
notepad $ConfigPath
```

Replace every `CUSTOMIZE:` value. Relative paths are resolved from the directory containing the TOML file.

## Inputs

`[semaphore]` contains four settings: `database_path`, `backup_directory`, `filename_template`, and `require_secret_records`. The filename template must contain `{timestamp}` once; a backup created at 14:05:09 UTC on 2026-07-20 uses `20260720T140509Z`.

Explicit paths keep the original interface and override local config:

```bash
DATABASE_PATH="/var/lib/semaphore/database.sqlite"
BACKUP_PATH="$HOME/semaphore-backups/pre-upgrade.sqlite"
python "$TOOL_DIR/semaphore_sqlite.py" backup "$DATABASE_PATH" "$BACKUP_PATH"
python "$TOOL_DIR/semaphore_sqlite.py" compare "$DATABASE_PATH" "$BACKUP_PATH"
```

With config, omit the source and generated destination:

```bash
python "$TOOL_DIR/semaphore_sqlite.py" --config "$CONFIG_PATH" backup
python "$TOOL_DIR/semaphore_sqlite.py" --config "$CONFIG_PATH" compare "$BACKUP_PATH"
```

Use `--require-secret-records` or `--allow-empty-secret-records` to override the comparison policy.

## Permissions

Run as an ordinary account that can read the database. Grant only the access needed for that file or run this command with elevation if the service account's database isn't readable. The backup directory should belong to the account running the command.

POSIX backups receive mode `0600`. Windows backups have inherited access removed and grant full control only to the current user SID and SYSTEM. A permission failure removes the empty or incomplete destination.

## Dry run

`backup --dry-run` checks source integrity, calculates the UTC timestamped destination, and rejects an existing destination without creating a file:

```bash
python "$TOOL_DIR/semaphore_sqlite.py" --config "$CONFIG_PATH" backup --dry-run
```

`compare` is read-only and can be run before maintenance to confirm a backup is readable.

## Changes made

`backup` creates one new file. It never replaces a destination or writes to the live database. `compare` changes no files. The configurator creates only the requested local TOML file.

## Safeguard reasoning

Exclusive file creation prevents an old backup from being overwritten. Permissions are restricted before SQLite writes any page into the destination. Source and destination integrity checks bracket the online backup. Comparisons select an allowlist of non-secret columns and use constant-time digest comparison for encrypted payloads; output includes booleans and record counts, not credential values or digests.

## Rollback

The live database is never modified, so backup failure needs no database rollback. The tool deletes a destination it created when backup, integrity, or permission handling fails. Keep a verified backup outside the live database directory before maintenance.

Restoring is a separate operation: stop Semaphore, preserve the failed live database, copy the selected backup into place with the service account's ownership and permissions, start Semaphore, and run application-level checks.

## Troubleshooting

- `destination already exists`: choose a new path or let the timestamp template generate one.
- `source database integrity check failed`: stop maintenance and diagnose the live SQLite file.
- `unsupported Semaphore schema`: test against the installed Semaphore version and update the safe-column map before relying on comparison.
- `icacls.exe could not restrict`: run under an account allowed to edit the destination ACL.
- `safe-structure-unchanged=false`: inspect the maintenance change before restoring or accepting it.

## Exit behavior

- `0`: dry run passed, backup and integrity checks passed, or comparison matched the selected policy.
- `1`: invalid config or path, existing destination, permission failure, SQLite error, integrity failure, or unsupported schema.
- `2`: comparison completed but structure, secret digests, integrity, or required-record policy did not match.
