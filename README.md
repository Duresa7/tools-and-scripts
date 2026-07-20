# Tools and Scripts

Reusable utilities pulled from maintenance jobs and failure recovery. Each one accepts environment-neutral inputs, avoids command-line secrets, fails with useful exit codes, and runs under automated checks.

## Contents

- [Tools](#tools)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Safety](#safety)
- [Development checks](#development-checks)
- [License](#license)

## Tools

| Tool | Purpose | Runtime |
|---|---|---|
| [Prometheus target check](prometheus/README.md) | Compare the Prometheus active-target API with an expected target set and reject stale addresses or unhealthy scrapes | Python 3.11+ |
| [Semaphore SQLite guard](semaphore/README.md) | Create an online SQLite backup and compare secret-safe Semaphore structure before and after a change | Python 3.11+ |
| [TeamSpeak channel migration](teamspeak/README.md) | Export a channel tree through ClientQuery and recreate it through ServerQuery without putting credentials in command arguments | Python 3.11+ |

Each directory contains its own prerequisites, examples, exit behavior, and rollback notes. Example networks use the documentation ranges reserved by RFC 5737.

## Requirements

- Python 3.11 or newer for the Python tools.
- The Python unit tests run on Linux, macOS, and Windows.

The scripts use the Python standard library. The development requirements provide the test runner and formatter.

## Quick start

Clone the repository, create a virtual environment, and run the checks before adapting an example:

```bash
git clone https://github.com/Duresa7/tools-and-scripts.git
cd tools-and-scripts
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest
```

Windows PowerShell activation uses `.venv\Scripts\Activate.ps1`.

## Safety

- The repository contains no private keys, passwords, tokens, or live identity files.
- TeamSpeak credentials come from environment variables or a hidden terminal prompt. Query errors never include the command that carried a credential.
- TeamSpeak exports can contain private channel names, topics, and descriptions. Review the JSON file before sharing it.

## Development checks

The local test commands check Python formatting, lint rules, and unit tests.

```bash
ruff check .
ruff format --check .
pytest
```

## License

[MIT](LICENSE)
