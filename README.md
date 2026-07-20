# Tools and Scripts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Small utilities for DevOps, IT operations, security work, migrations, and self-hosted infrastructure. Each tool lives in its own folder with its documentation, examples, safeguards, and tests.

## Contents

- [Quick start](#quick-start)
- [Tool catalog](#tool-catalog)
- [Status and safety](#status-and-safety)
- [Configuration rules](#configuration-rules)
- [Local checks](#local-checks)
- [License](#license)

## Quick start

Clone the repository, choose one tool, and read that tool's README before running it:

```bash
git clone https://github.com/Duresa7/tools-and-scripts.git
cd tools-and-scripts
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

PowerShell uses a different activation command:

```powershell
git clone https://github.com/Duresa7/tools-and-scripts.git
Set-Location tools-and-scripts
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements-dev.txt
```

You don't need the whole repository to run one tool. Download its folder and keep the files together.

## Tool catalog

### Networking

- [NetworkManager cutover](networking/networkmanager-cutover/README.md): move a Linux interface to a prepared NetworkManager profile, verify its exact address, route, and DNS state, and restore the prior network configuration after a failed cutover.

### Monitoring

- [Prometheus target check](monitoring/prometheus-target-check/README.md): compare the active-target API with an expected set and reject missing, duplicate, unexpected, forbidden, or unhealthy targets.

### Backup and recovery

- [Semaphore SQLite guard](backup-and-recovery/semaphore-sqlite-guard/README.md): create an online SQLite backup and compare Semaphore records without printing stored credentials.

### Migrations

- [TeamSpeak channel migration](migrations/teamspeak-channel-migration/README.md): export a channel tree through ClientQuery and recreate it through ServerQuery with a dry-run import path.

### Identity and access

- [SSH key rotation](identity-and-access/ssh-key-rotation/README.md): audit, stage, verify, and retire public keys across POSIX and Windows targets with an allowlist and a gated retirement step.

## Status and safety

"Locally checked" means automated tests and static checks cover the code path. It does not claim that a tool has passed the full operating-system matrix on dedicated machines.

| Tool | Platform | Runtime | Privilege | Changes state | Preview | Rollback | Tested status |
|---|---|---|---|---|---|---|---|
| NetworkManager cutover | Linux | Bash 4+ | Elevation for cutover | Network files, profiles, services | Yes | Automatic on failed validation | Locally checked; live matrix pending |
| Prometheus target check | Linux, macOS, Windows | Python 3.11+ | Ordinary user | No | Read-only command | Not applicable | Locally checked; live matrix pending |
| Semaphore SQLite guard | Linux, macOS, Windows | Python 3.11+ | Read access to database; write access to backup folder | Creates a backup file | Comparison is read-only | Original database is never replaced | Locally checked; live matrix pending |
| TeamSpeak channel migration | Linux, macOS, Windows | Python 3.11+ | Query accounts only | Export writes JSON; import creates channels | Import dry run | Keep the export and remove created channels manually | Locally checked; live matrix pending |
| SSH key rotation | Linux controller; POSIX and Windows targets | Ansible Core 2.17+ | Per-target settings | Authorized-key files | Audit and check mode | Replacement is verified before retirement | Locally checked; live matrix pending |

Every state-changing tool documents the exact confirmation, backup, and rollback behavior in its own README. Don't test network cutovers, key retirement, or channel creation against an active system that can't tolerate interruption.

## Configuration rules

- Copy an annotated example to its ignored local filename before editing it.
- Search examples for `CUSTOMIZE:`. Each marker identifies a value owned by your environment.
- Keep credentials out of command history and configuration files. Tools read secrets from named environment variables or hidden prompts.
- Prefer an ordinary login account. Apply `sudo`, Ansible `become`, or Windows elevation only to the write that requires it.
- Command-line values override local configuration. Local configuration overrides documented defaults.

Examples use RFC 5737 addresses such as `192.0.2.10`; those addresses aren't live systems.

## Local checks

Development checks run locally. Install the development requirements, then run the Python suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
ruff check .
ruff format --check .
```

The repository will add one strict `python check.py` entry point after the tool-specific configuration work is complete. ShellCheck, Ansible validation, PowerShell parsing, and full-history secret scanning remain local requirements.

## License

[MIT](LICENSE)
