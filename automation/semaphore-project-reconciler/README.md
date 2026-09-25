# Semaphore project reconciler

Compare Semaphore project objects with reviewed manifests, then apply selected changes through its API.

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

Keep project credentials, repositories, file inventories, variable groups, views, and Ansible task templates aligned with versioned manifests. The default command reads the API and prints a plan. It retains unmanaged objects unless you explicitly request template and view pruning.

The client targets the Semaphore 2.18 project API. It doesn't run playbooks or create schedules. A matching template definition doesn't prove that its playbook works or that Ansible check mode is safe for it.

## Prerequisites

- Python 3.11 or newer. JSON manifests use only the standard library. YAML manifests need PyYAML.
- A reachable Semaphore API and a token with access to the selected projects.
- Repository and inventory paths that exist on the Semaphore execution host, or a reachable Git repository URL.
- An SSH private key in a named environment variable when creating a project credential or explicitly refreshing it.

| Field | Value |
|---|---|
| Platform | Python command-line runtime; live operating-system validation pending |
| Privilege | Ordinary local user; Semaphore account with permissions for the requested operations |
| State change | API objects; optional token revocation; configurator writes one local TOML file |
| Preview | Default read-only plan; offline `--check-manifests` |
| Rollback | No automatic rollback; restore a verified backup or correct individual objects |
| Exit codes | 0 success; 1 failure; 2 planned drift or command-line usage error |
| Tested status | Locally checked; live matrix pending |

## Guided setup

Run these commands from this tool's directory. The configurator makes no network requests and refuses to replace an existing file. It has no overwrite option; edit the existing file or choose a new `--output` path.

```bash
python -m pip install PyYAML
python configure.py --url https://semaphore.example.net
cp manifest.yml.example manifest.local.yml
${EDITOR:-vi} config.local.toml manifest.local.yml
python reconcile_semaphore.py --config config.local.toml --check-manifests
```

Replace every `CUSTOMIZE:` value. The manifest's playbook paths are examples; provide your own playbooks. Keep local manifests outside version control because they describe your environment.

## Manual setup

```bash
cp config.example.toml config.local.toml
cp manifest.yml.example manifest.local.yml
${EDITOR:-vi} config.local.toml manifest.local.yml
```

Set the API token through a hidden prompt in Bash. The config stores only its variable name:

```bash
read -r -s -p 'Semaphore API token: ' SEMAPHORE_API_TOKEN
export SEMAPHORE_API_TOKEN
python reconcile_semaphore.py --config config.local.toml
unset SEMAPHORE_API_TOKEN
```

Use your platform's secret manager to populate `SEMAPHORE_SSH_PRIVATE_KEY` with the complete multiline private key before an apply that needs it. Do not place token or key values in manifests, TOML files, command arguments, or shell history. Existing credentials are reused without reading a key during planning.

## Inputs

Command-line values override explicit `--config` values, which override defaults. Local configuration is never loaded implicitly. Config-relative manifest and CA paths resolve from the config directory; command-line paths resolve from the current directory. Positional manifest paths replace the configured list.

| Setting | Default and purpose |
|---|---|
| `semaphore.url`, `--url` | `http://127.0.0.1:3000`; base URL without `/api` |
| `semaphore.token_env`, `--token-env` | `SEMAPHORE_API_TOKEN`; token environment-variable name |
| `semaphore.timeout_seconds`, `--timeout` | 30 seconds per request |
| `semaphore.ca_file`, `--ca-file` | Empty; system trust store. `--no-ca-file` clears a configured CA |
| `semaphore.allow_insecure_http`, `--allow-insecure-http` | False; explicit permission for plain HTTP beyond loopback |
| `credential.login`, `--credential-login` | `ansible`; ordinary SSH login stored with the credential |
| `credential.private_key_env`, `--private-key-env` | `SEMAPHORE_SSH_PRIVATE_KEY`; private-key environment-variable name |
| `manifests.paths`, positional paths | No implicit runtime manifest; configurator seeds `manifest.local.yml` |

Each manifest describes one project with one managed repository, inventory, and variable group. Names identify existing objects. Renaming an object in a manifest can create a new object and leave the old one unmanaged. Views must start with `All`; view and template names must be unique. Template views must exist in the same manifest. Arguments are string lists. Environment variables map names to strings; extra variables must be JSON-compatible mappings. Never include secrets in either mapping or template arguments.

The optional `--expire-token` flag revokes the current token after reconciliation, including after a failure. This is an explicit state change even in plan mode. It doesn't run with `--check-manifests` or when setup fails before the API client is created.

