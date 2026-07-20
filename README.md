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
| [NetworkManager cutover](networkmanager/README.md) | Move a Debian interface from ifupdown to a prepared NetworkManager profile, verify address, route, and DNS, then restore the original file if a check fails | Bash, Debian networking tools |
| [Prometheus target check](prometheus/README.md) | Compare the Prometheus active-target API with an expected target set and reject stale addresses or unhealthy scrapes | Python 3.11+ |
| [Semaphore SQLite guard](semaphore/README.md) | Create an online SQLite backup and compare secret-safe Semaphore structure before and after a change | Python 3.11+ |
| [TeamSpeak channel migration](teamspeak/README.md) | Export a channel tree through ClientQuery and recreate it through ServerQuery without putting credentials in command arguments | Python 3.11+ |
| [SSH key rotation](ansible/ssh-key-rotation/README.md) | Audit, stage, verify, and retire SSH public keys across POSIX and Windows hosts with an allowlist and an explicit retirement gate | Ansible Core 2.17+ |

Each directory contains its own prerequisites, examples, exit behavior, and rollback notes. Example networks use the documentation ranges reserved by RFC 5737.

## Requirements

- Python 3.11 or newer for the Python tools.
- Bash 4 or newer plus Debian's ifupdown, NetworkManager, iproute2, and standard system tools for the NetworkManager cutover.
- Ansible Core 2.17 or newer plus the collections pinned in the SSH rotation project.
- The Python unit tests run on Linux, macOS, and Windows.

The scripts use the Python standard library. PyYAML is required only by the Ansible project validator.

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
- The NetworkManager script requires an explicit confirmation flag, keeps a timestamped copy of `/etc/network/interfaces`, and restores it when validation fails. Run it from a console or another out-of-band path because changing network ownership can interrupt SSH.
- The SSH retirement playbook requires a staged replacement, a recorded owner test, successful prechecks on every selected host, and an exact confirmation phrase.
- TeamSpeak exports can contain private channel names, topics, and descriptions. Review the JSON file before sharing it.

## Development checks

The local test commands check Python formatting, lint rules, and unit tests.

```bash
ruff check .
ruff format --check .
pytest
bash -n networkmanager/networkmanager-ifupdown-cutover.sh
python ansible/ssh-key-rotation/tests/validate_project.py
```

## License

[MIT](LICENSE)
