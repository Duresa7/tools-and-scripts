# Monitoring exporters

Install and verify node_exporter, cAdvisor, textfile collectors, and What's Up Docker on explicitly approved Linux hosts.

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

Keep host and container metrics consistent without mixing monitoring deployment with operating-system updates. Each playbook has its own inventory group and a separate reviewed allowlist. A target override can narrow that scope, but cannot bypass it.

Platform: Linux controller and Linux targets with systemd and APT or DNF. The upstream binary path is limited to x86_64. Tested status: **Locally checked; live matrix pending**. Local checks do not establish operating-system compatibility.

## Prerequisites

- Python 3.11+ and PyYAML for the local validator. The configurator uses only the standard library.
- Ansible Core 2.17+ on a Linux controller; use Python 3.11-3.13 with this project's locally checked Ansible 2.19 series.
- Target Python, Bash 4+, curl, tar, and systemd. Container targets need Docker Engine, Docker Compose v2.18+, and `ss` from iproute2.
- DNF targets also need RPM and the standard coreutils commands. Debian textfile targets need the distribution's `prometheus-node-exporter-collectors` package.
- Ordinary SSH login accounts with sudo access and verified host keys. Private keys stay in your SSH credential store. Use `--ask-become-pass` when sudo needs a password.
- Target access to package repositories and the documented upstream image/release registries.

`requirements.yml` intentionally declares no collections. Built-in modules invoke the target Docker Compose CLI and `firewall-cmd`. There are no imports from other tool folders.

## Guided setup

The configurator creates only `inventory/hosts.yml`, with mode `0600`. It publishes a complete file atomically and refuses an existing file or symlink. It has no overwrite option and contacts no host.

| Field | Behavior |
|---|---|
| Privilege | Ordinary controller account; target writes use become |
| State change | Packages, service account, binaries, units, metrics, Compose projects, optional firewall rules and scrape credentials |
| Preview | `--check` validates scope and stops before deployment |
| Rollback | Manual restoration; no automatic rollback of completed hosts |
| Exit codes | Setup/validator: 0 success, 1 failure, 2 invalid CLI; Ansible: nonzero on failure |

```bash
TOOL_DIR="$HOME/tools-and-scripts/automation/monitoring-exporters"
cd "$TOOL_DIR"
python configure.py
${EDITOR:-vi} inventory/hosts.yml
python tests/validate_project.py --inventory hosts.yml
```

Review every `CUSTOMIZE:` value before any remote command. The tool-local ignore rule covers `automation/monitoring-exporters/inventory/hosts.yml`. Shared catalog and check registration is a separate integration step.

`--source PATH` selects a reviewed template; `--output PATH` selects the destination file path (its parent directory must exist). Both override the documented example and output paths.

## Manual setup

Copy the annotated example without replacing a local file:

```bash
cd "$TOOL_DIR"
(set -C; umask 077; cat inventory/hosts.yml.example > inventory/hosts.yml)
${EDITOR:-vi} inventory/hosts.yml
python tests/validate_project.py --inventory hosts.yml
```

The Ansible-native example is `inventory/hosts.yml.example`; its local counterpart is `inventory/hosts.yml`. No second config file is required. Add or remove a host in both its group and `all.vars.monitoring_scope`. An empty group needs an empty allowlist. The validator rejects duplicates, mismatched scope, privileged login accounts, conflicting host variables, and embedded credential fields.

The optional `semaphore/task-templates.yml` describes eight templates and five views. Review the marked paths, inventory credential name, and locale before registering it. Every tracked template uses `--check`. A live local template must remove `--check` and supply the same confirmation as the command line. Nothing here creates Semaphore objects.

## Inputs

Ansible command-line extra variables take precedence over local inventory settings, which take precedence over documented defaults. Environment choices live in the `monitoring` mapping. For example, `monitoring.cadvisor_host_port` sets the inventory value and `-e cadvisor_host_port=9201` overrides it for one run. When overriding the mapping at host level, include every setting that host needs; Ansible does not merge nested dictionaries by default.

