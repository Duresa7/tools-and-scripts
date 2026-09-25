# Windows session limits

Enforce a daily local-time window and observed usage budget for direct members of a directory group.

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

Use this on a domain-joined Windows endpoint when existing sessions must end at a daily deadline or after a usage budget. A SYSTEM scheduled task checks once a minute. Directory logon hours can remain a separate sign-in backstop. This tool doesn't set them or block a new sign-in immediately.

Tested status: Locally checked; live matrix pending.

## Prerequisites

- Windows with Windows PowerShell 5.1 or newer, ScheduledTasks, directory access, `msg.exe`, and `logoff.exe`.
- A domain-joined endpoint whose computer account can read users and groups in its default directory naming context.
- A reviewed group sAMAccountName, local time zone, policy, and unused task name.
- An unused local state folder below an existing protected parent. Use a local NTFS volume. Avoid user-writable parent folders.
- Python 3.11+, pytest, and PowerShell 7 (`pwsh`) for local tests. Missing PowerShell fails the tests.

No live operating-system compatibility result is claimed. Local tests run the arithmetic and mocked boundaries on PowerShell 7. Windows integration still needs dedicated validation.

## Guided setup

| Field | Value |
|---|---|
| Platform | Domain-joined Windows |
| Runtime | Windows PowerShell 5.1+; installed task uses Windows PowerShell |
| Privilege | Ordinary account for configuration; elevation for install, uninstall, and a live tick; task runs as SYSTEM |
| State change | New protected folder, installed script and config, usage JSON, log, lock file, scheduled task; enforcement can sign users out |
| Preview | Default for Install, Uninstall, and Tick; `-WhatIf` also prevents changes |
| Rollback | Unregister task; retain files and usage; lost unsaved work cannot be restored |
| Exit codes | 0 for completion or preview; 1 for input or operation failure |

From this tool folder, create your local configuration:

```powershell
.\configure.ps1
notepad .\config.local.json
.\session-limits.ps1 -Mode Install -ConfigPath .\config.local.json
```

The configurator copies annotated example values into ignored `config.local.json`. Review every `CUSTOMIZE:` marker. It makes no directory query and refuses to overwrite any existing output. Use `-OutputPath` for a different file; its parent must exist. Policy parameters accepted by the main script also work on the configurator.

## Manual setup

Copy the example only if your local file doesn't exist:

```powershell
if (Test-Path .\config.local.json) { throw 'Local configuration already exists.' }
Copy-Item .\config.example.json .\config.local.json
notepad .\config.local.json
```

The main script reads only the file explicitly supplied with `-ConfigPath`. It never silently loads a local file. No credential belongs in the configuration. The task uses the computer account's directory access.

## Inputs

Parameters override explicit JSON configuration, which overrides the defaults below. Unknown configuration fields are rejected, except `_comment*` annotation fields.

| JSON field and parameter | Meaning and default |
|---|---|
| `TargetGroup` | Required group sAMAccountName; no default. Direct membership only, including changes made since sign-in. Nested and primary-group membership aren't evaluated. |
| `WindowStart` | Inclusive local `HH:mm` start; default `09:00`. |
| `WindowEnd` | Exclusive local `HH:mm` end; default `21:00`. An earlier end crosses midnight; equal endpoints are rejected. |
| `DailyBudgetMinutes` | Integer 1 to 1440; default 120. Shared across this account's simultaneous sessions on this endpoint. |
| `WarningMinutes` | Array of integer lead times from 1 to 1440; default `[10, 3]`. Empty array disables advance warnings. |
| `StatePath` | Required absolute local folder; no default. No trailing separator, traversal, reparse points, or network paths. |
| `TaskName` | Required unique root-folder task name; no default. Letters, digits, spaces, dots, underscores, and hyphens. |

`-Mode` is `Status` by default. `Install`, `Uninstall`, and `Tick` require `-Apply` for changes. `-Help` prints usage. `-WhatIf` takes precedence over `-Apply`.

Usage resets at local calendar midnight, including inside an overnight window. Elapsed usage uses timestamps with offsets; the window uses the local clock. The first observation adds no usage. Later observations add positive gaps of at most five minutes. Longer sleep gaps and backward clock jumps add nothing. Brief gaps, including disconnects or directory outages up to five minutes, may count on the next observation. This is sampled usage, not an exact activity meter.

Only WTS active sessions are processed. Disconnected sessions are excluded. A locked session can remain WTS active and consume budget. Multiple sessions for one account are charged once per tick. Budgets aren't shared across endpoints. Warnings fire once per threshold per calendar day, based on whichever limit arrives first. Failed warning delivery is retried on the next tick.

## Permissions

The installer creates the state folder with inheritance disabled and full control for SYSTEM and the local Administrators group only. It checks ownership and write permissions before installing files and before enforcement. It copies the runtime and effective config there so a writable working copy isn't executed as SYSTEM.

