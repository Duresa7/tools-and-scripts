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

The 2026-09-25 local run, after fifteen tools were added, completed `python check.py` with 38 passed check groups and 608 passed tests, with 1 Windows-only test skipped. Gitleaks scanned the complete Git history, including the new tool commits, and reported no leaks.

| Component | Version or result |
|---|---|
| Local workstation | Ubuntu 26.04.1 LTS, kernel 7.0.0-31-generic |
| Python | 3.14.4 |
| pytest | 9.1.1, 608 passed, 1 skipped |
| Ruff | 0.16.9 |
| ShellCheck | 0.11.0 |
| Ansible Core | 2.19.13 |
| Ansible Lint | 25.12.2, 19 files processed, 0 failures |
| PowerShell | 7.6.6, every `.ps1` file parsed and the PowerShell tool tests run |
| Node.js | 24.19.0, every `.mjs` file syntax-checked |
| Gitleaks | 8.30.1, complete history, no leaks found |

The same limit applies. None of the new tools has run against a live system for these checks.

A second run the same day, after seven more tools were added, completed `python check.py` with 49 passed check groups and 797 passed tests, with the same Windows-only test skipped. Ansible Lint passed all four Ansible projects with 0 failures, all 15 playbooks passed the syntax check, and Gitleaks again found no leaks in the complete history. The toolchain versions above are unchanged.

## Required targets

Each target must be dedicated to this validation. An active workload can't be used for a destructive or failure-path test.

| Target | Required use | State |
|---|---|---|
| Debian 13 | ifupdown cutover, Python tools, POSIX SSH target | Pending |
| Ubuntu 24.04 | Netplan cutover, Python tools, POSIX SSH target | Pending |
| Rocky 10 | Direct NetworkManager profile activation, DNF updates textfile, Python tools, POSIX SSH target | Pending |
| Windows 11 | Python tools, standard-user OpenSSH target, workstation bootstrap, recovery lockdown, session limits, online logon policy client check | Pending |
| Windows Server 2025 | Python tools, administrator OpenSSH target, domain controller for the online logon policy and session limits | Pending |
| Proxmox VE 9 | Proxmox subscription notice | Pending |
| Docker Engine with Compose v2 on Debian 13 | Compose service update | Pending |

Record the exact operating-system build, NetworkManager or Netplan version, Python version, SQLite version, TeamSpeak client and server versions, OpenSSH version, and Ansible version used for each result.

## Required scenarios

| Tool | Required live scenarios | State |
|---|---|---|
| NetworkManager cutover | Successful Debian and Ubuntu cutovers; deliberate post-activation assertion failure; automatic file and connection rollback; successful reboot validation; direct Rocky 10 profile validation | Pending |
| Prometheus target check | HTTP and HTTPS; private CA; bearer and basic auth; Linux and Windows clients; missing, duplicate, unexpected, forbidden, and unhealthy target results | Pending |
| Semaphore SQLite guard | Online backup while the application is running; busy database; source tamper or integrity failure; destination no-overwrite; comparison mismatch; POSIX mode `0600`; Windows current-user and SYSTEM ACL | Pending |
| TeamSpeak channel migration | ClientQuery export; disposable ServerQuery import; dry run; parent failure; existing-channel reuse; atomic export; credential-safe authentication failure | Pending |
| SSH key rotation | Onboarding, stage, verify, and retirement; ordinary connection account with elevation; POSIX standard account; Windows standard-user file; Windows administrator file; unreachable-target retirement gate | Pending |
| TCP reachability check | Open, closed, filtered, and unresolvable targets; expected-source guard; JSON and table output; PowerShell 7 on Linux and Windows; Windows PowerShell 5.1 | Pending |
| Grafana dashboard check | Layout findings on real exported dashboards; queries over HTTP and HTTPS; private CA; bearer and basic auth; allow-empty list; template variable substitution | Pending |
| DNF updates textfile | Up-to-date and pending states; security advisories; Obsoleting rows excluded; reboot flag both ways; DNF failure keeps the previous file; systemd timer run | Pending |
| Cloudflared tunnel health | Running and stopped connector; unreachable metrics endpoint reported as unknown; origin up and down; atomic write; systemd timer run | Pending |
| TeamSpeak voice probe | Local Init1 handshake; public SRV resolution; tunnel and server fault separation; textfile write; ServerQuery statistics; credential-safe authentication failure | Pending |
| Minecraft status probe | Current Java Edition server; SRV record; latency ping; offline, refused, and malformed responses | Pending |
| Windows online logon policy | GPO creation and reuse; confirmation gate and WhatIf; OU link; client application; offline sign-in and unlock rejected; local recovery account still works; rollback | Pending |
| Semaphore project reconciler | Read-only plan; gated create and update; repeat-run drift check; pruning; credential refresh; HTTPS and private CA; partial-apply recovery | Pending |
| UniFi flow collector | API-key and session login; HTTPS and private CA; CIM mapping; preview sends nothing; checkpoint restart; HEC rejection; systemd lifecycle | Pending |
| Windows workstation bootstrap | WhatIf preview; confirmation gate; OpenSSH install; key-only sign-in; password sign-in refused; firewall scope; rename and reboot | Pending |
| Windows recovery lockdown | Install and first run; startup and daily triggers; standard-user recovery attempt; feature-update re-enforcement; gated uninstall; deliberate WinRE repair and restore | Pending |
| Windows session limits | Task install and removal; overnight window; midnight reset; warnings; budget exhaustion; locked, disconnected, and simultaneous sessions; corrupt state; sleep and resume | Pending |
| Compose service update | Single and multi-file projects; file outside the allowed root refused; dry run; pull then recreate; previous-tag rollback | Pending |
| Proxmox subscription notice | Check, apply, and restore on the current toolkit; unsupported layout refused; package upgrade restores the stock file; proxy restart | Pending |
| Git preview server | Tracked files served; untracked, ignored, and dotfile paths refused; traversal refused; git failure fails closed; non-loopback bind needs the opt-in flag | Pending |
| Wazuh hash list refresh | Normal feed; empty or failed feed keeps the old list; atomic swap; manager restart and verification; list registered and matched by a rule; systemd timer run | Pending |
| Installer signature check | Signed installer from the expected publisher; look-alike publisher refused; unsigned file refused; hash match and mismatch; source-host guard | Pending |
| UniFi flow dashboards | Build and deploy the generated app; verify against a live Splunk with collector data; state-changing query refused; token never printed | Pending |
| Discord alert relay | Grafana alert with several alerts; Splunk alert from an allowed and a refused source; wrong secret refused; Discord rate limit; health before and after startup | Pending |
| Fleet updates | apt and dnf patching; report-only reboot flags; gated reboot one host at a time with boot-ID check; Compose update and health check | Pending |
| Monitoring exporters | node_exporter from APT and upstream binary; checksum mismatch refused; cAdvisor and WUD containers; textfile collectors; metric verification | Pending |
| Linux access baseline | Automation account with key-only login; creation-only password; NOPASSWD drop-in; rootpw refused until root's password is proven; console recovery | Pending |

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
- Each tool added after the first five states its own limits in its README.

## Release gate

The live compatibility gate is closed. No target may be marked supported until every required scenario for that target has a sanitized passing record above.

A failed scenario stays failed until its cause is fixed and the same scenario passes on a fresh dedicated target. Results from active services, partially reused targets, or tests that omit rollback don't satisfy this gate.
