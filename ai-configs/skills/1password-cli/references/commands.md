# The complete op command map

Every command group in `op` 2.x, with the safe pattern for each. Enumerated from `op --help` on a live install.

`op` gains commands and flags between releases, and this map can fall behind yours. The installed CLI is always the authority: `op --help` lists the groups on your build, and `op <command> --help` lists its subcommands and flags. When the two disagree, believe the CLI and not this file. The safety rules below do not depend on a version.

- [Two lists to read first](#two-lists-to-read-first)
- [Secrets and files](#secrets-and-files)
- [Items](#items)
- [Documents](#documents)
- [Vaults](#vaults)
- [Users and groups](#users-and-groups)
- [Service accounts](#service-accounts)
- [Connect servers](#connect-servers)
- [Events API](#events-api)
- [Shell plugins](#shell-plugins)
- [Accounts and sessions](#accounts-and-sessions)
- [Housekeeping](#housekeeping)
- [Who can run what](#who-can-run-what)

## Two lists to read first

**These commands print a credential on stdout.** Capture the output in a variable or a restricted file. Never let it reach a report, a log, or a transcript.

| Command | What it prints |
|---|---|
| `op read` | the secret value |
| `op item get --reveal` | the value of each concealed field |
| `op item get --format json` | the value of each concealed field, whatever `--reveal` says |
| `op item get --otp` | a current one-time password |
| `op document get` (no `-o`) | the file content |
| `op events-api create` | an Events API token |
| `op signin --raw` | a session token |
| `op item share` | a link that gives the holder the item. Treat the link as the secret. |

**These commands change or destroy something.** Ask the user first, unless the user's request names the exact change. Say what you are about to do, and to which item or vault.

`op item delete` · `op item edit` · `op item move` · `op item share` · `op document delete` · `op document edit` · `op account forget` · `op signout --forget` · `op plugin clear`

A second list is shorter, and absolute. **This skill does not run these at all**, whatever the task says:

`op vault create` · `op vault edit` · `op vault delete` · `op vault user grant|revoke` · `op vault group grant|revoke` · `op group create|edit|delete` · `op group user grant|revoke` · every `op user` command that changes a member · `op service-account create` · `op connect server create|edit|delete` · `op connect token create|edit|delete` · `op connect vault grant|revoke` · `op connect group grant|revoke`

They change the shape of the account: which vaults exist, who can open them, and who the members are. A mistake there is not a lost password, it is a person locked out of everything, or a vault opened to the wrong people. Report what change is needed and let the user make it in the 1Password app.

`--dry-run` exists on `op item create` and `op item edit`. Use it first when you edit.

## Secrets and files

```powershell
op read "op://<vault>/<item>/<field>"          # one value; see SKILL.md for the query parameters
op inject -i <template> -o <file>              # fill {{ op://... }} placeholders
op run --env-file <file> -- <command>          # resolve references into a child process
op run -- <command>                            # resolve references already in the environment
```

These four never need `--vault`, because a reference names its vault. `op run` masks values in the stdout and stderr of the child only.

## Items

```powershell
op item list --vault "<vault>"                          # no --vault needed, but scope it anyway
op item list --vault "<vault>" --categories Login --tags prod --favorite
op item get "<item>" --vault "<vault>"                  # masked human output
op item get "<item>" --vault "<vault>" --fields label=username,label=type
op item get "<item>" --vault "<vault>" --otp            # a one-time password
op item create --vault "<vault>" -                      # JSON template through stdin
op item edit "<item>" --vault "<vault>" --dry-run       # preview a change
op item delete "<item>" --vault "<vault>" --archive     # archive instead of destroy
op item move "<item>" --current-vault "<a>" --destination-vault "<b>"
op item template list
op item template get "<category>"
```

- `op item get`, `edit`, and `delete` need `--vault` for a service account. `op item list` and `op item template` do not.
- `op item move` copies the item and deletes the original, so the item gets a **new ID**. Any reference that used the ID breaks.
- `op item share` makes a link that anyone with the link can open. Default expiry is 7 days. Always narrow it, and always tell the user that the link itself is a secret:

```powershell
op item share "<item>" --vault "<vault>" --emails person@example.com --view-once --expires-in 1h
```

- Do not share an item to work around a permission problem. Ask the user to grant vault access instead.

## Documents

```powershell
op document list --vault "<vault>"
op document get "<title>" --vault "<vault>" -o <path>   # always use -o, never stdout
op document create <path> --title "<title>" --vault "<vault>" --file-name <name>
op document create <path> --title "<title>" --vault "<vault>" --tags <tags>
op document edit "<title>" <path> --vault "<vault>"
op document delete "<title>" --vault "<vault>" --archive
```

A downloaded document is a secret file. Restrict it, use it, delete it, the same as any staged value. See `references/windows.md` for the Windows permission rule.

## Vaults

Read only. Use these to find a vault, and to understand why a vault or a field is out of reach.

```powershell
op vault list                                   # every vault you can reach
op vault list --user <user> --permission <p>    # diagnose an access problem
op vault get "<vault>"
op vault user  list "<vault>"                   # who can reach this vault, and with which permissions
op vault group list "<vault>"                   # which groups can reach this vault
```

Permission names that appear in that output include `allow_viewing`, `allow_editing`, `allow_managing`, and finer ones such as `view_items`, `create_items`, `edit_items`, `archive_items`, `delete_items`, `view_and_copy_passwords`, `view_item_history`, `import_items`, `export_items`, `copy_and_share_items`, `print_items`, and `manage_vault`.

**This skill does not create, rename, or delete a vault, and it does not grant or revoke vault access.** A vault and its permissions are the shape of the user's account, and a wrong change there can lock a person out of everything or open a vault to the wrong people. If a task needs one of those changes, say which vault and which permission it needs, and let the user make the change in the 1Password app.

## Users and groups

Read only. Use these to answer "who is this?" and "which group holds that access?".

```powershell
op user list
op user get --me                         # the current identity
op user get <user> --fingerprint --public-key

op group list
op group get <group>
op group user list <group>               # who belongs to a group
```

**This skill does not change people or groups.** It does not add, remove, edit, lock out, or recover a member, and it does not create, rename, or delete a group, or change who belongs to one. Those changes reach a person on every device they own, and a group carries vault access with it. If a task needs one, name the person or the group and the change, and let the user make it in the 1Password app.

A service account cannot run any `op group` command in any case, and cannot run the `op user` commands that change a member.

## Service accounts

```powershell
op service-account ratelimit <service-account>
```

`ratelimit` shows how many requests are left. Use it before a long loop. See [Rate limits](reference.md#rate-limits) for the numbers.

**This skill does not make a service account.** `op service-account create` grants a new token the right to open a vault, which is the same kind of change as granting a person access. When a task needs a new token, tell the user which vault and which permissions it needs, and let them make it. Two facts help them decide:

- The permissions are fixed when the token is made. To add a vault later, they must make a new service account.
- `--expires-in` gives the token an end date. A date is better than a token that lives forever.

If the user makes a token and hands it to you, never echo it. Put it straight into 1Password, or into their environment variable, and confirm from metadata only.

## Connect servers

```powershell
op connect server list|get
op connect token list
```

`op connect vault grant|revoke` and `op connect group grant|revoke` change who can open a vault, so this skill does not run them. `op connect server create` and `op connect token create` hand out new access to a vault, and `op connect server create` also writes a `1password-credentials.json` file into the working directory, which is itself a credential. Leave all of these to the user, and say which server and vault the task needs.

A service account cannot run `op connect`. Remember that `OP_CONNECT_HOST` and `OP_CONNECT_TOKEN` take priority over `OP_SERVICE_ACCOUNT_TOKEN`, which produces a confusing failure when both are set.

## Events API

```powershell
op events-api create "<name>" --features signinattempts,itemusages --expires-in 90d
```

It prints a bearer token one time, for a log pipeline such as Splunk or Sentinel. Handle it the same as a service account token. A service account cannot run this command.

## Shell plugins

```powershell
op plugin list                     # the CLIs that have a plugin
op plugin init <executable>        # set up one, and pick the credential
op plugin inspect
op plugin run -- <command>
op plugin clear --all
op plugin credential import
```

A plugin gives a third-party CLI its credential with no value in any argument, which is safer than `op read`. Two hard limits: it works only in Bash, Zsh, or fish, and it needs the desktop app integration. So PowerShell cannot use it, and a service account cannot use it. It also needs `source ~/.op/plugins.sh` in the shell profile.

## Accounts and sessions

```powershell
op whoami --format json            # the only reliable identity check
op account list                    # local human accounts, even under a service account token
op account get
op account add --address <account> --email <email> --shorthand <name>
op account forget <account>        # 'op account forget --all'
op signin --account <account>
op signin --raw                    # prints a session token; put it in OP_SESSION
op signout                         # '--all', '--forget'
```

`op account list` reads this machine's configuration. It is not a session check. Use `op whoami`.

## Housekeeping

```powershell
op update                          # or 'winget upgrade 1password-cli'
op completion powershell|bash|zsh|fish
```

Global flags that matter: `--account`, `--format json`, `--iso-timestamps`, `--no-color`, `--session`, `--debug`, `--cache` (no effect on Windows), `--encoding`.

## Who can run what

| Command group | Service account | Desktop or manual session |
|---|---|---|
| `read`, `inject`, `run` | yes | yes |
| `item`, `document` | yes, with `--vault` on get/edit/delete | yes |
| `vault list`, `vault get`, `vault user/group list` | yes | yes |
| `user list/get`, `group list/get` | **no** | yes, for an owner or admin |
| `service-account ratelimit` | yes | yes |
| `connect list/get`, `events-api` | **no** | yes, for an owner or admin |
| `plugin` | **no** | yes, in Bash, Zsh, or fish only |
| `signin`, `signout`, `account` | not needed | yes |

A command that a service account cannot run fails with an authorization error, not a permission prompt. When that happens, do not retry: switch to the desktop mode, or ask the user to run it.
