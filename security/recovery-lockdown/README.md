# Windows recovery lockdown

Keep the Windows Recovery Environment disabled and require authentication for recovery tools through a scheduled SYSTEM task.

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

On Windows 11, a standard user can pick "Reset this PC, Remove everything" from the recovery menu without credentials. This tool disables the recovery environment and sets `Security/RecoveryEnvironmentAuthentication` to require authentication if recovery becomes available again.

The task reapplies both controls after startup and daily. Feature updates can re-enable WinRE. This tool addresses the installed recovery environment; it does not prevent booting external installation media or replace disk encryption and firmware controls.

Disabling WinRE also removes Reset, Startup Repair, the recovery command prompt, and the recovery menu route to Safe Mode for administrators. Have a separate repair or rebuild path before installing.

Tested status: **Locally checked; live matrix pending**. Local tests cover decision logic and mocked operations. Windows build compatibility and recovery-menu behavior still need live validation.

## Prerequisites

- Target platform: Windows 11 with 64-bit Windows PowerShell 5.1 or PowerShell 7. Syntax is kept compatible with Windows PowerShell 5.1.
- Task Scheduler, the `ScheduledTasks` cmdlets, `reagentc.exe`, and the local MDM bridge class `MDM_Policy_Config01_Security02`.
- English `reagentc /info` output. Other languages and ambiguous status lines fail closed.
- An existing Task Scheduler folder and an unused task name.
- An unused installation directory outside the Windows directory, beneath an existing parent owned by SYSTEM, Administrators, or the Windows servicing identity. Ancestors must not allow other identities to delete children or change permissions. Junctions and other reparse points are refused.
- A reviewed local backup and an independent recovery path.
- For local tests: Python 3.11+, pytest, Ruff, and PowerShell 7 available as `pwsh`.

## Guided setup

| Field | Value |
|---|---|
| Platform | Windows 11 target; live validation pending |
| Runtime | 64-bit Windows PowerShell 5.1 or PowerShell 7 |
| Privilege | Ordinary user for configuration; elevation for live commands; SYSTEM for enforcement |
| State change | Installed files and ACLs, one scheduled task, WinRE configuration, recovery authentication policy |
| Preview | `-Install -WhatIf` or `-Uninstall -WhatIf` |
| Rollback | Remove the task explicitly; retained files and policy require deliberate cleanup |
| Exit codes | `0` success or preview, `1` operation/input failure, `2` recovery assertions failed |

From this tool folder, create the ignored local configuration:

```powershell
.\configure.ps1
notepad .\config.local.json
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json -WhatIf
```

The configurator writes `config.local.json` beside itself. It performs no discovery or live changes. Review every `CUSTOMIZE:` marker. Pass settings such as `-TaskName`, `-InstallRoot`, or `-DailyAt` to prefill them. `-OutputPath` selects another output file and `-ConfigPath` selects another input example. Existing output is always refused; there is no overwrite option. Rename an old configuration before generating another one.

## Manual setup

Copy [config.example.json](config.example.json) to `config.local.json` and review every marked value:

```powershell
if (Test-Path .\config.local.json) { throw 'Local configuration already exists.' }
Copy-Item .\config.example.json .\config.local.json
notepad .\config.local.json
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json -WhatIf
```

After review, run the gated installation from an elevated PowerShell session:

```powershell
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json `
  -Approval INSTALL_RECOVERY_LOCKDOWN
