# Linux access baseline

Create a dedicated automation account, manage account passwords, and apply guarded sudo policy on Linux hosts.

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

Use this project for three separate accounts: an existing connection account, a managed automation account, and an interactive operator. The connection account keeps its existing passwordless sudo grant as a recovery route. The managed automation account gets one public key and no sudo grant unless you explicitly select the optional NOPASSWD policy.

`automation-account.yml` applies an optional console password only when creating the account. Without that credential, a new account has a locked password and accepts its key. Existing passwords remain unchanged. SSH password and keyboard-interactive authentication must already be disabled. This project reads SSH policy and never edits or reloads sshd.

`account-passwords.yml` separately converges root's and the operator's passwords. `sudoers-rootpw.yml` makes sudo request root's password after proving it authenticates. `sudoers-nopasswd.yml` is a separately guarded alternative that grants passwordless access. NOPASSWD bypasses the password policy; it is not a prerequisite for rootpw.

## Prerequisites

- Linux controller, Python 3.11+, Ansible Core 2.17+, PyYAML and passlib. Install ansible-lint and pytest for local checks.
- Linux targets with Python, OpenSSH, shadow utilities, `su`, `runuser`, `sudo`, `visudo`, `getent`, `id` and `ssh-keygen`.
- A `sudo` or `wheel` group granting the operator administrative access. Existing operator accounts must already hold that membership.
- An ordinary connection account with working SSH keys and an existing NOPASSWD root grant. Local controller runs must also start as an ordinary account.
- A tested console or equivalent out-of-band recovery path. Keep a second root session open until verification finishes.
- A dedicated test target before rollout. Host keys must already be trusted; host-key checking stays enabled.

| Field | Behavior |
|---|---|
| Platform | Linux controller and Linux account targets |
| Runtime | Python 3.11+, Ansible Core 2.17+ |
| Privilege | Ordinary connection account; playbook-controlled elevation |
| State change | Accounts, password hashes, one authorized-key file, selected sudoers drop-ins |
| Preview | Explicit `--check`; normal runs require an exact confirmation phrase |
| Rollback | Manual recovery using a surviving administrative session or console |
| Exit codes | Python commands: 0 success, 1 validation/write failure, 2 argument error; Ansible: nonzero on failure |
| Tested status | Locally checked; live matrix pending |

All modules are built into Ansible Core. [requirements.yml](requirements.yml) has no external collections. The complete key file is written with `copy` and validated with `ssh-keygen` before replacement.

## Guided setup

Run these commands from this tool's directory. The configurator writes mode-0600 `inventory/hosts.yml` and `config.local.yml`. Both are ignored. It refuses existing files, including symlinks, and cleans up newly installed files if either installation fails. There is no overwrite option.

```bash
python configure.py --host-alias server-example --host 192.0.2.20 \
  --connection-user deploy
cp -n vars/automation-key.yml.example vars/automation-key.yml
chmod 600 vars/automation-key.yml
${EDITOR:-vi} config.local.yml inventory/hosts.yml vars/automation-key.yml
python tests/validate_project.py --inventory hosts.yml
```

Replace every `CUSTOMIZE:` value before running a playbook. The documentation address is not a live target. Set `--controller-hostname` only when configuring the local controller itself; the runtime hostname must match that value.

The key variables must contain one unrestricted `type data comment` public key, with no options prefix. Never supply a private key. The placeholder is rejected at runtime. See the [publication notice](vars/PUBLICATION-NOTICE.md).

## Manual setup

```bash
cp -n config.example.yml config.local.yml
cp -n inventory/hosts.yml.example inventory/hosts.yml
cp -n vars/automation-key.yml.example vars/automation-key.yml
chmod 600 config.local.yml inventory/hosts.yml vars/automation-key.yml
${EDITOR:-vi} config.local.yml inventory/hosts.yml vars/automation-key.yml
python tests/validate_project.py --inventory hosts.yml
```

The example includes a remote host and a guarded local controller. Remove the controller entry if you do not intend to manage the controller. Creation and key-only groups must be disjoint. Every optional operator NOPASSWD target must already belong to one of those groups. Group identifiers are part of the project interface; customize their membership.

## Inputs

