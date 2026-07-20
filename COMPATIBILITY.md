# Compatibility validation

This repository has passed local automated checks. It has not passed the required live operating-system matrix. Every live result below remains `Pending`, so publication does not constitute an operating-system compatibility claim.

## Contents

- [Local verification](#local-verification)
- [Required targets](#required-targets)
- [Required scenarios](#required-scenarios)
- [Evidence record](#evidence-record)
- [Support limits](#support-limits)
- [Release gate](#release-gate)

## Local verification

The 2026-07-20 local run completed `python check.py` with 21 passed check groups and 59 passed tests. Gitleaks 8.30.1 scanned the complete Git history and reported no leaks.

| Component | Version or result |
|---|---|
| Local workstation | Windows 11 Pro Insider Preview 10.0.26220, build 26220 |
| Python | 3.14.4 |
| pytest | 9.1.1, 59 passed |
| Ruff | 0.15.22 |
| ShellCheck in WSL | 0.8.0 |
| Ansible Core in WSL | 2.17.14 |
| Ansible Lint in WSL | 25.12.2, 20 files processed, 0 failures |
| PowerShell parser | PowerShell 7.5.8, 3 source files and 2 assembled task scripts parsed |
| Gitleaks | 8.30.1, complete history, no leaks found |

This proves local parsing, unit behavior, static analysis, CLI help, example configuration, playbook syntax, and secret scanning. It does not prove that a real network cutover rolls back, a busy live SQLite database backs up, a TeamSpeak server accepts the hierarchy, or a remote host remains reachable after key retirement.

## Required targets

Each target must be dedicated to this validation. An active workload can't be used for a destructive or failure-path test.

| Target | Required use | State |
|---|---|---|
| Debian 13 | ifupdown cutover, Python tools, POSIX SSH target | Pending |
| Ubuntu 24.04 | Netplan cutover, Python tools, POSIX SSH target | Pending |
| Rocky 10 | Direct NetworkManager profile activation, Python tools, POSIX SSH target | Pending |
| Windows 11 | Python tools and standard-user OpenSSH target | Pending |
| Windows Server 2025 | Python tools and administrator OpenSSH target | Pending |

Record the exact operating-system build, NetworkManager or Netplan version, Python version, SQLite version, TeamSpeak client and server versions, OpenSSH version, and Ansible version used for each result.

## Required scenarios

| Tool | Required live scenarios | State |
|---|---|---|
| NetworkManager cutover | Successful Debian and Ubuntu cutovers; deliberate post-activation assertion failure; automatic file and connection rollback; successful reboot validation; direct Rocky 10 profile validation | Pending |
| Prometheus target check | HTTP and HTTPS; private CA; bearer and basic auth; Linux and Windows clients; missing, duplicate, unexpected, forbidden, and unhealthy target results | Pending |
| Semaphore SQLite guard | Online backup while the application is running; busy database; source tamper or integrity failure; destination no-overwrite; comparison mismatch; POSIX mode `0600`; Windows current-user and SYSTEM ACL | Pending |
| TeamSpeak channel migration | ClientQuery export; disposable ServerQuery import; dry run; parent failure; existing-channel reuse; atomic export; credential-safe authentication failure | Pending |
| SSH key rotation | Onboarding, stage, verify, and retirement; ordinary connection account with elevation; POSIX standard account; Windows standard-user file; Windows administrator file; unreachable-target retirement gate | Pending |

No scenario passes because a command was issued. A pass requires the observed state, expected state, exit code, versions, rollback result when applicable, and a sanitized evidence note.

## Evidence record

Add one row per completed scenario. Don't record private addresses, hostnames, account names, public-key comments, channel names, credentials, or database content.

| Date | Target | Tool and scenario | Versions | Expected result | Observed result | Exit code | Rollback or cleanup | Result |
|---|---|---|---|---|---|---|---|---|
| Pending | Pending | Pending | Pending | Pending | Pending | Pending | Pending | Pending |

Use documentation addresses such as `192.0.2.10` when a sanitized note needs an example address. Store raw evidence outside the public repository; retain only the minimum sanitized result needed to support a compatibility claim.

## Support limits

- NetworkManager cutover validates one interface, one global IPv4 address, the complete normalized default-route set, an exact DNS server set, and one existing profile. It doesn't create a production-ready profile or test IPv6, bonds, bridges, or VLAN topology.
- Prometheus target check reads `/api/v1/targets`. It doesn't edit Prometheus, follow redirects, or validate alerting rules and recording rules.
- Semaphore SQLite guard supports the table families named in its safe-column map. It creates and compares backups; it doesn't stop Semaphore or restore a database.
- TeamSpeak migration copies channel hierarchy and listed channel properties. It doesn't copy channel passwords or icon files and doesn't automatically delete a partial import.
- SSH rotation runs from a Linux Ansible controller, manages supported OpenSSH public-key formats, and never handles a private key. Windows support is limited to managed targets reached through Ansible's SSH connection.

## Release gate

The live compatibility gate is closed. No target may be marked supported until every required scenario for that target has a sanitized passing record above.

A failed scenario stays failed until its cause is fixed and the same scenario passes on a fresh dedicated target. Results from active services, partially reused targets, or tests that omit rollback don't satisfy this gate.
