# SSH public-key rotation

Audit, add, stage, verify, and retire SSH public keys across POSIX and Windows OpenSSH targets. Each identity carries an exact host allowlist; additive operations never delete unrelated keys.

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

Use this project to onboard a public key, stage a replacement beside it, prove the replacement exists, and retire the exact old key only after a login test. The removal match uses key algorithm and base64 material; a changed comment doesn't bypass or broaden the match.

Ansible controllers are Linux-based. Windows 11 and Windows Server 2025 are managed targets, not native controllers. See the [Ansible Windows guide](https://docs.ansible.com/projects/ansible/latest/os_guide/intro_windows.html).

## Prerequisites

- Linux, WSL, or a Linux VM for the controller.
- Ansible Core 2.17 or newer and the collections in `requirements.yml`.
- Python 3.11 and PyYAML for the configurator and local validator.
- Public keys only. Private keys stay on their owner devices and are never read by this project.
- Dedicated test targets before a first retirement run.

Install the pinned collections:

```bash
TOOL_DIR="$HOME/tools-and-scripts/identity-and-access/ssh-key-rotation"
cd "$TOOL_DIR"
ansible-galaxy collection install --requirements-file requirements.yml
```

## Guided setup

The configurator creates ignored `inventory/hosts.yml` and `identities/<identity-id>.yml` together. It reads one `.pub` file, computes its SHA256 fingerprint, refuses any private-key marker, and removes both outputs if the second installation fails.

```bash
HOST_ALIAS="server-one"
TARGET_ADDRESS="192.0.2.10"
CONNECTION_ACCOUNT="replace-connection-account"
MANAGED_ACCOUNT="replace-managed-account"
AUTHORIZED_KEYS_PATH="/home/$MANAGED_ACCOUNT/.ssh/authorized_keys"
PUBLIC_KEY_PATH="$HOME/.ssh/id_ed25519.pub"

python "$TOOL_DIR/configure.py" \
  --host-alias "$HOST_ALIAS" \
  --host "$TARGET_ADDRESS" \
  --connection-user "$CONNECTION_ACCOUNT" \
  --managed-owner "$MANAGED_ACCOUNT" \
  --authorized-keys-path "$AUTHORIZED_KEYS_PATH" \
  --platform posix \
  --become \
  --identity-id workstation-key \
  --display-name "Workstation key" \
  --public-key-file "$PUBLIC_KEY_PATH"
```

The tool performs local file reads and writes only. It doesn't contact a target or handle the private half of the key.

## Manual setup

Copy both annotated examples:

```bash
cd "$TOOL_DIR"
cp inventory/hosts.yml.example inventory/hosts.yml
cp identities/_identity-template.yml.example identities/workstation-key.yml
${EDITOR:-vi} inventory/hosts.yml
${EDITOR:-vi} identities/workstation-key.yml
python tests/validate_project.py --inventory hosts.yml
```

Replace every `CUSTOMIZE:` value. The copied inventory and identity files are ignored by Git. Public keys aren't authentication secrets, but their comments, device labels, fingerprints, and allowlists reveal system information.

## Inputs

Each supported host separates these values:

| Variable | Meaning |
|---|---|
| `ansible_user` | Account used for the SSH connection |
| `ssh_key_owner` | Account or Windows group whose key file is managed |
| `ssh_authorized_keys_path` | Exact file changed or inspected |
| `ssh_key_become` | Whether only the key-file task receives privilege escalation |
| `ssh_key_manage_directory` | Whether Ansible may create and manage the key file's parent directory |
| `ssh_key_shared_writer` | Writable inventory alias for a shared file |
| `ssh_key_windows_account_type` | `standard` or `administrator` ACL and path rules |

Keep unverified hosts under `ssh_key_unknown`; operational playbooks can't select that group. Shared key stores declare one writable alias and mark readers with `ssh_key_write_enabled: false` plus the writer alias.

An identity file contains its ID, display name, SHA256 fingerprint, complete public key, exact target allowlist, optional replacement public key, and owner-login verification flag. It never contains a private key.

## Permissions

Connect with an ordinary account. Set `ssh_key_become: true` only when the managed file belongs to another POSIX account or a Windows write needs `runas`. The POSIX tasks use `ssh_key_owner` and the explicit authorized-key path instead of assuming the connection account owns the key.

Windows standard-user files use the configured user's `C:\Users\<account>\.ssh\authorized_keys` path and an ACL limited to that account SID and SYSTEM. Administrator accounts use `C:\ProgramData\ssh\administrators_authorized_keys` with Administrators and SYSTEM. The PowerShell task removes inheritance and verifies the two-entry allow ACL after each write. See [Microsoft's OpenSSH key-management documentation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).

