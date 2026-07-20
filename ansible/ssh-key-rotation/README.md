# SSH public-key rotation workflow

This Ansible project audits, adds, stages, verifies, and retires SSH public keys on POSIX and Windows OpenSSH hosts. Each identity carries its own target allowlist. The playbooks never use `exclusive: true`, so onboarding and staging do not delete unrelated keys.

## Safety gates

Retirement runs only when all of these conditions pass:

1. The identity has a distinct replacement public key.
2. The replacement is present beside the current key on every selected host.
3. `rotation.operator_verified` is `true` after a login test from the key's owner device.
4. The command includes the exact phrase `RETIRE <identity-id>`.
5. Every selected host completes its precheck. An unreachable host keeps the removal gate closed.

The removal task matches the key algorithm and base64 material, not its comment. Shared key stores have one writable host and one or more verification-only hosts. An identity that targets a verification-only host must also target its declared writer.

## Prepare the project

Install the pinned collections:

```bash
cd ansible/ssh-key-rotation
ansible-galaxy collection install --requirements-file requirements.yml
```

Replace the documentation hosts in `inventory/hosts.yml` with your systems. Keep unverified systems under `ssh_key_unknown`; the playbooks cannot select that group.

Copy the identity template and add only public information:

```bash
cp identities/_identity-template.yml.example identities/admin-laptop.yml
ssh-keygen -lf ~/.ssh/id_ed25519.pub
python tests/validate_project.py
```

Identity YAML files are ignored by Git in this repository. Public keys are not passwords, but their comments, device names, fingerprints, and target allowlists reveal inventory details.

## Audit and onboard

Audit is read-only and reports presence without printing public-key material:

```bash
ansible-playbook playbooks/ssh-key-audit.yml \
  -e ssh_identity=admin-laptop
```

Preview additive onboarding, then run it:

```bash
ansible-playbook playbooks/ssh-identity-onboard.yml --check \
  -e ssh_identity=admin-laptop

ansible-playbook playbooks/ssh-identity-onboard.yml \
  -e ssh_identity=admin-laptop
```

Limit an operation to an allowlisted inventory group with `-e ssh_target_group=group-name`, or pass a JSON list through `ssh_target_hosts`. The loader rejects targets outside the identity's allowlist.

## Rotate an identity

1. Generate the replacement on the owner device.
2. Put its public key in `rotation.replacement_public_key` and leave `operator_verified: false`.
3. Stage the replacement.
4. Verify both keys, then test login from the owner device to every target.
5. Set `operator_verified: true` after those login tests pass.
6. Retire the old key with the confirmation phrase.

```bash
ansible-playbook playbooks/ssh-key-stage.yml \
  -e ssh_identity=admin-laptop

ansible-playbook playbooks/ssh-key-verify.yml \
  -e ssh_identity=admin-laptop

ansible-playbook playbooks/ssh-key-retire.yml \
  -e ssh_identity=admin-laptop \
  -e 'ssh_retire_confirmation=RETIRE admin-laptop'
```

After retirement, promote the replacement to `current_public_key`, update `fingerprint`, clear `replacement_public_key`, reset `operator_verified` to `false`, and run the validator and audit again.

## Partial failure recovery

The precheck prevents removal from starting when a selected host is already unreachable or missing either key. A host can still fail after the gate opens. If that happens, use a surviving administrative credential to reinstall the recorded old public key on any host where the replacement login also fails, then rerun the audit before continuing.