| Input | Meaning |
|---|---|
| `config.local.yml` | Account names, homes, dedicated policy paths, public-key file and password environment-variable names |
| `inventory/hosts.yml` | Approved hosts, ordinary connection user and optional local-controller hostname |
| `automation_account_targets` | Create the managed account and install its key |
| `automation_account_key_only` | Install a key for an existing account; never create that account |
| `operator_nopasswd_targets` | Optional passwordless grants for selected operator accounts |
| `target` or Ansible `--limit` | Narrow the selected hosts; targets must remain in an approved account group |
| `baseline_config` | Absolute path to alternate non-secret settings; a missing file fails |
| `baseline_confirmation` | Exact phrase `APPLY ACCESS BASELINE` for every apply run |
| `sudoers_nopasswd_confirmation` | Additional exact phrase `GRANT PASSWORDLESS SUDO`, even for its preview |

For non-secret settings, explicit command-line values take precedence over local settings, then [config.example.yml](config.example.yml) defaults. The configurator merges `--config` with defaults and applies its account flags last. It does not automatically read a local settings file. Playbooks read `config.local.yml` when present and use documented defaults otherwise. Ansible `-e` overrides those settings. Use `-i` to override the default local inventory.

Password fields in config contain environment-variable **names**, never values. Defaults are `BASELINE_ROOT_PASSWORD`, `BASELINE_OPERATOR_PASSWORD`, and optional `BASELINE_AUTOMATION_PASSWORD`. For example, read credentials with hidden prompts in Bash:

```bash
read -r -s -p 'Root password: ' BASELINE_ROOT_PASSWORD; printf '\n'
read -r -s -p 'Operator password: ' BASELINE_OPERATOR_PASSWORD; printf '\n'
export BASELINE_ROOT_PASSWORD BASELINE_OPERATOR_PASSWORD
```

For a creation-only console password, similarly read and export `BASELINE_AUTOMATION_PASSWORD`. Leave it unset for a new locked-password account. Unset all three variables when finished. Never expand a password into an `ansible-playbook -e` argument.

Alternatively, create encrypted variables with `ansible-vault create vars/secrets.local.yml`. Use `vault_root_password`, `vault_operator_password`, and optional `vault_automation_account_password` inside the encrypted file. Pass `-e @vars/secrets.local.yml --ask-vault-pass`. Vault values take precedence over environment lookups. Do not create a plaintext staging file or put passwords in inventory. Rootpw requires distinct root and operator passwords so its negative authentication test is meaningful.

[Semaphore templates](semaphore/task-templates.yml) provide preview and apply jobs. Customize the repository path, inventory path and SSH credential. Inject passwords through protected environment settings using the configured variable names, or provide encrypted Vault variables. Templates contain no stored confirmation phrase or credential value.

## Permissions

Ansible elevates account and policy tasks to root. The preflight checks the actual connection UID without elevation and refuses UID 0. This matters because `su` from root can succeed without checking a password. Keep the connection account independent of both managed accounts.

Do not put `ansible_become*` or `ansible_sudo*` variables in inventory. Such connection variables can override task-level elevation controls and invalidate authentication proofs. The validator rejects them. The playbooks control elevation themselves.

## Dry run

Local validation and syntax checks make no host connection:

```bash
python tests/validate_project.py
ansible-lint --offline
for playbook in playbooks/*.yml; do
  ansible-playbook -i inventory/hosts.yml.example --syntax-check "$playbook"
done
python -m pytest -q tests
```

A check-mode run contacts the selected hosts, reads policy and predicts changes:

```bash
ansible-playbook playbooks/automation-account.yml --check --limit server-example
ansible-playbook playbooks/account-passwords.yml --check --limit server-example
ansible-playbook playbooks/sudoers-rootpw.yml --check --limit server-example
ansible-playbook playbooks/sudoers-nopasswd.yml --check --limit server-example \
  -e 'sudoers_nopasswd_confirmation=GRANT PASSWORDLESS SUDO'
```

The password plays require credentials even in check mode. Authentication proofs are skipped there. A missing new account also skips dependent key tasks with an explicit report. `--check` is not evidence that login, password authentication, or a changed sudo policy works.

## Changes made

After reviewing one-host previews and confirming console recovery, apply only the operation you intend:

```bash
ansible-playbook playbooks/automation-account.yml --limit server-example \
  -e 'baseline_confirmation=APPLY ACCESS BASELINE'
ansible-playbook playbooks/account-passwords.yml --limit server-example \
  -e 'baseline_confirmation=APPLY ACCESS BASELINE'
ansible-playbook playbooks/sudoers-rootpw.yml --limit server-example \
  -e 'baseline_confirmation=APPLY ACCESS BASELINE'
```