| Input | Default or meaning |
|---|---|
| `target` | Playbook's own `*_targets` group; `--limit` can further narrow it |
| `monitoring_scope` | Exact allowed host aliases for each of the four groups |
| `monitoring_confirmation` | Exact phrase `APPLY monitoring exporters`; never stored in the example |
| `node_exporter_version`, `node_exporter_apt_version` | `1.9.0`, `1.9.0-1+b4` |
| `node_exporter_port`, `node_exporter_listen_address`, `node_exporter_probe_host` | `9100`, `127.0.0.1:9100`, `127.0.0.1` |
| `node_exporter_binary`, `node_exporter_service_user` | `/usr/local/bin/node_exporter`, `node_exporter` |
| `node_exporter_unit_dir` | `/etc/systemd/system` |
| `node_exporter_arch` | `amd64`; other architectures are rejected |
| `node_exporter_manage_firewall` | `false`; opt-in adds the port to active and persistent firewalld rules |
| `allow_port_takeover` | `false`; explicit extra variable after inspecting an unmanaged listener |
| `textfile_dir`, `dnf_script_path`, `dnf_kernel_package` | `/var/lib/prometheus/node-exporter`, `/usr/local/sbin/dnf-updates-textfile`, `kernel` |
| `dnf_boot_delay`, `dnf_interval`, `dnf_random_delay` | `5min`, `15min`, `2min` |
| `hardware_collectors` | Derived from APT plus virtualization role `host`; override explicitly only after checking hardware |
| `cadvisor_image`, `wud_image` | `ghcr.io/google/cadvisor:v0.60.5`, `getwud/wud:9.0.2` |
| `cadvisor_host_port`, `wud_host_port` | `9101`, `9102` |
| `cadvisor_bind_address`, `wud_bind_address` | `127.0.0.1`; probes use loopback, so remote binds must include it |
| `cadvisor_project_dir`, `wud_project_dir` | Dedicated `/opt/docker/cadvisor`, `/opt/docker/wud`; final directory name is fixed |
| `cadvisor_state`, `wud_state` | `present`; `absent` removes the managed project |
| `cadvisor_disabled_metrics` | The example excludes costly metrics while retaining CPU, memory, network, and container presence |
| `wud_cron`, `wud_timezone` | Per-host five-field cron; example `0 6 * * *`; timezone `Etc/UTC` |
| `wud_admin_user`, `wud_password_env` | `metrics-admin`, `WUD_PASSWORD` (environment-variable name only) |
| `wud_prometheus_host` | Empty disables credential distribution; otherwise an existing inventory alias |
| `wud_credential_dir`, `wud_credential_owner`, `wud_credential_group` | `/srv/prometheus/wud-passwords`, numeric UID/GID `65534` |

Use a distinct password environment variable per WUD host. Supply at least 16 URL-safe characters (letters, digits, underscore, hyphen). Read it through a hidden prompt:

```bash
read -r -s -p 'WUD password: ' WUD_PASSWORD
export WUD_PASSWORD
```

Keep the same credential for repeat runs unless rotating it deliberately. The password is passed through the Compose process environment, not saved in the Compose file. Docker stores the container environment and Docker administrators can inspect it. Optional Prometheus distribution writes a protected password file on the selected host; it does not edit Prometheus scrape configuration. No secrets belong in inventory or command arguments.

## Permissions

Connect with `ansible_user` set to an ordinary account and use become for target administration. Account creation, package management, systemd writes, firewall changes, and Docker access need elevated rights. Read-only HTTP probes run without become. Ansible's general sudo access is administrative access, not a command allowlist.

The node_exporter unit runs under its system account with `NoNewPrivileges`, `ProtectHome`, `ProtectSystem=strict`, and `PrivateTmp`. cAdvisor needs privileged device and cgroup access. WUD mounts the Docker socket read-only and disables deletion in its interface; a read-only socket mount does not restrict Docker API privileges. Limit network exposure and who can administer these containers.

## Dry run

These commands contact no target:

```bash
python tests/validate_project.py --inventory hosts.yml.example
ansible-lint --offline
for playbook in playbooks/*.yml; do
  ansible-playbook -i inventory/hosts.yml.example --syntax-check "$playbook"
done
```

A remote preview gathers facts, validates the allowlist and project settings, then stops each host before downloads, writes, restarts, or health probes:

```bash
ansible-playbook playbooks/node-exporter.yml --check --limit linux-example
ansible-playbook playbooks/cadvisor.yml --check
ansible-playbook playbooks/textfile-collectors.yml --check
ansible-playbook playbooks/wud.yml --check
```

This is a scope preview, not a simulation of package changes or a health check. Download-dependent tasks and skipped probes cannot establish a working deployment in check mode. Run syntax checks against the example inventory and review the Inputs table to inspect planned settings offline.

## Changes made

Run one reviewed scope at a time:

```bash
ansible-playbook playbooks/node-exporter.yml \
  -e 'monitoring_confirmation="APPLY monitoring exporters"'
ansible-playbook playbooks/cadvisor.yml \
  -e 'monitoring_confirmation="APPLY monitoring exporters"'
ansible-playbook playbooks/textfile-collectors.yml \
  -e 'monitoring_confirmation="APPLY monitoring exporters"'
ansible-playbook playbooks/wud.yml \
  -e 'monitoring_confirmation="APPLY monitoring exporters"'
unset WUD_PASSWORD
```

Node exporter uses the exact APT candidate when available. Otherwise it creates a root-owned temporary staging directory with mode 0700, downloads the pinned upstream archive into it, verifies it against that release's SHA256 manifest, unarchives without creates, copies the binary, and removes the staging directory in an always block. It installs a hardened unit and verifies the running version. Both paths honor listener settings. It refuses a competing package/upstream installation rather than silently switching service owners. The APT listener task replaces the package's `ARGS` setting; review existing custom flags first.

