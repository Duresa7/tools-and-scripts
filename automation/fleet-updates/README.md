# Fleet updates

Patch Linux guests and reconcile explicitly listed Docker Compose projects with reboot and container health checks.

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

Use a reviewed guest inventory to update apt or dnf packages and registry-backed Compose stacks. Leave hypervisors, Windows hosts, retired projects, and projects managed by another controller outside this inventory. No discovery automatically expands the update scope.

Tested status: Locally checked; live matrix pending. Linux controller and Linux targets only; no operating-system compatibility claim is made.

## Prerequisites

- Python 3.11+, PyYAML, Ansible Core 2.17+, and the collection in `requirements.yml`.
- Docker Engine and Compose plugin 2.18+ for container targets.
- Bash on RHEL-family targets; systemd for optional automatic reboots.
- Verified SSH host keys, an ordinary login account, and maintenance elevation.
- Tested backups and console access before a live maintenance window.

```bash
cd automation/fleet-updates
ansible-galaxy collection install -r requirements.yml -p .ansible/collections
```

## Guided setup

The configurator creates ignored `inventory/hosts.yml` with mode `0600`. It never replaces an existing file, including a symlink. It contacts no host. There is no overwrite option.

```bash
python configure.py
${EDITOR:-vi} inventory/hosts.yml
python tests/validate_project.py --inventory hosts.yml
```

Replace every `CUSTOMIZE:` value. To start from your own reviewed YAML, pass `--config config.local.yml`. The default source is `config.example.yml`, which matches `inventory/hosts.yml.example`. `--output` selects the destination file path (its parent directory must exist); keep that output private.

## Manual setup

```bash
cp -n inventory/hosts.yml.example inventory/hosts.yml
chmod 600 inventory/hosts.yml
${EDITOR:-vi} inventory/hosts.yml
python tests/validate_project.py --inventory hosts.yml
```

Find existing project names and configuration files on each intended Docker host with `docker compose ls --format json`. Copy its exact `Name` to `name`, the configuration directory to `project_src`, and the ordered file list to `files`. Retain deployed `profiles`. A project name can differ from its directory name. Review every entry; never include generated stacks owned by another deployment system.

The optional `semaphore/task-templates.yml` is a generic manifest example, not an installer. Customize its paths and existing credential reference. Its templates run previews only. Store credentials in Semaphore's credential facility, not this file.

## Inputs

Ansible extra variables (`-e`) override local inventory variables, which override playbook defaults. Use `-i` to select an explicit inventory. The default is `inventory/hosts.yml`; the validator defaults to the checked-in example and accepts `--inventory hosts.yml` for local validation.

| Input | Default and purpose |
|---|---|
| `target` | `os_update_targets` or `docker_compose_targets`; narrow with an alias or `--limit` |
| `fleet_confirmation` | Empty; live changes require exactly `UPDATE FLEET` |
| `reboot` | `report`; `auto` opts into reboots only when required; must be set with `-e` |
| `os_update_serial` | `2`; automatic reboot always forces `1` (pass `reboot=auto` with `-e`) |
| `os_update_become` | `true`; package operation elevation |
| `apt_upgrade` | `safe`; explicitly select `full` only after reviewing dependencies |
| `apt_autoremove`, `apt_autoclean` | `true`, `true` |
| `compose_serial` | `1` host at a time |
| `compose_pull` | `always`; a project's `pull` takes precedence |
| `compose_wait_timeout` | `180` seconds |
| `compose_pull_retries`, `compose_pull_retry_delay` | `3` retries, `10` seconds |
| `reboot_drop_timeout`, `reboot_timeout` | `120`, `600` seconds |
| `reboot_connect_timeout`, `reboot_poll_interval` | `10`, `5` seconds |
| `reboot_system_state_retries`, `reboot_system_state_delay` | `30` retries, `10` seconds |
| `local_expected_hostname` | Required exact OS hostname for any local connection |

Pass `reboot=auto` with `-e` rather than in inventory variables. The playbook computes `serial: 1` at play level from the `reboot` variable; inventory variables are evaluated per-host after play-level serial is already fixed. An early assertion stops execution before any changes if `reboot: auto` is encountered with more than one host in `ansible_play_batch`.

Each Compose entry needs a unique `name` and absolute `project_src`. Optional `files` stay inside that directory. Optional `profiles` are unique non-empty strings. `pull` accepts `always`, `missing`, `never`, or `policy`. Use `never` for locally built images or deliberate publication workflows. `remove_orphans` defaults to `false`.

## Permissions

Connect as an ordinary user and elevate for package changes and Compose access to protected environment files. Read-only reboot probes and SSH waits do not elevate. Fact gathering may require elevation under inventory policy. The project neither provisions accounts nor changes sudo rules.

Keep private keys outside the project. Use Ansible's hidden `--ask-pass` and `--ask-become-pass` prompts when passwords are required. Do not put passwords, tokens, or key contents in inventory or arguments. Registry authentication must already be available to the account running Compose.