Account creation manages the automation account's shell and home, creates its mode-0700 `.ssh` directory, and replaces its mode-0600 `authorized_keys` with exactly one key. This removes other keys in that file. Existing automation passwords are neither reset nor locked. Key-only targets must already have the account.

The password play creates the operator if absent and chooses `sudo` or `wheel` from the group database. For existing operators it changes only the password. Root and operator passwords use `update_password: always`; fresh salts mean every apply reports changed. Both passwords must authenticate through `su`, and the connection account must retain passwordless sudo afterwards.

Rootpw writes the configured `00-` sudoers drop-in. Optional NOPASSWD writes two configured `90-` drop-ins according to group membership. Its apply requires both confirmation variables. It does not remove existing grants or automatically convert NOPASSWD accounts into password-gated accounts.

## Safeguard reasoning

Every play uses `serial: 1`, linear execution and `any_errors_fatal: true`. A failure stops the run before the next host. Every sudoers write uses `ansible.builtin.copy` with `validate: visudo -cf %s` and mode `0440`. The whole configuration is checked before and after policy changes.

Before rootpw is written, the play checks root's password status and proves the supplied password authenticates through `su` from the ordinary connection account. A present hash alone is insufficient. After writing, it submits passwords to `sudo -S` on stdin with `no_log`, never through a command string. It invalidates cached credentials before each sudo proof.

Policy is read as root using `sudo -l -U`, because an ordinary account's `sudo -l` may itself prompt after rootpw. The play asserts that resolved Defaults contain rootpw. If a NOPASSWD grant still works, the negative password proof reports **untestable**, not success. The connection account's passwordless recovery grant is checked before changes and rechecked after password and rootpw changes.

The controller hostname assertion applies to every local-connection target. Account creation uses `update_password: on_create` and reads back any existing hash to verify that a missing console credential did not lock it. SSH allowlists and effective password settings are checked, but a live login test is still required. The `sshd -T` check covers global defaults; review any `Match` blocks for the intended client separately.

## Rollback

There is no automatic host rollback. A later assertion can fail after a valid file or password was installed. Keep the second administrative session open and stop rollout until the first host is understood.

Before applying, retain the old authorized-key file and any existing managed sudoers drop-ins in protected storage outside this repository. Record whether each account and policy file already existed. Recover through the independent connection account while its NOPASSWD grant works. Otherwise use the physical, virtual-machine or other out-of-band console and obtain a root recovery shell.

For rootpw recovery, use that shell to remove the newly introduced `00-rootpw` file or restore its previous content with `visudo`. Use your configured filename if different. Run `visudo -c`, then test sudo from a separate ordinary session before closing recovery access. Removing rootpw returns sudo prompts to the invoking user's password unless another policy overrides it. Restore only this project's optional NOPASSWD files; preserve the connection account's recovery grant.

Password changes cannot be reversed from a hash generated by this run. Use console `passwd` prompts to set known replacement passwords, then test both login and sudo. Reinstall the previous authorized keys when reverting key replacement. Remove a newly created account only after checking ownership, running processes and recovery access; removing an account does not undo files it created.

## Troubleshooting

- Missing key or placeholder rejected: fill the ignored public-key variables file with one unrestricted public key.
- Wrong local controller hostname: correct inventory or use SSH to the intended host. Do not disable the assertion.
- Locked root password or failed root authentication: run the password play on that host, verify `root_auth=ok`, then retry rootpw.
- Missing credentials: export the configured variable names or supply encrypted Vault variables. Secret-handling assertions hide their details from logs.
- NOPASSWD reported as untestable: inspect effective policy as root. Removing grants requires a separately reviewed change and console recovery.
- SSH policy rejects the account: review allowlists, validate changes with `sshd -t`, and test another session before closing the first. Wildcard allowlists are treated conservatively.
- A post-write check fails: the change may already exist on the first host. Use the rollback path before retrying.

## Exit behavior

The configurator returns `0` after creating both files, `1` for invalid settings or a filesystem refusal, and `2` for invalid CLI arguments. The validator returns `0` on success, `1` for project or inventory failures, and `2` for invalid CLI arguments. Neither command contacts a host.

Ansible returns `0` when selected plays succeed and a nonzero status for syntax, connection, assertion, credential or task failures. Read the recap; a failure stops later hosts but does not undo completed tasks. An empty host selection may also exit successfully, so confirm the selected hosts with `--list-hosts` before applying. An `untestable` password proof is not a passed proof even when the play exits `0`.
