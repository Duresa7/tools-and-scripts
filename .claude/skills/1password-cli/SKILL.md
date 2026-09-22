---
name: 1password-cli
description: Drive the whole 1Password CLI (op) without putting a secret value into output — read, use, create, rotate, and share secrets, and manage items, documents, vaults, users, groups, service accounts, Connect servers, and SSH keys. Use this skill whenever a task touches a stored credential, API token, password, connection string, or SSH key, whenever it touches 1Password access or membership, and whenever op reports a sign-in, vault, field, or authorization error. Use it even when the user only says "get the token" or "put the key in the config", because the safe method differs from the obvious one.
---

# 1Password CLI

One rule controls every step below: **the secret value must not appear in your output.** Keep it out of commands, arguments, reports, logs, transcripts, screenshots, and git. Work with secret references. Let `op` move the values.

This skill holds no account, vault, or item names, so it works in any repository. Record your own names in your project instructions.

## Placeholders

`<vault>` is a vault name or ID. `<item>` is an item title or ID. `<field>` is a field label. A secret reference has this form:

```
op://<vault>/<item>/<field>                 # field at the top level of the item
op://<vault>/<item>/<section>/<field>       # field inside a section
```

The reference is not a secret. Put it in a file, a command, or git. References ignore letter case. Names can hold only letters, digits, `-`, `_`, `.`, and spaces. If a name holds any other character, such as a slash, use the ID form instead.

## Setup

1. Run `op --version`. Any `op` 2.x works. Below 2.30, human output did not mask concealed fields, so `op item get` printed secrets to the screen — on such a build, treat every `op item get` as secret-bearing output and tell the user to upgrade.
   - Windows: `winget install 1password-cli`
   - macOS: `brew install 1password-cli`
   - Linux: <https://www.1password.dev/cli/get-started/>