## Permissions

No local elevation is required. The account needs read access to configuration, manifests, and any CA file. Planning needs API read access; apply needs permission to create and update selected project objects. Pruning needs delete permission. Limit token scope and lifetime to the intended work.

An inventory login does not grant host elevation. Configure that separately in your Ansible project. This tool never connects to inventory hosts itself.

## Dry run

Validate files without a token or network access:

```bash
python reconcile_semaphore.py --config config.local.toml --check-manifests
```

With the named token variable set, inspect remote drift:

```bash
python reconcile_semaphore.py --config config.local.toml
python reconcile_semaphore.py --config config.local.toml --prune
```

Both commands leave remote state unchanged. Planned changes return status 2. Extra retained objects alone don't count as drift. For a missing project, the plan reports creation and a template count; dependent objects are populated during apply.

## Changes made

Before applying, take a verified backup. For SQLite deployments, follow [Semaphore SQLite guard](../../backup-and-recovery/semaphore-sqlite-guard/README.md). Preserve the matching Semaphore configuration and encryption settings with your recovery set.

After reviewing the plan and preparing any needed private-key variable:

```bash
python reconcile_semaphore.py --config config.local.toml --apply
python reconcile_semaphore.py --config config.local.toml
```

The second command verifies the resulting managed fields by reading them again. Apply itself reports completed writes; it doesn't provide an atomic transaction or automatic final readback.

Deleting unlisted templates and views requires both flags and the exact phrase:

```bash
python reconcile_semaphore.py --config config.local.toml --apply --prune \
  --confirm DELETE_UNLISTED_SEMAPHORE_OBJECTS
```

Replacing stored SSH keys needs a separate confirmation:

```bash
python reconcile_semaphore.py --config config.local.toml --apply --refresh-credential \
  --confirm REPLACE_SEMAPHORE_SSH_CREDENTIALS
```

Supply both confirmations when combining the operations. Pruning never deletes projects, repositories, inventories, credentials, or variable groups. Template deletion may also remove related task history in Semaphore. The configurator creates only its requested local file.

## Safeguard reasoning

`--apply` is the explicit write gate. Extra phrases protect deletion and credential replacement. All manifests are validated before reconciliation starts. Unknown keys fail validation so a misspelled argument field cannot silently remove `--check` from a template.

Without a supplied private key, apply first checks every selected project for missing credentials and stops before writing if a key is needed. Missing `All` views and unexpected collection shapes fail instead of inventing a replacement layout. Unmanaged detection uses object IDs so duplicate names remain visible.

HTTP redirects are rejected to avoid forwarding authorization to another endpoint. HTTPS uses certificate verification. Plain HTTP beyond loopback requires an explicit opt-in. Credential-write errors omit response bodies, and token values are redacted from ordinary API errors. Stored environment secrets are not supplied as replacements.

## Rollback

No change is rolled back automatically. A failure can leave a partially applied project; completed writes remain printed in order. Rerun the plan before deciding whether to finish or restore. Avoid concurrent edits while reconciling.

For exact recovery, stop Semaphore using your deployment procedure, restore the verified database and matching configuration, then restart it and check integrity and service health. Database restoration affects changes made after the backup, including unrelated projects. The backup tool doesn't perform restoration. A manifest alone cannot recover deleted task history or old private keys.

For a small correction, restore the previous reviewed manifest and plan again. Objects created by a renamed manifest remain until you remove them manually or explicitly prune eligible templates and views. An expired token must be replaced; database object edits cannot renew it.

## Troubleshooting

- `environment variable ... is empty or unset`: populate the named variable in the same process environment.
- `needs an SSH private key`: provide a private-key block through the configured variable, then review the plan again.
- `unknown keys`, `duplicate template`, or `unknown view`: correct the manifest before retrying.
- `missing its All view`: inspect the project layout in Semaphore; don't guess at the missing object.
- HTTP 3xx: set the final base URL. Redirects are rejected.
- HTTP 401 or 403: check token expiry and project permissions.
- `partial-apply`: inspect completed writes, then plan again or use the recovery set.
- Persistent drift after apply: compare the server version and managed fields with the manifest. Live API compatibility remains pending.

## Exit behavior

- `0`: offline validation passed, a plan found no managed drift, or apply completed without a reported error. Help and successful configuration also return 0.
- `1`: invalid configuration or manifest, missing credential, API failure, unexpected layout, token-expiration failure, or configurator refusal to overwrite.
- `2`: a plan found managed changes. Argument parsing also uses 2 for invalid command-line usage; inspect the accompanying message.