.\recovery-lockdown.ps1 -Check -ConfigPath .\config.local.json
```

Use the same configuration and parameter overrides for subsequent checks and uninstall. Installation saves the effective settings, including overrides, into its protected `config.local.json`. You can pass that installed file to later commands.

## Inputs

Parameters override the explicitly selected JSON configuration. That file overrides these defaults. No local configuration is loaded implicitly. Unknown keys are rejected, except `_comment*` annotations. No credentials are accepted or stored.

| Config key and matching parameter | Default | Purpose |
|---|---|---|
| `TaskName` | `Windows Recovery Guard` | Unused task name |
| `TaskPath` | `\` | Existing Task Scheduler folder, with leading and trailing backslashes |
| `InstallRoot` | `%ProgramData%\WindowsRecoveryGuard` | New dedicated installation directory |
| `ScriptName` | `recovery-lockdown.ps1` | Installed script filename inside that directory |
| `LogName` | `recovery.log` | Append-only operational log filename inside that directory |
| `DailyAt` | `03:00` | Daily local time, `HH:mm` |
| `StartupDelaySeconds` | `60` | Boot delay, 1 to 3600 seconds |
| `VerificationTimeoutSeconds` | `120` | Initial-run and stop timeout, 10 to 3600 seconds |
| `ReagentcPath` | `%SystemRoot%\System32\reagentc.exe` | Full path to the recovery utility |
| `PowerShellPath` | `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` | Full path to the task's 64-bit interpreter |

`%ProgramData%` and `%SystemRoot%` expand on Windows. Executable paths must point to trusted installed system binaries. UNC paths, traversal components, wildcards, quotes, and control characters are refused. Script and log names must be simple filenames ending in `.ps1` and `.log`. Windows API namespace and registry paths are fixed protocol locations.

Select one of `-Install`, `-Uninstall`, `-Check`, or `-Enforce`. The default is `-Check`. `-ConfigPath` selects the input JSON. `-Approval` is an exact, case-sensitive phrase for the selected operation. `-Enforce` is the scheduled task entry point and requires SYSTEM plus `ENFORCE_RECOVERY_LOCKDOWN` for a live run. Approval is never read from JSON.

## Permissions

Create and edit configuration as an ordinary user. Elevate only the live installation, check, or uninstall command. The MDM bridge requires SYSTEM, so the installer registers a service-account task with highest privileges and runs enforcement through that task. No password is supplied.

The installation directory, script, and installed configuration are owned by Administrators and grant access only to SYSTEM and Administrators. Logs inherit the directory permissions. Checks reject untrusted owners, writable installed paths, and reparse points. Keep the source files and configured executable paths under trusted control.

## Dry run

```powershell
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json -WhatIf
.\recovery-lockdown.ps1 -Uninstall -ConfigPath .\config.local.json -WhatIf
```

`-WhatIf` validates input and reports the selected operation and destination. It does not call Windows APIs, write a log, create files, or verify live readiness. It can run without elevation and on the local test host. Live checks require Windows and elevation:

```powershell
.\recovery-lockdown.ps1 -Check -ConfigPath .\config.local.json
```

Check verifies installed file permissions, the installed configuration, task identity and schedule, WinRE status, the policy provider marker, and the effective authentication requirement. It makes no changes. A passing check describes current state; it does not prove a future feature update or recovery-menu session will behave correctly.

## Changes made

Install copies this script and effective configuration into the new directory, restricts ACLs, verifies the script copy, and registers one task. The task runs as SYSTEM after startup and daily, starts when a missed run becomes available, permits battery operation, and ignores overlapping runs. Install refuses an existing directory or task. It starts the task immediately, waits for completion, checks its result, and reads back all recovery controls.

Each enforcement run reads `reagentc /info`, disables WinRE only when enabled, and creates or updates the MDM Security instance with the signed integer value `1`. It then reads WinRE status, `RecoveryEnvironmentAuthentication_ProviderSet`, and `WinREAuthenticationRequirement`. All three must match. The log records these values and the result, without account or machine names. An MDM failure produces a nonzero result.

The tool does not change other Security instance properties. The log grows until an operator archives it. Check does not write logs.

## Safeguard reasoning

Both install and uninstall require an exact approval phrase plus `ShouldProcess`. `-Confirm:$false` cannot bypass the approval phrase. The scheduled task carries the separate enforcement phrase because installing the task authorizes its recurring operation.

An unknown or duplicate recovery status is an error, not evidence of protection. Both the policy provider marker and effective registry value are checked because a provider write alone does not prove the effective policy. The signed integer matters to the MDM bridge; an unsigned value can fail with a type mismatch.

Task deletion requires the expected description, SYSTEM principal, executable, and exact action arguments. This prevents an accidental name collision from removing an unrelated task. Installation refuses to replace pre-existing state. Files remain after a failed installation for inspection; after registration, the task may already have changed recovery state. There is no automatic reversal of recovery controls.

## Rollback

Uninstall disables the matching task, stops it, waits for it to stop, removes it, and verifies removal:

```powershell
.\recovery-lockdown.ps1 -Uninstall -ConfigPath .\config.local.json -WhatIf
.\recovery-lockdown.ps1 -Uninstall -ConfigPath .\config.local.json `
  -Approval UNINSTALL_RECOVERY_LOCKDOWN
```