2. Select a sign-in mode. See [Sign-in modes](#sign-in-modes).
3. Run `op vault list` to find the vault name. Record it in your project instructions. Never record a token there.

## Check the session first

```powershell
op whoami                                                  # human output
$me = op whoami --format json | ConvertFrom-Json -AsHashtable   # parseable form
$me.user_type
```

- `SERVICE_ACCOUNT` — the token mode is active.
- `HUMAN` — a desktop or manual session is active.
- Exit code 1, or `account is not signed in` — go to [Sign-in modes](#sign-in-modes).

`-AsHashtable` is not optional in PowerShell. `op whoami --format json` returns both `url` and `URL`, and plain `ConvertFrom-Json` rejects two keys that differ only in case. In Bash, `op whoami --format json | jq -r .user_type` has no such problem.

Use `op whoami`, not `op account list`. `op account list` shows the human accounts that this machine has configured, even while a service account token controls the session. It answers a different question.

## Sign-in modes

### Service account token (best for agents)

A service account reaches a fixed group of vaults. It needs no prompt and no desktop app, so it is the correct mode for automated work. The CLI reads the token from `OP_SERVICE_ACCOUNT_TOKEN`.

Ask the user to set the variable. The user runs this, because the token must not enter your transcript:

```powershell
# PowerShell 7 or later. The User scope keeps the variable after a restart.
$t = Read-Host -Prompt "Service account token" -AsSecureString
[Environment]::SetEnvironmentVariable("OP_SERVICE_ACCOUNT_TOKEN", (ConvertFrom-SecureString $t -AsPlainText), "User")
```

If a new shell does not have the variable, its parent process is older than the variable. Load it again, but do not show it:

```powershell
$env:OP_SERVICE_ACCOUNT_TOKEN = [Environment]::GetEnvironmentVariable("OP_SERVICE_ACCOUNT_TOKEN","User")
```

What a service account cannot do:

- It cannot reach a Personal, Private, or Employee vault. Someone must share a vault with it.
- Its vault permissions are fixed when someone makes it. To add a vault, the user makes a new service account.
- `op item get`, `op item edit`, `op item delete`, and `op document` need a `--vault` flag. `op item list` and `op item template` do not. `op read`, `op inject`, and `op run` never need it, because the reference names the vault.

### Desktop app (interactive)

Use this mode for a vault that the service account cannot reach. The user must unlock the app, turn biometrics on in **Settings > Security**, and then turn on **Settings > Developer > Integrate with 1Password CLI**.

```powershell
$env:OP_SERVICE_ACCOUNT_TOKEN = $null   # $null removes the variable; an empty string does not
$env:OP_CONNECT_HOST = $null            # Connect variables take priority over the token
$env:OP_CONNECT_TOKEN = $null
op signin --account <account>
```

Two documented limits make this mode weak for an agent. Authorization ends after 10 minutes of no use, and after 12 hours in all cases. On Windows the authorization belongs to the process that asked for it, so each new sub-shell asks again. If a command waits and prints nothing, the app is waiting for the user. Ask the user to unlock it.

This session belongs to the user. Ask before you touch a vault that the task does not name.

## What op can do

`op` is not only a secret reader. It manages the account as well. `references/commands.md` maps every command and subcommand, with the safe pattern for each.

`op` changes between releases, and the map can fall behind the CLI in front of you. Treat the installed CLI as the authority: `op --help` shows the groups on this build, and `op <command> --help` shows its subcommands and flags. Check there before you decide a command does not exist. The rules below hold on every version.

| Group | Use it for |
|---|---|
| `read`, `inject`, `run` | move a value into a variable, a config file, or a process |
| `item` | list, get, create, edit, delete, move, and share items; read templates |
| `document` | store and fetch files |
| `vault` | see which vaults you can reach, and who else can reach them (read only) |
| `user`, `group` | see members and groups (read only) |
| `service-account` | check how many requests the token has left |
| `connect`, `events-api` | see self-hosted access and log feeds; making them is the user's job |
| `plugin` | give a third-party CLI its credential, in Bash, Zsh, or fish only |
| `account`, `signin`, `signout`, `whoami` | identity and sessions |

Two rules cover the whole surface.

**A command that prints a credential must never print into your output.** That is `op read`, `op item get` with `--reveal`, `--format json`, or `--otp`, `op document get` without `-o`, `op signin --raw`, `op item share`, and `op events-api create`. A new token appears one time and never again, so capture it, put it straight into the vault or into the user's hands, and confirm from metadata only. The same care applies to a token the user makes and hands to you.

**A command that changes access or destroys data needs the user's agreement first**, unless the user's request already names that exact change. Name the item before you act. `op item delete`, `op item edit`, `op item move`, `op item share`, `op document delete`, and `op document edit` all belong here. `--dry-run` on `op item create` and `op item edit` shows the result without the change.

**A third rule stops one step earlier. Do not change the shape of the account at all.** Deleting a password is a small, repairable loss. Deleting a vault, changing who can open one, or changing a member is not: it can lock a person out of everything they own, or open a vault to the wrong people. So this skill reads vaults, users, and groups, and never writes them. It does not run `op vault create|edit|delete`, `op vault user|group grant|revoke`, `op group create|edit|delete`, `op group user grant|revoke`, or any `op user` command that changes a member. The same reason covers `op connect`, because a Connect server or token hands vault access to a machine. When a task needs one of these, say which vault, person, or permission is involved, and let the user make the change in the 1Password app.

`op item share` deserves its own note. It makes a link that works for anyone who holds it, for 7 days by default. Narrow it with `--emails`, `--view-once`, and `--expires-in`, tell the user that the link is itself a secret, and never use sharing to work around a permission problem. Ask for vault access instead.

## Find items and fields

Field labels come from the item template, and they change with the category. Look at the item before you build a reference.

```powershell
op item list --vault "<vault>"                           # titles and categories only
op item get "<item>" --vault "<vault>"                   # human output keeps concealed fields masked
op item get "<item>" --vault "<vault>" --fields label=username,label=type
```

You do not have to assemble a reference by hand. `op` builds it for you, section and all, in the `reference` field of the JSON output. The JSON holds the value as well, so keep it in a variable and read only the one field:

```powershell
$ref = (op item get "<item>" --vault "<vault>" --fields label="<field>" --format json | ConvertFrom-Json).reference
```

Human output prints `[use 'op item get <id> --reveal' to reveal]` in place of a concealed value, so that output is safe to keep. Two flags break that:

- `--reveal` prints the value. Do not use it. It is fine to tell the user how to run it in their own terminal.
- `--format json` prints every concealed value in plaintext, and `--reveal` has no effect on it. Use JSON only inside a script that never prints the result. See [Rotate a secret](#rotate-a-secret).

## Read a secret

```powershell
$token = op read "op://<vault>/<item>/<field>"
$token = $null; Remove-Variable token -ErrorAction SilentlyContinue   # when the work is complete
```

Put the value in a variable. Do not echo it. Do not put it in a command argument, because arguments are visible to other processes and go into shell history.

Never run `op read` bare. Without a variable, the value goes straight to the terminal, and the terminal goes into the transcript. To test only that a reference works, throw the output away and read the exit code:

```powershell
op read "op://<vault>/<item>/<field>" > $null 2>&1
if ($LASTEXITCODE -eq 0) { "the reference resolves" } else { "the reference is wrong" }
```

Three query parameters help in special cases:

```powershell
op read "op://<vault>/<item>/one-time password?attribute=otp"     # a current OTP code
op read "op://<vault>/<item>/private key?ssh-format=openssh"      # OpenSSH form, see SSH keys
op read "op://<vault>/<item>/<field>" -n                          # no newline, for hashing or files
```

## Give a secret to a program

Select the first method that fits. Earlier methods keep the value away from the disk.

**1. An environment variable, with no file.** `op run` resolves any `op://` reference that it finds in the environment. This needs no file at all, so it is the best method for a single command.

```powershell
$env:API_TOKEN = "op://<vault>/<item>/<field>"     # the reference, not the value
try   { op run -- <command> }
finally { Remove-Item Env:API_TOKEN -ErrorAction SilentlyContinue }
```

**2. Several variables, with an env file.** The file holds references only, so it is safe to keep and safe to commit.

```powershell
# secrets.env  ->  API_TOKEN=op://<vault>/<item>/<field>
op run --env-file .\secrets.env -- <command>
```

`op run` hides values that appear in the stdout and stderr of the child process. It hides nothing else. `--no-masking` and the `OP_RUN_NO_MASKING` variable turn that off, so do not set either. A child process that writes its own log file writes the real value into it.

Your shell expands `$VAR` before `op run` starts. To make the child expand it instead, wrap the command: `op run -- sh -c 'curl -H "Authorization: Bearer $API_TOKEN" ...'`.

**3. A configuration file, with `op inject`.** The template holds `{{ op://... }}` placeholders. The output file holds real values, so it is a secret file.

```powershell
op inject -i .\config.tpl -o .\config.yml
```

**4. A file for a remote host, with `op read --out-file`.** The value goes from `op` into the file, so it never passes through the shell.

```powershell
op read "op://<vault>/<item>/<field>" --out-file .\stage.txt
```

Methods 3 and 4 write a secret to the disk. Restrict the file at once, use it, then delete it. On Windows, `--file-mode` does nothing, and a new file inherits the permissions of its parent directory, which can include other accounts:

```powershell
icacls .\stage.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"   # cut inheritance, then grant read to one user
# ... use the file ...
Remove-Item .\stage.txt            # no -Force: see below
```

On macOS and Linux, `--file-mode 0600` works, and `chmod 600` does the same job for `op inject` output.

Never put a secret value in a command string that a tool keeps, such as a command sent through an MCP server, or a step in a CI job. That string goes into a log or a history file. Use method 1 or method 4. In a report, name the variable, such as `$token`, and say that you removed the value.

## Create a secret

A field value in a command argument goes into the shell history and the process list. Send a JSON template through stdin instead.

```powershell
$template = op item template get "API Credential" | ConvertFrom-Json
$template.title = "<item>"
($template.fields | Where-Object id -eq "credential").value = $secret
$template | ConvertTo-Json -Depth 10 -Compress | op item create --vault "<vault>" -
```

`op item template list` shows the categories. `op item template get "<category>"` shows the fields of one.

If 1Password can make the value, let it, because then no value exists outside the vault:

```powershell
op item create --category Login --title "<item>" --vault "<vault>" --generate-password="letters,digits,symbols,32"
```

The length must be 1 to 64. Without a recipe you get 32 characters from letters, digits, and the symbols `!@.-_*`.

Check the result with metadata only: `op item get "<item>" --vault "<vault>"`. Then clear your variables and delete each temporary file.

## Rotate a secret

`op item edit "<item>" 'password=<value>'` puts the value in the process arguments. Never do that.

For a new random value, let 1Password make it. No value reaches your process:

```powershell
op item edit "<item>" --vault "<vault>" --generate-password="letters,digits,symbols,32"
```

For a value that must match an external system, send the item JSON through stdin, inside a script that never prints it:

```powershell
$item = op item get "<item>" --vault "<vault>" --format json | ConvertFrom-Json
($item.fields | Where-Object id -eq "password").value = $newSecret
$item | ConvertTo-Json -Depth 10 -Compress | op item edit "<item>" --vault "<vault>"
```

Three traps in this recipe:

- A JSON template does not hold passkeys, and it overwrites a passkey that the item holds. If the item has a passkey, edit it in the app instead.
- `--tags` replaces the tags. It does not add to them. Send the tags you want to keep.
- Run the command with `--dry-run` first. It prints the result of the change without making it.

After the change, look at `Updated` and `Version` in `op item get` output. To prove that two items hold the same value, compare fingerprints, not values: `scripts/compare-secret.ps1` and `scripts/compare-secret.sh` do this.

## SSH keys

For authentication, use the **1Password SSH agent**. A key in the vault then signs SSH and git operations, and no private key file exists. Turn the agent on in the desktop app.

Three facts decide whether this works for you:

- The agent is a desktop app feature. It needs an unlocked app and a user who can answer a prompt. A service account cannot use it, and a headless agent will wait forever.
- By default it offers only the keys in your Personal, Private, or Employee vault — the vaults a service account cannot reach. A key in a shared vault must be listed in `agent.toml`. See `references/windows.md` for the path and the syntax.
- A public key is not secret. `op item get "<item>" --vault "<vault>" --fields label="public key"` is safe. Use it to fill `authorized_keys`.

Make a new key inside the vault, so the private key never reaches a disk:

```powershell
op item create --category "SSH Key" --title "<item>" --vault "<vault>" --ssh-generate-key
```

The CLI can export a private key with `op read "op://<vault>/<item>/private key?ssh-format=openssh"`. **This skill does not permit that.** The block is a policy, not a CLI limit: an exported key leaves the vault, and 1Password can no longer protect it. If a task seems to need the private key value, stop and ask the user. The agent, or a new key, is almost always the correct answer.

## Cost and limits

An agent can empty an account's quota with a loop. Plan the calls.

- Windows has no cache. The `--cache` help says so. Each command is a fresh network round trip.
- A service account has an hourly and a daily request limit. A Teams, Individual, or Families token gets 1,000 reads per hour, and as few as 1,000 requests per day. Check with `op service-account ratelimit <service-account>`.
- Batch the work. One `op run --env-file` with five references costs less than five `op read` calls. One `op item get --fields label=a,label=b` costs less than two calls.
- `op` exits 1 on any error, so a script can test the exit code instead of reading the message.

## Errors

These strings come from a live CLI. Wording can change between releases, so match on the idea, not on the exact characters, and believe your CLI over this table. `op` exits 1 on any error, so a script can test the exit code instead of the text.

| Message | Cause | Action |
|---|---|---|
| `account is not signed in` | no token, and no desktop session | See [Sign-in modes](#sign-in-modes). |
| `a vault query must be provided when this command is called by a service account` | no `--vault` flag | Add `--vault "<vault>"`. |
| `"<name>" isn't a vault in this account` | the service account cannot reach the vault | Run `op vault list`. Ask the user to share the vault, or use the desktop mode. |
| `"<name>" isn't an item in the "<vault>" vault` | wrong title, or wrong vault | Run `op item list --vault "<vault>"`. |
| `item '<vault>/<item>' does not have a field '<field>'` | wrong field label, or a missing section | Run `op item get "<item>" --vault "<vault>"` and read the labels. |
| `invalid secret reference` | the reference does not start with `op://` | Correct the reference. |
| `No accounts configured for use with 1Password CLI` | the desktop integration is off | Ask the user to turn on Settings > Developer > Integrate with 1Password CLI. |
| the command waits and prints nothing | the app is waiting for an unlock | Ask the user to unlock 1Password. |

## Bash and zsh

The `op` commands do not change. Only the shell syntax changes.

| Task | PowerShell | Bash |
|---|---|---|
| put a secret in a variable | `$token = op read "op://..."` | `token=$(op read "op://...")` |
| clear a variable | `Remove-Variable token` | `unset token` |
| clear an env variable | `Remove-Item Env:X` | `unset X` |
| restrict a file | `icacls f /inheritance:r /grant:r "$($env:USERNAME):(R)"` | `chmod 600 f` |
| delete a file | `Remove-Item f` (no `-Force`) | `rm -f f` |
| edit JSON | `ConvertFrom-Json` and `ConvertTo-Json` | `jq` |

`op plugin` gives a third CLI its credential with no value in any argument, which is better than `op read`. It works only in Bash, Zsh, or fish, and only with the desktop app, so a PowerShell shell or a service account cannot use it.

## Guardrails

- Treat a file that holds or stages a credential as opaque. Answer questions about its name, endpoint, format, scope, or validity from metadata and observed behavior. Never print its contents to look at them.
- Keep values out of git, screenshots, and captured output. Record the command with the value removed, and say that you removed it.
- Do not print a value when the user asks you to. Give a fingerprint and the metadata instead, and explain the reason. The user can always see the value in the app, or with `--reveal` in their own terminal. A fingerprint of a short or guessable value is itself a risk, so prefer a comparison that you run.
- Check the metadata and the scope of a credential before you depend on it. A stored credential can be old or out of date.
- Ask the user before you write to, or delete from, a vault that the task does not name.
- Never change a vault, a group, or a member. Read them, report what you find, and let the user make the change. This holds even when the user asks inside a larger task, because the change reaches every device that person uses.
- Sharing an item reaches other people too, and you cannot take a link back. Explain the effect, name who gets it, and wait for the answer.
- If a value does reach output or a file, stop. Delete or hide the value, and tell the user. Rotation is the user's decision.

## More detail

- `references/commands.md` — every command and subcommand, the two safety lists in full, vault permission names, and a table of what a service account may run.
- `references/reference.md` — environment variables, secret reference rules, archive behavior, `op signout`, Connect servers, CI notes, and rate limits.
- `references/windows.md` — the SSH agent on Windows, `agent.toml`, git commit signing, file permissions, and PowerShell quoting traps.
- `scripts/compare-secret.ps1`, `scripts/compare-secret.sh` — compare a stored secret with another value, and print only MATCH or DIFFERENT.
