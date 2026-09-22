# 1Password CLI on Windows

Behavior that differs on Windows, and the setup that Windows needs. Measured on Windows 11 with PowerShell 7 and `op` 2.x.

None of this depends on a particular `op` release. It comes from Windows itself: how Windows handles file permissions, how it names pipes, and how PowerShell parses a string. If a command here behaves differently on your build, check it with `op <command> --help` and trust that.

- [File permissions](#file-permissions)
- [No cache](#no-cache)
- [Desktop app integration](#desktop-app-integration)
- [The SSH agent](#the-ssh-agent)
- [agent.toml](#agenttoml)
- [Git commit signing](#git-commit-signing)
- [PowerShell quoting](#powershell-quoting)

## File permissions

`--file-mode` does nothing on Windows. It is a POSIX mode, and it has no Windows equivalent. The command still exits 0, which makes the failure silent.

A file that `op read --out-file` or `op inject -o` creates inherits the permissions of its directory. Measured on this machine, a staged file in the temporary directory arrived with six inherited entries, and two of them gave **Modify** rights to groups other than the owner.

So on Windows, one extra command is necessary after every write of a secret to disk:

```powershell
op read "op://<vault>/<item>/<field>" --out-file .\stage.txt
icacls .\stage.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
# ... use the file ...
Remove-Item .\stage.txt
```

`/inheritance:r` removes the inherited entries. `/grant:r` then gives read access to one account. Check the result with `icacls .\stage.txt`, or with `(Get-Acl .\stage.txt).Access`.

**Delete it with `Remove-Item`, and not with `Remove-Item -Force`.** This order of steps has a trap. After `/grant:r ...(R)` the file is read-only to you, and `-Force` starts by clearing the read-only attribute, which needs a write permission you just removed. The delete then fails with "Access to the path is denied", and a live credential stays on the disk. Plain `Remove-Item` deletes it without touching the attributes. `[System.IO.File]::Delete($path)` also works.

If a delete does fail, give yourself full control again and repeat it:

```powershell
icacls .\stage.txt /grant "$($env:USERNAME):(F)"
Remove-Item .\stage.txt
```

`Remove-Item` unlinks the file. It does not overwrite the disk blocks. If a high-value secret reached a file, tell the user, and let the user decide about rotation.

## No cache

The `op --help` text says: "Caching is enabled by default on UNIX-like systems. Caching is not available on Windows."

On Windows every `op` command is a fresh network round trip. `--cache=true` changes nothing. Two results matter for an agent:

- Commands are slower, and a loop of `op read` is much slower than one `op run --env-file`.
- Each call counts against the service account rate limit. See `references/reference.md`.

## Desktop app integration

Setup order, which the documentation gives:

1. Unlock the 1Password app.
2. Turn on Windows Hello in **Settings > Security**.
3. Turn on **Settings > Developer > Integrate with 1Password CLI**.
4. Turn on **Settings > General > Keep 1Password in the notification area**, so the app can prompt while it is not in front.

`OP_BIOMETRIC_UNLOCK_ENABLED` turns the integration on or off from the environment.

Two documented limits make this mode weak for an agent:

- Authorization ends after 10 minutes with no use, and after 12 hours in all cases.
- On Windows the authorization belongs to the process that asked for it, together with that process's start time. A sub-shell is a different process, so **each sub-shell must ask again**. On macOS and Linux the authorization reaches sub-processes, so Windows behaves differently here.

A prompt that nobody answers looks like a command that has stopped. If `op` prints nothing and does not return, ask the user to unlock the app.

Errors that belong to this mode: `No accounts configured for use with 1Password CLI`, `LostConnectionToApp`, and `connectionreset`. Restart the desktop app, or run `op account forget --all` when a machine moves to app-only authentication.

## The SSH agent

The 1Password SSH agent signs with a key that stays in the vault. Turn it on in **Settings > Developer > SSH Agent**.

**Windows needs one more step, and it is the most common failure.** Windows ships its own OpenSSH Authentication Agent service, and both agents claim the same named pipe, `\\.\pipe\openssh-ssh-agent`. The 1Password agent then serves nothing, with no error. Stop and disable the Windows service first:

1. Open `services.msc`.
2. Find **OpenSSH Authentication Agent**.
3. Set Startup type to **Disabled**.
4. Stop the service.

Then check that the pipe exists: `Test-Path '\\.\pipe\openssh-ssh-agent'`.

Do **not** set `SSH_AUTH_SOCK` on Windows. The named pipe is the interface. `SSH_AUTH_SOCK` belongs to macOS and Linux, where the sockets are:

- macOS: `~/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock`
- Linux: `~/.1password/agent.sock`

The agent needs an unlocked app and a user who can answer a prompt. It approves each application separately, and it forgets the approval when 1Password locks. It also hides prompts that come from a background process, behind an indicator in the notification area. A service account cannot use the agent at all.

## agent.toml

`agent.toml` lists the keys that the agent offers, and in which order.

- Windows: `%LOCALAPPDATA%\1Password\config\ssh\agent.toml`
- macOS and Linux: `~/.config/1Password/ssh/agent.toml`

With no file, the agent offers every SSH Key item in your Personal, Private, or Employee vault. A key in any other vault is invisible until the file lists it. An empty file therefore turns the default off.

```toml
[[ssh-keys]]
item = "Deploy Key - example"
vault = "Automation"

[[ssh-keys]]
item = "id_ed25519"
vault = "Personal"
account = "my.1password.com"
```

## Git commit signing

1Password signs a git commit with `op-ssh-sign`, a helper that the app installs.

**Do not hard-code its path.** On this machine it resolves to an app execution alias in `WindowsApps`, and the older path under `%LOCALAPPDATA%\1Password\app\8\` does not exist. Resolve it:

```powershell
$signer = (Get-Command op-ssh-sign.exe).Source
git config --global gpg.format ssh
git config --global user.signingkey "$(op item get '<item>' --vault '<vault>' --fields label='public key')"
git config --global commit.gpgsign true
git config --global gpg.ssh.program "$signer"
```

The public key is not secret, so the `user.signingkey` value is safe to keep in a config file.

## PowerShell quoting

`op` takes assignment statements such as `field=value` and recipes such as `letters,digits,32`. PowerShell can change these before `op` sees them. Measured traps:

- **A colon after a variable name eats the variable.** This is the worst one, because it fails quietly. `"$env:USERNAME:(R)"` does not give `<your-user-name>:(R)`. PowerShell reads the name as `env:USERNAME:` and the whole variable disappears, so the argument arrives as `(R)`. `icacls` then answers `Invalid parameter "(R)"` with exit code 87, and the file keeps every inherited permission. Write `"$($env:USERNAME):(R)"`, and check `$LASTEXITCODE` after the command.
- **Double quotes interpolate.** `"password=a$bc"` sends `password=a` plus an empty variable. Single-quote every assignment statement: `'password=a$bc'`.
- **`ConvertFrom-Json` rejects keys that differ only in case.** `op whoami --format json` returns `url` and `URL`, so the plain pipe throws. Use `ConvertFrom-Json -AsHashtable`.
- **A bare semicolon splits the command.** PowerShell reads it as a statement separator.
- **Bare parentheses are a parse error.** Quote them.
- Commas and square brackets are safe without quotes, so a recipe such as `--generate-password=letters,digits,32` needs no quotes. Quoting it is harmless.
- A backslash escapes `.`, `=`, and `\` in a **section or field name**, never in a value.

Remember that an assignment statement puts the value in the process arguments, which other processes can read. Use the stdin template in `SKILL.md` for any real value.

One more PowerShell rule: `$env:X = ""` leaves the variable in place with an empty value. `$env:X = $null` removes it. Use `$null` when you clear a token.