## Dry run

Offline checks contact no host:

```bash
python tests/validate_project.py
ansible-lint --offline
ansible-playbook -i inventory/hosts.yml.example --syntax-check playbooks/os-update.yml
ansible-playbook -i inventory/hosts.yml.example --syntax-check playbooks/docker-compose-update.yml
```

After reviewing local inventory, preview remote work:

```bash
ansible-playbook playbooks/os-update.yml --check
ansible-playbook playbooks/docker-compose-update.yml --check
```

Check mode contacts targets. It does not install packages, pull images, recreate containers, or schedule reboots. The RHEL reboot probe still runs because it is read-only. Compose `pull: always` can report a predicted change without knowing whether a newer image exists. The complete container-state assertion also runs during preview against current state. A stopped or unhealthy project can fail a preview. Preview success does not prove health after a future update.

## Changes made

```bash
ansible-playbook playbooks/os-update.yml -e 'fleet_confirmation=UPDATE FLEET'
ansible-playbook playbooks/docker-compose-update.yml -e 'fleet_confirmation=UPDATE FLEET'
ansible-playbook playbooks/os-update.yml --limit web-01.example.net \
  -e 'fleet_confirmation=UPDATE FLEET' -e reboot=auto
```

The OS play refreshes package metadata, runs apt safe upgrades with autoremove and autoclean, or dnf `name: '*' state: latest`. It sets `DEBIAN_FRONTEND=noninteractive` and `NEEDRESTART_MODE=l`. The latter requests service listing from needrestart; package maintainer scripts can still restart services.

Compose pulls according to policy, then reconciles with `state: present` and waits for readiness. It recreates containers whose image or configuration changed. It does not rewrite Compose files or deliberately remove volumes. Image tags and version constraints remain those in the deployed files. Floating tags can introduce major changes; healthy containers do not prove application compatibility.

## Safeguard reasoning

Both plays reject live execution without the exact confirmation and reject targets outside their respective inventory group. Review the local allowlist before each window. The validator rejects empty groups, malformed projects, duplicate YAML keys, project duplicates, privileged login accounts, inline credentials, and unguarded local connections. It does not prove remote ownership or readiness.

Package runs use small serial batches. Automatic reboot uses one host at a time and refuses local connections. Because serial is computed at play level, `reboot=auto` must be passed with `-e`; an early assertion halts the play if `reboot_policy` is `auto` while `ansible_play_batch` contains more than one host. Debian uses `/var/run/reboot-required`. RHEL checks the standalone helper, dnf5, then dnf4. If none gives a conclusive result, a running-versus-installed kernel mismatch proves a pending reboot; matching or missing kernel evidence remains inconclusive.

The reboot path records the boot ID, schedules a transient systemd timer outside SSH, waits for SSH to drop, reconnects, and compares boot IDs. The drop wait is advisory because a fast reboot can escape sampling. The changed boot ID is the proof. It then waits for systemd to report `running` before clearing the pending state.

Compose pins each project name to avoid accidentally starting a second project under its directory name. Registry failures receive bounded retries. Live verification requires a non-empty complete `ps --all` result, every container running, and every configured health check healthy. Failure messages list affected names rather than dumping labels or commands.

## Rollback

There is no automatic rollback of package transactions, reboots, or container recreation. Take application-consistent backups and retain prior package versions and image digests before applying changes. Recover packages through the OS recovery process or restore a guest backup. Restore previous image references and application data when needed, then reconcile that project again. Database migrations may make an image-only rollback unsafe.

Delete the local inventory to undo configuration setup. Stop Semaphore schedules before recovery. A failed run can leave earlier hosts or projects updated; use the recap to inspect them before rerunning.

## Troubleshooting

- Missing collection: install `requirements.yml` into `.ansible/collections` from this folder.
- Consent failure: review scope, preview it, then supply the exact confirmation.
- Local identity failure: set `local_expected_hostname` to the runner's actual OS hostname or use SSH.
- Automatic reboot assertion: pass `reboot=auto` with `-e` so the play evaluates serial as 1 before starting.
- Inconclusive reboot check: inspect installed helpers and package state; this is not proof that no reboot is needed.
- Unchanged boot ID or systemd timeout: inspect the guest through console access before retrying.
- Compose health failure: inspect each named service, image, and deployed profile. One-shot or intentionally stopped services do not satisfy this project's all-running contract.
- Slow package run: inspect progress before interrupting a package manager. Reduce the report-mode batch size when storage is shared.

## Exit behavior

The configurator and validator exit `0` on success, `1` for invalid configuration or file errors, and `2` for invalid CLI arguments. Ansible returns `0` for successful selected plays; nonzero means a connection, syntax, consent, package, reboot, or health failure. An inconclusive reboot report alone does not fail the run. A failure does not undo earlier changes.
