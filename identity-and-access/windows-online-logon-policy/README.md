# Windows online logon policy

Create or update a workstation GPO that requires online domain-password sign-in, then check the resulting policy on each workstation.

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

Use this for a reviewed workstation OU where domain-password sign-in and unlock must require a reachable domain controller. It disables cached sign-in and excludes PIN, face, fingerprint, picture-password, and FIDO providers while retaining the Windows password provider.

This can lock laptops out of domain sign-in while off-network. Before applying it, test a local recovery account with a known password and keep console access available. Confirm that domain connectivity is available before sign-in, including any required pre-logon VPN. Start with a disposable pilot workstation. Keep servers and domain controllers outside the target scope.

Tested status: **Locally checked; live matrix pending**. Automated checks don't establish operating-system compatibility.

## Prerequisites

- Windows with Windows PowerShell 5.1 or newer. PowerShell 7 parsing and pure functions are checked locally; Windows cmdlet integration remains pending.
- The GroupPolicy module from RSAT for the setter. Run it in Windows PowerShell when the module requires it.
- A domain-joined workstation with CIM, resultant computer policy, Netlogon, and `Test-ComputerSecureChannel` for the checker.
- Working domain DNS, time synchronization, a healthy secure channel, and a tested local recovery account.
- A dedicated GPO name, reviewed OU membership, GPO backup, and a record of existing links and policy values before a live run.

## Guided setup

Run from this folder. The configurator prompts for all four inputs, writes `config.local.json`, and refuses to replace an existing file. It performs no directory discovery or remote changes.

| Field | Behavior |
|---|---|
| Platform | Windows management host and Windows domain workstation |
| Runtime | Windows PowerShell 5.1+; PowerShell 7 for local checks |
| Privilege | Delegated GPO and OU rights for changes; elevated workstation check |
| State change | GPO registry values, user-half status, enabled OU link; local config |
| Preview | Setter `-WhatIf`; checker is read-only |
| Rollback | Manual GPO restoration and client recovery; no automatic rollback |
| Exit codes | `0` success or preview; `1` error or failed check |

```powershell
.\configure.ps1
.\Set-WorkstationOnlineLogonPolicy.ps1 -WhatIf
```

For unattended setup, supply `-Domain`, `-Server`, `-TargetOU`, and `-GpoName` to `configure.ps1`. `-OutputPath` changes the destination. There is no overwrite option. Move an old config aside yourself before generating a replacement.

## Manual setup

Copy [config.example.json](config.example.json) to `config.local.json` without replacing an existing file:

```powershell
if (Test-Path -LiteralPath .\config.local.json) { throw 'Local configuration already exists.' }
[System.IO.File]::Copy((Join-Path $PWD 'config.example.json'), (Join-Path $PWD 'config.local.json'), $false)
notepad .\config.local.json
.\Set-WorkstationOnlineLogonPolicy.ps1 -ConfigPath .\config.local.json -WhatIf
```

Review every `CUSTOMIZE:` marker. The example uses documentation DNS names and a pilot OU; replace them before use. Keep the same config and GPO name with the checker on each workstation.

## Inputs

| Config key / parameter | Meaning |
|---|---|
| `Domain` / `-Domain` | Expected workstation AD DNS domain |
| `Server` / `-Server` | Domain controller for GPO operations and the secure-channel test |
| `TargetOU` / `-TargetOU` | OU distinguished name within the configured domain |
| `GpoName` / `-GpoName` | Dedicated GPO display name used by both scripts |

Parameters override local configuration. There are no defaults for these four values. Both scripts default `-ConfigPath` to `config.local.json` beside the scripts. If that default file is absent, all four parameters must be supplied. An explicitly specified missing config is always an error. Unknown config keys are rejected except `_comment*` fields. The checker validates `TargetOU` for config consistency but doesn't query OU membership.

The scripts use the current Windows identity and accept no secrets in config or arguments. Confirmation is a command parameter only; it cannot be stored in config. `OnlineLogon.Common.ps1` holds the shared configuration merge, provider list, registry settings, and evaluation functions. Dot-sourcing any script doesn't execute its main block.

## Permissions

Create the local config as an ordinary user. Use delegated rights to create or edit the dedicated GPO and manage its link on the selected OU. Run the workstation checker from an elevated PowerShell session so it can read computer resultant policy and test the secure channel. Don't grant broader directory rights just to run this tool.

## Dry run

```powershell
.\Set-WorkstationOnlineLogonPolicy.ps1 -WhatIf
```

`-WhatIf` validates local inputs and prints the target and planned action. It does not load GroupPolicy, contact the domain, or prove that the OU exists or permissions are sufficient. It works without the confirmation phrase. The workstation checker reads local state and contacts the configured controller to test the secure channel; it does not repair it.

```powershell
.\Test-WorkstationOnlineLogonPolicy.ps1
```