Uninstall retains the installation files, logs, disabled WinRE state, and authentication policy. A missing task is an idempotent success. Archive the log and manually remove the dedicated installation directory after confirming no task uses it. A reinstall requires a new or cleaned installation directory.

For a legitimate repair, first uninstall the task as above so a boot or daily run cannot disable WinRE during the repair. Then run these commands in the elevated session:

```powershell
$Settings = Get-Content .\config.local.json -Raw | ConvertFrom-Json
$RecoveryTool = [Environment]::ExpandEnvironmentVariables($Settings.ReagentcPath)
& $RecoveryTool /enable
if ($LASTEXITCODE -ne 0) { throw 'Recovery enable failed.' }
& $RecoveryTool /info
if ($LASTEXITCODE -ne 0) { throw 'Recovery query failed.' }
```

Confirm `Windows RE status: Enabled` before proceeding. The authentication requirement remains, so use authorized recovery credentials. If the image is unavailable, use your independent repair media or rebuild procedure. To restore lockdown afterward, archive and remove the old installation directory, reinstall, and check again.

This tool does not record prior policy values or automatically restore them. To remove the authentication requirement itself, use your approved Windows policy management process after checking whether device management or Group Policy also controls it. Do not delete the whole MDM Security instance; it can contain unrelated policies.

## Troubleshooting

- `Expected exactly one English Windows RE status line`: inspect the utility output and language. No localized status is guessed.
- `Enforcement requires SYSTEM`: use installation to run the task; an elevated interactive shell is insufficient for the MDM bridge.
- `InstallRoot already exists` or `Task already exists`: inspect the existing state. Uninstall only a matching installation, then archive its files before retrying.
- Permission or reparse-point failure: choose a new dedicated directory under a trusted system parent. Do not relax the protected files' ACLs.
- Task mismatch: use the original effective configuration. Inspect task actions before making manual corrections.
- Nonzero task result or failed readback: inspect the protected log, Task Scheduler history, MDM bridge availability, and effective policy. A partial failure may leave WinRE disabled.
- Initial-run timeout: inspect the task before retrying; it can still be running. Installation retains it and reports failure.

Run local checks from the repository root:

```powershell
python -m pytest -q windows/recovery-lockdown/tests
python -m ruff check windows/recovery-lockdown
python -m ruff format --check windows/recovery-lockdown
```

The tests invoke `pwsh -NoProfile -NonInteractive`. Missing PowerShell is a failure, not a skipped test. Live validation must still cover startup, daily runs, policy readback, a standard-user recovery session, and deliberate repair rollback on a disposable Windows target.

## Exit behavior

- `0`: installation and first enforcement verified, current checks passed, uninstall completed or the task was already absent, or `ShouldProcess` declined the operation. A successful preview does not mean a live change occurred.
- `1`: invalid configuration, refused approval, missing privilege or dependency, unknown recovery output, task or file mismatch, Windows operation failure, or timeout. Files, task, and recovery state may remain after a partial failure.
- `2`: check or scheduled enforcement read all recovery values but at least one assertion failed. An initial task result of `2` makes installation fail with `1`.

The configurator returns `0` after creating the file and `1` for invalid input, an existing output, or a write failure. PowerShell parameter-binding errors also return a nonzero status.