## Dry run

Audit is read-only:

```bash
ansible-playbook playbooks/ssh-key-audit.yml -e ssh_identity=workstation-key
```

Preview additive onboarding or replacement staging with Ansible check mode:

```bash
ansible-playbook playbooks/ssh-identity-onboard.yml --check \
  -e ssh_identity=workstation-key
ansible-playbook playbooks/ssh-key-stage.yml --check \
  -e ssh_identity=workstation-key
```

`ssh_target_group` or a JSON `ssh_target_hosts` list can narrow audit, onboarding, staging, and verification, but every selected host must remain inside the identity allowlist. Retirement always requires the full allowlist.

## Changes made

Onboarding adds the current public key without `exclusive: true`. Staging adds the replacement beside it. Retirement removes only the current key material after all gates pass.

```bash
ansible-playbook playbooks/ssh-identity-onboard.yml \
  -e ssh_identity=workstation-key
ansible-playbook playbooks/ssh-key-stage.yml \
  -e ssh_identity=workstation-key
ansible-playbook playbooks/ssh-key-verify.yml \
  -e ssh_identity=workstation-key
```

After testing replacement login from the owner device to every allowlisted target, set `rotation.operator_verified: true` and run:

```bash
ansible-playbook playbooks/ssh-key-retire.yml \
  -e ssh_identity=workstation-key \
  -e 'ssh_retire_confirmation=RETIRE workstation-key'
```

## Safeguard reasoning

Retirement opens only when a distinct replacement exists, both keys are present on every allowlisted target, the owner-login flag is true, the exact confirmation phrase matches, and the selected target list equals the full identity allowlist. An unreachable target never records a passed precheck, so the localhost gate stays closed before any removal task runs.

Shared readers can't write. An identity that includes a reader must also include its declared writer, which keeps verification and the single write in the same allowlist.

## Rollback

Before retirement, rollback is removing the staged replacement after confirming the current key still works. After retirement, use a surviving administrative credential or console to reinstall the recorded old public key on any target where replacement login fails.

After a successful rotation, promote `replacement_public_key` to `current_public_key`, update `fingerprint`, clear the replacement, set `operator_verified: false`, run the validator, and audit again.

## Troubleshooting

- `unsupported targets`: add the alias under `ssh_key_supported` and keep the identity allowlist exact.
- `shared writer is missing`: add the declared writer alias to both inventory and the identity target list.
- `elevated Windows writes require`: set `ssh_key_become: true`, `ansible_become_method: runas`, and a suitable `ansible_become_user`.
- `windows_acl_valid=false`: correct the managed owner or account type, then rerun onboarding or staging to enforce the ACL.
- `Retirement blocked by`: restore reachability or key presence on every listed host before retrying.

## Exit behavior

The validator exits `0` when local YAML, fingerprints, allowlists, platform fields, shared writers, and required files pass. It exits `1` and lists every detected error otherwise.

Ansible returns `0` only when every selected play and assertion succeeds. Connection, validation, ACL, key-presence, or retirement-gate failures return a nonzero status and keep later gated tasks from starting.
