# 1Password CLI reference

Detail that `SKILL.md` points to. Read the section you need.

- [Environment variables](#environment-variables)
- [Secret reference rules](#secret-reference-rules)
- [Files and documents](#files-and-documents)
- [Sessions and accounts](#sessions-and-accounts)
- [Archive and deletion](#archive-and-deletion)
- [Bulk work](#bulk-work)
- [Rate limits](#rate-limits)
- [What a service account cannot do](#what-a-service-account-cannot-do)
- [Connect servers](#connect-servers)
- [CI and CD](#ci-and-cd)
- [Versions](#versions)

## Environment variables

`op` reads these. Source: <https://www.1password.dev/cli/environment-variables/>

| Variable | Effect |
|---|---|
| `OP_SERVICE_ACCOUNT_TOKEN` | Authenticates as a service account. |
| `OP_CONNECT_HOST`, `OP_CONNECT_TOKEN` | Use a Connect server. **These take priority over the service account token.** Clear both to reach a desktop session. |
| `OP_ACCOUNT` | Selects the account, the same as `--account`. |
| `OP_SESSION` | Holds a session from a manual `op signin --raw`. |
| `OP_BIOMETRIC_UNLOCK_ENABLED` | Turns the desktop app integration on or off. |
| `OP_CACHE` | Turns caching on or off. It has no effect on Windows. |
| `OP_FORMAT` | Sets the default output format, `human-readable` or `json`. |
| `OP_ISO_TIMESTAMPS` | Prints ISO 8601 timestamps. |
| `OP_INCLUDE_ARCHIVE` | Includes archived items, the same as `--include-archive`. |
| `OP_RUN_NO_MASKING` | Turns off masking in `op run`. **Do not set this.** |
| `OP_CONFIG_DIR` | Moves the configuration directory. |
| `OP_DEBUG` | Turns on debug output. Read it with care, because debug output is verbose. |

## Secret reference rules

Source: <https://www.1password.dev/cli/secret-reference-syntax/>

- Form: `op://<vault>/<item>/[<section>/]<field>`.
- References ignore letter case. `op://Prod/DB/password` and `op://prod/db/PASSWORD` do the same thing.
- A name can hold letters, digits, `-`, `_`, `.`, and whitespace. Any other character, such as a slash in a title, makes the name unusable. Use the vault ID, item ID, and field ID instead.
- The section element is necessary only when the field label is not unique in the item. A reference without the section works while the label stays unique, so it can break later. Include the section.
- Use IDs when titles change often. IDs survive a rename, but they tell a reader nothing. `op item move` gives the item a **new** ID, because it copies and deletes.
- Query parameters:
  - `?attribute=otp` gives a current one-time password.
  - `?attribute=value|type|id|purpose` selects a field attribute.
  - `?attribute=content|name|size|id|type` selects a file attribute.
  - `?ssh-format=openssh` gives an SSH private key in OpenSSH form. See the policy in `SKILL.md`.

## Files and documents

- A file on an item has a reference too: `op://<vault>/<item>/[<section>/]<file name>`.
- `op document get "<title>" --vault "<vault>" -o <path>` writes a document to a file. The file holds real content, so restrict it and delete it, the same as any staged secret.
- `op read -n` leaves off the trailing newline. Use it when you write a file or hash a value, because a newline changes the hash.

## Sessions and accounts

- `op signin --account <account>` starts a desktop-app session. It does nothing if a session is already active.
- `op signin --raw` prints a session token for a manual sign-in. Put it in `OP_SESSION`. The session ends after 30 minutes of no use. This is the only path on a headless machine with no service account.
- `op signout` ends the session. `op signout --all` ends all of them. `op signout --forget` also removes the account from this machine.
- `op account add --shorthand <name>` adds an account. `op account forget --all` removes every account, which helps when a machine moves to app-only authentication.
- `op signin` selects an account in this order: the `--account` flag, then `OP_ACCOUNT`, then the account used last.
- `op vault list --user <user>`, `--group <group>`, and `--permission <permission>` show who can reach what. Use them to explain an `isn't a vault in this account` error.

## Archive and deletion

- `op read`, `op run`, and `op inject` skip archived items. A reference to an archived item fails, which looks like a missing item.
- `--include-archive`, or `OP_INCLUDE_ARCHIVE`, includes them.
- `op item delete --archive` archives instead of deleting. A deleted item stays in Recently Deleted for 30 days.

## Bulk work

Pipe JSON from one command into the next. `-` means "read the item from stdin".

```powershell
op item list --vault "<vault>" --format json | op item get - --fields label=username
op item get "<item>" --vault "<source>" --format json | op item create --vault "<target>" -
```

A JSON pipeline carries real values. Keep it inside one command, and never print the middle of it.

## Rate limits

Source: <https://www.1password.dev/service-accounts/rate-limits>

| Account type | Reads per hour | Writes per hour | Requests per day |
|---|---|---|---|
| Business | 10,000 | 1,000 | 50,000 |
| Teams | 1,000 | 100 | 5,000 |
| Individual, Families | 1,000 | 100 | 1,000 |

- The limit belongs to the token, and the daily limit belongs to the account.
- Over the limit gives HTTP 429.
- `op service-account ratelimit <service-account>` shows what is left.
- One `op item get` or `op read` that names the **vault ID and the item ID** costs one request. The same call with names costs three, because `op` must look each name up.

## What a service account cannot do

- Reach a Personal, Private, or Employee vault, or the default Shared vault.
- Change its own permissions. They are fixed when someone makes it. To add a vault, make a new service account.
- Run `op connect`, `op events-api`, `op vault edit`, any `op group` command, or the `op user` commands that add or change a member.
- Use the 1Password SSH agent or `op plugin`, because both need the desktop app.

A service account token starts with `ops_`, so secret scanners can find it in a leak. `--expires-in` at creation sets an expiry. Rotation keeps the permissions and can leave the old token valid for a short time, so there is no outage.

## Connect servers

A Connect server is a self-hosted API for secrets. Set `OP_CONNECT_HOST` and `OP_CONNECT_TOKEN`. Only `op read`, `op inject`, `op run`, and `op item get --format json` work against it. Remember that these two variables take priority over a service account token, which makes a confusing failure if both are set.

## CI and CD

- GitHub Actions: `1password/load-secrets-action@v4`. It masks values in the log as `***`.
- A token set in one step reaches every later step in the job. Put the token in the `env:` block of the single step that needs it.
- `unset-previous: true` clears secrets that an earlier step loaded.
- `op item create` with field arguments fails in GitHub Actions, because the runner looks like a pipe. The stdin template in `SKILL.md` is the method that works.
- 1Password's own guidance: a plaintext secret in source code, a config file, an env file, or a shell profile is one of the most common causes of a leak. Do not write a token into a shell profile.

## Versions

This skill does not target one release. Everything in it works across `op` 2.x. Only the points below depend on which version someone has, and each one is a thing you can test in a second rather than look up.

**One real minimum: 2.30.** Before that release, human output did not mask concealed fields, so `op item get` printed secrets to the screen. This skill leans on that masking everywhere. On an older CLI, treat every `op item get` as secret-bearing output, and tell the user to upgrade.

**Two behavior changes to know about.**

- Service accounts need 2.18 or later. Below that they do not work at all.
- `--tags` replaces the tags on an item from 2.33 onward. Earlier builds added to them. Either way, send the full list you want to keep.

**Do not trust a feature list, including this one.** Test the feature on the CLI in front of you:

```powershell
op <command> --help          # does this command exist on this build, and with which flags?
op --help                    # which command groups exist at all
```

1Password Environments are the current example. The documentation describes `op environment read` and `op run --environment`, but neither exists on the stable builds tested here — `op environment` answers `unknown command`, and `op run` offers only `--env-file` and `--no-masking`. Your build may differ, so check before you plan around them.

The error strings in `SKILL.md` come from a live CLI, and wording can change between releases. If a message on your machine differs, believe your CLI.

Upgrade with `op update`, or `winget upgrade 1password-cli`, or `brew upgrade 1password-cli`.
