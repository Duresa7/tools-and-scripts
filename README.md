# Tools and Scripts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Small utilities for DevOps, IT operations, security work, migrations, and self-hosted infrastructure. Each tool lives in its own folder with its documentation, examples, safeguards, and tests.

## Contents

- [Quick start](#quick-start)
- [Tool catalog](#tool-catalog)
- [Status and safety](#status-and-safety)
- [Compatibility](#compatibility)
- [Configuration rules](#configuration-rules)
- [Local checks](#local-checks)
- [Adding a tool](#adding-a-tool)
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

Run the complete repository check before editing or sharing a tool:

```bash
python check.py
```

## Tool catalog

Tools are grouped into five folders:

| Folder | What's in it |
|---|---|
| `monitoring/` | Health checks, probes, and metrics or log collectors |
| `windows/` | Windows workstation and domain tools |
| `linux/` | Linux server, container, and hypervisor tools |
| `automation/` | Ansible, Semaphore, backup, and migration tools |
| `utilities/` | Small cross-platform helpers |

The [`ai-configs/`](ai-configs/README.md) folder holds skills and guidance files for AI coding agents. It isn't a tool folder, so the tool rules and checks below don't apply to it.

### Monitoring

- [Prometheus target check](monitoring/prometheus-target-check/README.md): compare the active-target API with an expected set and reject missing, duplicate, unexpected, forbidden, or unhealthy targets.
- [Grafana dashboard check](monitoring/grafana-dashboard-check/README.md): find overlapping or out-of-grid panels offline, then run every visible PromQL expression and reject errors and unexpected empty results.
- [DNF updates textfile](monitoring/dnf-updates/README.md): export pending DNF updates by repository, pending security updates, and the reboot-required flag for the node_exporter textfile collector.
- [Cloudflared tunnel health](monitoring/cloudflared-tunnel-health/README.md): export the tunnel connection count and local origin HTTP health for the node_exporter textfile collector, keeping a stopped connector distinct from an unreachable metrics endpoint.
- [TeamSpeak voice probe](monitoring/teamspeak-voice-probe/README.md): send a real TeamSpeak 3 Init1 handshake to local and public voice endpoints and tell a server fault from a relay or DNS fault.
- [Minecraft status probe](monitoring/minecraft-status-probe/README.md): query a Java Edition server with the Server List Ping protocol and report version, players, and latency.
- [UniFi flow dashboards](monitoring/unifi-flow-dashboards/README.md): generate Splunk Dashboard Studio views for the UniFi flow collector's events and verify every panel query read-only against Splunk.
- [Discord alert relay](monitoring/discord-alert-relay/README.md): receive Grafana and Splunk alert webhooks and post them to a Discord channel as color-coded embeds.
- [UniFi flow collector](monitoring/unifi-flow-collector/README.md): poll UniFi traffic flows, map them to CIM field names, and preview or send them to Splunk HEC with a checkpoint.

### Windows

- [Windows online logon policy](windows/online-logon-policy/README.md): create a gated workstation GPO that requires online domain-password sign-in, then check the applied result on each workstation.
- [Windows workstation bootstrap](windows/workstation-bootstrap/README.md): rename a fresh Windows workstation and set up a key-only OpenSSH server behind a confirmation phrase.
- [Windows recovery lockdown](windows/recovery-lockdown/README.md): keep the Windows Recovery Environment disabled through a SYSTEM scheduled task so a standard user can't reset the machine from the recovery menu.
- [Windows session limits](windows/session-limits/README.md): enforce a daily sign-in window and usage budget for members of a directory group and sign them off when either runs out.
- [Installer signature check](windows/installer-signature-check/README.md): confirm a downloaded installer's Authenticode signature, exact publisher, and SHA-256 before anyone runs it.
- [Volume control panel](windows/volume-control-panel/volume-cp-open.bat): a batch file that opens the Windows sound control panel, handy for a mouse or keyboard shortcut.

### Linux

- [NetworkManager cutover](linux/networkmanager-cutover/README.md): move a Linux interface to a prepared NetworkManager profile, verify its exact address, route, and DNS state, and restore the prior network configuration after a failed cutover.
- [Proxmox subscription notice](linux/proxmox-subscription-notice/README.md): check, suppress, or restore the web UI subscription notice with an exact-count layout guard.
- [Wazuh hash list refresh](linux/wazuh-hash-list-refresh/README.md): rebuild the Wazuh manager's known-bad hash list from the MalwareBazaar recent feed, refuse an empty list, and swap it in atomically.
- [Compose service update](linux/compose-service-update/README.md): pull and recreate one Compose service from the files its running container was started with, refusing any file outside an allowed root.

### Automation

- [Fleet updates](automation/fleet-updates/README.md): patch Linux hosts through apt or dnf and update listed Compose projects, with report-only reboots by default and a gated, one-host-at-a-time reboot path.
- [Monitoring exporters](automation/monitoring-exporters/README.md): install and verify node_exporter, cAdvisor, textfile collectors, and What's Up Docker with pinned, checksum-verified versions.
- [Linux access baseline](automation/linux-access-baseline/README.md): create an automation account, manage account passwords, and roll out sudo policy with visudo validation and a root-password proof before `Defaults rootpw`.
- [SSH key rotation](automation/ssh-key-rotation/README.md): audit, stage, verify, and retire public keys across POSIX and Windows targets with an allowlist and a gated retirement step.
- [Semaphore project reconciler](automation/semaphore-project-reconciler/README.md): plan Semaphore project objects against reviewed manifests and apply the difference only behind an explicit gate.
- [Semaphore SQLite guard](automation/semaphore-sqlite-guard/README.md): create an online SQLite backup and compare Semaphore records without printing stored credentials.
- [TeamSpeak channel migration](automation/teamspeak-channel-migration/README.md): export a channel tree through ClientQuery and recreate it through ServerQuery with a dry-run import path.

### Utilities

- [TCP reachability check](utilities/tcp-reachability-check/README.md): probe a declared list of TCP endpoints from one host and fail when any target doesn't connect.
- [Git preview server](utilities/git-preview-server/README.md): serve only the tracked files of a git working tree on loopback for local HTML and SVG previews.

## Status and safety

"Locally checked" means automated tests and static checks cover the code path. It does not claim that a tool has passed the full operating-system matrix on dedicated machines.

| Tool | Platform | Runtime | Privilege | Changes state | Preview | Rollback | Tested status |
|---|---|---|---|---|---|---|---|
| Prometheus target check | Linux, macOS, Windows | Python 3.11+ | Ordinary user | No | Read-only command | Not applicable | Locally checked; live matrix pending |
| Grafana dashboard check | Linux, macOS, Windows | Python 3.11+ | Ordinary user | No | Read-only command; query preview | Not applicable | Locally checked; live matrix pending |
| DNF updates textfile | Linux with DNF | Bash 4+ | Write access to the textfile directory | One metrics file | Dry run | Disable the timer and remove the file | Locally checked; live matrix pending |
| Cloudflared tunnel health | Linux with systemd | Python 3.11+ | Write access to the textfile directory | One metrics file | Standard-output preview | Disable the timer and remove the file | Locally checked; live matrix pending |
| TeamSpeak voice probe | Linux, macOS, Windows | Python 3.11+ | Ordinary user | Metrics file when configured | Standard-output preview | Remove the metrics file | Locally checked; live matrix pending |
| Minecraft status probe | Linux, macOS, Windows | Python 3.11+ | Ordinary user | No | Read-only command | Not applicable | Locally checked; live matrix pending |
| UniFi flow collector | Linux, macOS, Windows; systemd example | Python 3.11+ | Ordinary user or service account | HEC events and a checkpoint file | Preview by default | Stop collection; sent events remain | Locally checked; live matrix pending |
| UniFi flow dashboards | Linux, macOS, Windows | Python 3.11+ | Ordinary user; Splunk search rights | Generated files only | Build output review; read-only verify | Delete generated files | Locally checked; live matrix pending |
| Discord alert relay | Linux container or host | Python 3.11+ | Ordinary user or container user | Discord messages | Not applicable | Messages stay posted | Locally checked; live matrix pending |
| Windows online logon policy | Windows domain | Windows PowerShell 5.1+ | GPO and OU link rights; elevated workstation check | GPO settings and OU link | WhatIf | Unlink or restore the GPO manually | Locally checked; live matrix pending |
| Windows workstation bootstrap | Windows 10 and 11 | Windows PowerShell 5.1 or PowerShell 7 | Elevation | Computer name, OpenSSH, firewall, sshd config | WhatIf | Documented manual steps before domain join | Locally checked; live matrix pending |
| Windows recovery lockdown | Windows 11 | Windows PowerShell 5.1 or PowerShell 7 | Elevation; SYSTEM task | Scheduled task, WinRE state, recovery policy | WhatIf and read-only check | Remove the task and re-enable WinRE | Locally checked; live matrix pending |
| Windows session limits | Domain-joined Windows | Windows PowerShell 5.1+ | Elevation to install; SYSTEM task | Scheduled task, usage state, session sign-out | Preview by default and WhatIf | Remove the task; a sign-out can't be undone | Locally checked; live matrix pending |
| Installer signature check | Windows | Windows PowerShell 5.1 or PowerShell 7 | Ordinary user | No | Read-only command | Not applicable | Locally checked; live matrix pending |
| NetworkManager cutover | Linux | Bash 4+ | Elevation for cutover | Network files, profiles, services | Yes | Automatic on failed validation | Locally checked; live matrix pending |
| Proxmox subscription notice | Proxmox VE, Proxmox Backup Server | Bash 4+ | Elevation for apply and restore | Web UI toolkit file, proxy service | Check mode | Restore mode or package reinstall | Locally checked; live matrix pending |
| Wazuh hash list refresh | Linux Wazuh manager | Bash 4+ | Elevation to install the list and restart | CDB list file, manager restart | Dry run | Previous list kept on any failure before the swap | Locally checked; live matrix pending |
| Compose service update | Linux | Bash 4+ with Docker Compose v2 | Docker socket access | Service image and container | Dry run | Rerun with the previous image tag or digest | Locally checked; live matrix pending |
| SSH key rotation | Linux controller; POSIX and Windows targets | Ansible Core 2.17+ | Per-target settings | Authorized-key files | Audit and check mode | Replacement is verified before retirement | Locally checked; live matrix pending |
| Fleet updates | Linux controller; Debian, Ubuntu, and RHEL-family targets | Ansible Core 2.17+ | become on targets | Packages, Compose containers, optional reboot | Check mode and scope preview | Package downgrade by hand; previous image tag | Locally checked; live matrix pending |
| Monitoring exporters | Linux controller; Linux targets | Ansible Core 2.17+ | become on targets | Exporter binaries, units, containers | Check mode and scope preview | Remove the unit and binary | Locally checked; live matrix pending |
| Linux access baseline | Linux controller; Linux targets | Ansible Core 2.17+ | become on targets | Accounts, passwords, sudoers drop-ins | Check mode | Console access to remove a drop-in | Locally checked; live matrix pending |
| Semaphore project reconciler | Linux, macOS, Windows | Python 3.11+ with PyYAML | Ordinary user; scoped API token | Semaphore projects and related objects | Plan by default | Restore from a Semaphore backup | Locally checked; live matrix pending |
| Semaphore SQLite guard | Linux, macOS, Windows | Python 3.11+ | Read access to database; write access to backup folder | Creates a backup file | Comparison is read-only | Original database is never replaced | Locally checked; live matrix pending |
| TeamSpeak channel migration | Linux, macOS, Windows | Python 3.11+ | Query accounts only | Export writes JSON; import creates channels | Import dry run | Keep the export and remove created channels manually | Locally checked; live matrix pending |
| TCP reachability check | Linux, macOS, Windows | PowerShell 7 or Windows PowerShell 5.1 | Ordinary user | No | Read-only command | Not applicable | Locally checked; live matrix pending |
| Git preview server | Linux, macOS, Windows | Node.js 20+ | Ordinary user | No | Not applicable | Not applicable | Locally checked; live matrix pending |

Every state-changing tool documents the exact confirmation, backup, and rollback behavior in its own README. Don't test network cutovers, key retirement, channel creation, sign-in policy, session sign-out, or recovery lockdown against an active system that can't tolerate interruption.

## Compatibility

[Compatibility validation](COMPATIBILITY.md) separates completed local checks from the pending Debian 13, Ubuntu 24.04, Rocky 10, Windows 11, and Windows Server 2025 live matrix. No live compatibility claim is complete until every required scenario has a sanitized passing record.

## Configuration rules

- Copy an annotated example to its ignored local filename before editing it.
- Search examples for `CUSTOMIZE:`. Each marker identifies a value owned by your environment.
- Keep credentials out of command history and configuration files. Tools read secrets from named environment variables or hidden prompts.
- Prefer an ordinary login account. Apply `sudo`, Ansible `become`, or Windows elevation only to the write that requires it.
- Command-line values override local configuration. Local configuration overrides documented defaults.

Examples use RFC 5737 addresses such as `192.0.2.10`; those addresses aren't live systems.

## Local checks

`python check.py` is the only complete verification command. It runs the Python tests and formatter checks, compiles Python files, parses Markdown links and example configurations, checks every command's help, validates Bash, PowerShell, and Node syntax, runs ShellCheck, validates and lints the Ansible project, checks every Ansible playbook's syntax, and scans the complete Git history with Gitleaks.

```bash
python -m pip install -r requirements-dev.txt
python check.py
```

The command doesn't skip a missing checker. It fails with the required installation step. On Windows, Bash, ShellCheck, Ansible Core, and Ansible Lint run inside WSL because Ansible controllers are Linux-based.

## Adding a tool

[Future tool template](TOOL-TEMPLATE.md) defines the folder layout, required README sections, configuration rules, safety metadata, comment policy, and minimum tests.

## License

[MIT](LICENSE)