The checker emits one JSON object with `Registry`, per-check `Checks`, and aggregate `Pass`. Runtime and configuration errors emit `Pass: false` and `Error`.

## Changes made

After reviewing scope, backup, preview, and recovery access, apply the policy:

```powershell
.\Set-WorkstationOnlineLogonPolicy.ps1 -ConfirmationPhrase 'REQUIRE ONLINE DOMAIN SIGN-IN'
```

The phrase is case-sensitive. `-Confirm` also works through PowerShell's standard confirmation mechanism; it doesn't replace the phrase.

The setter creates or reuses the named GPO, disables its user half, writes the settings below, and creates or enables the link on the target OU. Existing link order, enforcement, filtering, and other GPO settings aren't adjusted. Reusing a GPO affects every scope where that GPO already applies. Review all existing links before approving the change.

| Registry value | Type | Value |
|---|---|---|
| `CachedLogonsCount` | String | `0` |
| `ForceUnlockLogon` | DWORD | `1` |
| `SyncForegroundPolicy` | DWORD | `1` |
| `ExcludedCredentialProviders` | String | Six Microsoft provider GUIDs, comma-separated |
| `PassportForWork\Enabled` | DWORD | `0` |
| `AllowDomainPINLogon` | DWORD | `0` |
| `BlockDomainPicturePassword` | DWORD | `1` |

The exact registry paths and provider GUIDs are in [OnlineLogon.Common.ps1](OnlineLogon.Common.ps1). The setter checks every value and type, user-half status, and the enabled link after writing. This verifies the selected controller's state, not replication or client application.

The scripts don't refresh client policy, delete existing Hello credentials, log off users, or create recovery accounts. Arrange a controlled computer policy refresh and any necessary startup/sign-in cycle, then run the checker on each workstation.

## Safeguard reasoning

An exact confirmation phrase protects the operation that can remove offline access. `ShouldProcess` keeps `-WhatIf` and `-Confirm` available. The setter resolves OU inheritance before creating or editing a policy and refuses duplicate GPO display names. Every write remains inside the approved operation.

Disabling Hello provisioning alone doesn't cover existing credentials, so the original six alternative providers are also excluded. Password sign-in stays available for recovery. Missing required registry values fail checks. A missing `DisablePasswordChange` value uses the Windows default of enabled machine-password changes; failure to read its parent key is an error.

The checker also checks domain membership, the expected domain, secure-channel health, automatic Netlogon startup, and exactly one matching GPO in resultant computer policy. It doesn't prove that offline unlock or fresh offline sign-in is rejected. Test those interactively on a disposable pilot, then reconnect and verify recovery. Also test that excluded tiles are unavailable and the local recovery account works. An already open desktop isn't automatically locked or logged off when networking disappears.

## Rollback

There is no transaction or automatic rollback. A failure after creation or the first write can leave a partially changed GPO, including changes affecting other linked OUs. Preserve the pre-change GPO backup, values, user-half status, links, link state, and scope before applying.

For an existing GPO, restore its reviewed backup and prior link/status settings using Group Policy Management. For a newly created dedicated GPO, disable or remove its new link, then delete the GPO only after confirming no other scope uses it. Removing a link alone does not prove that all effective client registry settings have reverted. Refresh computer policy while connected, restore prior settings explicitly where needed, and verify effective values and actual sign-in behavior.

If domain sign-in fails, use the tested local recovery account at the console, restore connectivity, and complete policy recovery. The tool cannot restore remote access to an offline laptop. Config files can be removed independently of directory rollback.

## Troubleshooting

- `Refusing changes`: use `-WhatIf` or supply the exact phrase after preparing recovery access.
- Missing configuration or fields: generate the local config, or provide all four parameters.
- `GroupPolicy` unavailable: install RSAT and use a Windows PowerShell session that can load the module.
- OU or duplicate-name error: review the distinguished name, directory rights, and GPO inventory before retrying.
- Readback failure: inspect partial changes on the selected controller and restore the backup if needed.
- `PolicyApplied` false: inspect computer resultant policy, filtering, link state, inheritance, replication, and the shared GPO name.
- `SecureChannel` false: check domain connectivity, DNS, and time. The checker doesn't repair trust.
- Registry checks false: inspect competing policies and refresh policy under controlled conditions.

Local verification uses pytest with `pwsh -NoProfile -NonInteractive` and mocked Windows cmdlets. Missing PowerShell fails the tests rather than skipping them.

## Exit behavior

- `0`: config created, setter readback passed, preview completed or confirmation declined, or every workstation check passed.
- `1`: invalid input, missing dependency, refused gate, directory or registry error, failed readback, or failed workstation check. Changes may already exist after a setter failure.

PowerShell parameter-binding or host startup errors can occur before the script's JSON error handler. The workstation check's JSON success result describes current configuration and connectivity, not a completed interactive sign-in test.