cAdvisor and WUD use dedicated Compose directories. They pull missing pinned images, reconcile containers, and detect create/start/recreate/remove events. An unchanged container is not restarted. Node exporter restarts are handlers notified only by binary, unit, package, or configuration changes. Service starts and first collector runs still occur as needed.

Textfile collectors require the upstream unit. APT installs collectors without Recommends; SMART and NVMe timers run only on APT bare-metal hosts. The packaged APT collector uses its standard textfile directory, so changing `textfile_dir` on APT is rejected. DNF gets the bundled script and a systemd timer. The systemd drop-in replaces ExecStart with the configured binary, listener, and textfile flag; retain any additional custom flags manually before adopting it.

The copy under `playbooks/files/` keeps this project self-contained. `monitoring/dnf-updates` is the standalone version. Both export pending upgrades by repository, security updates, a reboot flag, and a timestamp through an atomic rename. DNF failures preserve the last good file.

## Safeguard reasoning

Scope must match the separate allowlist before any mutation. Every live play requires the exact confirmation phrase, including removal. Hosts run serially. Exporter listener checks stop conflicts. Packages use `install_recommends: false` to avoid unrelated hardware services. The binary download checksum prevents installing a damaged release archive, and using a private temporary directory removed in an always block prevents symlink or pre-existing binary substitution attacks.

Verification checks actual exported metrics. Node exporter must report the pinned version. cAdvisor must report named containers when Docker has running containers. WUD must return authenticated container metrics; its count may legitimately be lower than Docker's because some images are not watchable. Textfile checks require a clean scrape, a timestamp, and a reboot metric. Fully patched hosts need not expose pending APT rows. Hardware targets additionally need SMART and NVMe rows.

WUD password handling uses `no_log`. Optional scrape passwords use `0600` files inside a `0700` directory. Host keys remain checked. No SSH, service, package, or container command runs during the local test suite.

## Rollback

There is no automatic rollback. A failed health assertion stops that host after any completed changes; earlier hosts can already have changed. Preserve existing service units, exporter flags, Compose definitions, and previous image references outside this folder before adopting a host.

Restore prior node_exporter packages/binaries and unit files, reload systemd, and restart the prior service. Disable collector timers and restore or remove `node_exporter.service.d/textfile.conf` before restarting. Remove only this project's metrics files and timer units. Remove any firewall rule you enabled if it is no longer needed.

Remove a reviewed Compose project with the same live gate:

```bash
ansible-playbook playbooks/cadvisor.yml --limit docker-example \
  -e cadvisor_state=absent -e 'monitoring_confirmation="APPLY monitoring exporters"'
ansible-playbook playbooks/wud.yml --limit docker-example \
  -e wud_state=absent -e 'monitoring_confirmation="APPLY monitoring exporters"'
```

Removal deletes the dedicated project directory and container. WUD also deletes its store volume; that history cannot be restored without your own backup. Optional Prometheus password files remain for manual removal, and scrape configuration must be updated separately. To revert an image upgrade, select the previous reviewed pin and reconcile with the gate.

## Troubleshooting

- Scope mismatch: update group membership and the reviewed allowlist together. Empty groups are valid, but a mistyped `--limit` can select no hosts; check the Ansible recap.
- Competing node exporter installation: choose one service owner and migrate it deliberately before rerunning.
- Unmanaged listener: inspect the owner. `allow_port_takeover=true` does not stop a foreign process and should not be used to hide a conflict.
- cAdvisor reports no containers: inspect its logs and Docker storage driver. The documented v0.60.5 pin handles the containerd snapshotter that older releases failed to register.
- Textfile guard fails: confirm this is a binary-managed host, the timer ran, and the exporter can read the directory. SMART/NVMe checks require real compatible devices.
- WUD authentication fails: set the named environment variable in the controller process and keep it stable. The credential must use the documented character set. Review image-specific registry support and stagger schedules.
- Compose update reports no change: a cached pinned image is reused. Choose a new reviewed tag or digest for an upgrade.

## Exit behavior

Configurator and validator return `0` on success, `1` on input or filesystem failure, and `2` for invalid command-line syntax. The validator never contacts a host.

Ansible returns `0` when selected tasks succeed. Common nonzero statuses are `2` for failed tasks, `4` for unreachable hosts or parser errors, and `1` for other controller errors; all nonzero statuses require review. An empty host selection can return `0`, so confirm the recap contains the expected targets. Assertion failure prevents subsequent tasks for that host but does not undo completed changes.

The bundled DNF script returns `0` for a successful write or `--dry-run`, `1` for invalid input or missing commands, `2` for a DNF query failure, and `3` for a write failure. Its `--help` lists all options.