Elevate only the PowerShell session used to apply installation, removal, or a manual tick. Configuration and offline preview need no elevation. Status may require elevation under local task visibility policy. Keep the state folder and its parent protected; users who can replace the folder, disable the task, or change the clock can defeat enforcement.

## Dry run

```powershell
.\session-limits.ps1 -Mode Install -ConfigPath .\config.local.json
.\session-limits.ps1 -Mode Uninstall -ConfigPath .\config.local.json -Apply -WhatIf
.\session-limits.ps1 -Mode Tick -ConfigPath .\config.local.json
```

Preview validates and prints the effective policy and intended operation. It doesn't query sessions, contact the directory, validate Windows integration, write usage, send messages, or sign anyone out. Pure functions can be dot-sourced for saved-input tests without running the main block.

## Changes made

After reviewing the preview, run installation from an elevated PowerShell session:

```powershell
.\session-limits.ps1 -Mode Install -ConfigPath .\config.local.json -Apply
.\session-limits.ps1 -Mode Status -ConfigPath .\config.local.json
```

Installation refuses an existing task or state folder. It copies `session-limits.ps1` and an effective `config.local.json`, verifies the script hash, and registers a SYSTEM task with one-minute repetition, a five-minute execution limit, and IgnoreNew concurrency. It verifies the registered action, principal, and repetition. Registration uses no execution-policy bypass; local signing policy still applies.

Ticks keep one `<SID>.json` usage file per account, `enforcement.log`, and `tick.lock` in the state folder. State replacement is atomic; a file lock serializes manual and scheduled ticks. Usage is saved before warning or sign-out. The log contains timestamps, account SIDs, usage, and reasons. It has no automatic retention policy; arrange retention outside this tool.

Changing the working config after installation doesn't update the installed copy. To revise policy, uninstall with the original config, revise the working config, and install with a new state folder. A new folder starts fresh budgets. Retain the old folder for inspection and recovery.

## Safeguard reasoning

Install, uninstall, and enforcement have both an explicit `-Apply` gate and ShouldProcess support. A name collision never replaces another task. Uninstall checks the exact expected action and SYSTEM principal before removing it.

Directory lookup failures, unresolved session identities, and corrupt usage files skip enforcement for the affected account and write a warning. Losing unsaved work on an uncertain decision is worse than missing a tick. A corrupt file is retained unchanged for inspection; it never silently becomes a zero budget. Repair it from a known-good copy while the task is stopped.

WTS enumeration avoids localized console-output parsing and treats an idle endpoint as an empty session list. The session owner is checked again before messaging or sign-out to reduce the risk from recycled session IDs. Directory filter values are escaped. The directory is queried afresh each tick, without relying on a stale sign-in token.

## Rollback

From an elevated session, preview and remove the task:

```powershell
.\session-limits.ps1 -Mode Uninstall -ConfigPath .\config.local.json
.\session-limits.ps1 -Mode Uninstall -ConfigPath .\config.local.json -Apply
.\session-limits.ps1 -Mode Status -ConfigPath .\config.local.json
```

Removal disables, stops, unregisters, and verifies absence of the matching task. It keeps the installed script, config, log, lock file, and usage files. After confirming the task is absent, archive or delete that exact state folder manually. Directory policies remain untouched. Sign-out cannot restore unsaved work.

A failed install removes a task it successfully registered, but retains the new protected folder for inspection. If removal itself fails, inspect Task Scheduler immediately. Clear a reviewed partial folder before retrying installation. There is no automatic restoration of a previous task because existing tasks are never replaced.

## Troubleshooting

- `Task already exists` or `StatePath already exists`: inspect the existing installation. Use its original config to uninstall; archive the retained folder before a fresh install.
- `Task does not match`: inspect its action and principal. The script won't remove an unrelated task with the same name.
- Directory warnings: verify domain connectivity, group spelling, direct membership, and the computer account's directory read access.
- State warnings: stop the task, preserve the corrupt file, and restore a verified state copy. Deleting a usage file resets that account's budget.
- Missing warnings: check `msg.exe` permissions, active session state, and the installed log. Message failure doesn't block an already-due sign-out.
- Idle endpoint: no sessions is a successful tick. Enumeration failure is an error, not proof that nobody is signed in.
- Wrong cutoff time: verify local time zone and the installed configuration. The daily window follows the endpoint's clock.
- Access or signing error: inspect folder ACLs, execution policy, and Task Scheduler's last result. Don't make the runtime writable by managed users.

## Exit behavior

- `0`: configuration created, preview completed, task operation verified, status read, or tick completed. An absent task is valid status and an idempotent uninstall. Deliberately skipped accounts are logged and still return 0.
- `1`: invalid input, malformed config, overwrite refusal, missing dependency, ACL failure, task mismatch, session enumeration failure, state write failure, failed message/sign-out, or another operation error.

A final-message failure is logged but sign-out still proceeds when a limit is due. An advance-message failure returns 1 and its warning remains pending. Local tests cover decisions and mocked boundaries; task registration, ACL behavior, directory queries, session messaging, sign-out, and Windows PowerShell 5.1 execution remain live-matrix work.
