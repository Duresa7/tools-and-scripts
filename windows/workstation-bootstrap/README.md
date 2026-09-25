# Windows workstation bootstrap

Rename a fresh Windows workstation and configure a key-only OpenSSH server.

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

Run once on a freshly installed Windows 11 or Windows 10 workstation before domain join. It assigns the machine its computer name, enables OpenSSH Server, configures key-only authentication for local administrators, restricts remote firewall access to approved subnets, and verifies network and DNS configuration so subsequent domain joining and configuration can proceed remotely over SSH.

Tested status: Locally checked; live matrix pending.

## Prerequisites

- Windows 11 Pro or Windows 10 Pro with Windows PowerShell 5.1 or PowerShell 7.
- Elevated administrator console for live bootstrap.
- An authorized OpenSSH public key string or key file path (wire-type match required; private keys and comment-only input are rejected).
- Allowed remote management network address, subnet CIDR, or firewall keyword (e.g. `192.0.2.0/24`).
- Domain DNS name to test resolution (e.g. `ad.example.com`).
- Python 3.11+, pytest, and PowerShell 7 (`pwsh`) for local unit tests. Missing PowerShell fails the tests.

No live operating-system compatibility result is claimed. Local tests run pure decision logic, validation, config rendering, and configurator behavior on PowerShell 7. Windows cmdlet execution remains live-matrix work.

## Guided setup

| Field | Value |
|---|---|
| Platform | Windows 11 Pro / Windows 10 Pro |
| Runtime | Windows PowerShell 5.1+ or PowerShell 7 |
| Privilege | Administrator (elevated PowerShell session) |
| State change | Computer rename, network category to Private, OpenSSH Server installed, firewall inbound rule, administrators_authorized_keys, sshd_config |
| Preview | `-WhatIf` previews configuration and actions without changes |
| Rollback | Partial: revert computer name, remove authorized keys, disable sshd, and disable firewall rule; network profile, capability, firewall ICMP rule, and sshd_config edits remain |
| Exit codes | 0 for success, preview, or help; 1 for invalid input, gate refusal, ACL failure, verification failure, or execution failure |

From this tool folder, create your local configuration:

```powershell
.\configure.ps1 -ComputerName 'WS-WORKSTATION1'
notepad .\config.local.json
.\bootstrap-workstation.ps1 -ConfigPath .\config.local.json -WhatIf
.\bootstrap-workstation.ps1 -ConfigPath .\config.local.json -ConfirmationPhrase 'BOOTSTRAP AND REBOOT'
```

The configurator copies annotated example values into ignored `config.local.json`. Review every `CUSTOMIZE:` marker. It refuses to overwrite an existing file unless `-Force` is supplied. Use `-OutputPath` for a different destination.

## Manual setup

Copy the example file only if your local file does not exist:

```powershell
if (Test-Path .\config.local.json) { throw 'Local configuration already exists.' }
Copy-Item .\config.example.json .\config.local.json
notepad .\config.local.json
```

The main script reads only the configuration file explicitly passed via `-ConfigPath`. It does not load unreferenced files silently. Parameters passed on the command line override file values. Never put private keys in configuration or arguments.

## Inputs

Parameters override explicit JSON configuration, which overrides defaults. Unknown configuration fields are rejected, except `_comment*` annotation fields.

| Field and parameter | Description and default |
|---|---|
| `ComputerName` | Required NetBIOS computer name. 1 to 15 alphanumeric characters or hyphens, not all digits. |
| `AuthorizedKey` | Literal OpenSSH public key string (e.g. `ssh-ed25519 AAAAC... user@example.com`). Must contain at least one usable public key whose decoded wire type matches the key type prefix. Comment-only input and private keys are rejected. Optional if `AuthorizedKeyPath` is provided. |
| `AuthorizedKeyPath` | Path to a file containing an OpenSSH public key. Validated under the same public key rules. Optional if `AuthorizedKey` is provided. |
| `AllowedRemoteAddresses` | Inbound firewall rule remote address scope: single IPv4 dotted-decimal address, IPv6 address, CIDR range, or supported firewall keyword (`Any`, `LocalSubnet`, etc.); default `192.0.2.0/24`. Validated during configuration parsing and preview. |
| `DomainDnsTestName` | Domain FQDN to test DNS resolution; default `ad.example.com`. |
| `Restart` | Boolean switch to reboot immediately after successful bootstrap; default `false`. |
| `ConfirmationPhrase` | Command parameter only: exact string `BOOTSTRAP AND REBOOT` required to apply rename and reboot. |

Value precedence: command line, then explicit local config, then documented default.

## Permissions

The configurator and offline preview run as an ordinary user.

Live bootstrap requires an elevated PowerShell session (Run as Administrator) because it changes system-wide settings:
- Installs the OpenSSH Server Windows capability or package.
- Modifies Windows Defender Firewall rules and enables ICMP echo.
- Changes network connection profiles to Private.
- Writes `C:\ProgramData\ssh\administrators_authorized_keys` and resets DACL permissions exclusively to `Administrators` and `SYSTEM`.
- Edits `C:\ProgramData\ssh\sshd_config` and restarts the `sshd` Windows service.
- Invokes `Rename-Computer` and optionally `Restart-Computer`.

## Dry run

Preview planned actions and configuration without applying changes:

```powershell
.\bootstrap-workstation.ps1 -ConfigPath .\config.local.json -WhatIf
```

Dry run validates the computer name, verifies the public key (ensuring wire type matches header prefix and rejecting private keys or comment-only content), and validates the remote address scope before displaying planned actions. It outputs the effective JSON configuration and planned steps without making changes, without requiring elevation, and without requiring the confirmation phrase. Pure functions can be dot-sourced for isolated unit testing.

## Changes made

After reviewing the preview, execute bootstrap from an elevated session:

```powershell
.\bootstrap-workstation.ps1 -ConfigPath .\config.local.json -ConfirmationPhrase 'BOOTSTRAP AND REBOOT'
```

Execution performs these steps:
1. Validates computer name, public key (at least one valid key, wire type match, no private keys or comment-only content), remote address scope (valid IPv4 dotted decimal, IPv6, prefix limits, or supported keywords), and the gate confirmation phrase.
2. Reports TPM presence and readiness, Secure Boot state, and Windows OS caption and build number. It does not check the TPM version or block execution on hardware prerequisites.
3. Sets non-domain network connection profiles to Private so the OpenSSH inbound firewall rule applies.
4. Installs the `OpenSSH.Server~~~~0.0.1.0` Windows capability, falling back to silent `winget` installation if needed. Stops and reports if `sshd` service is absent after both attempts.
5. Configures the `sshd` Windows service for Automatic startup and starts it.
6. Configures inbound firewall rule `OpenSSH-Server-In-TCP` for TCP port 22 with the validated remote address scope, and enables ICMP echo requests (`FPS-ICMP4-ERQ-In`).
7. Writes the public key to `C:\ProgramData\ssh\administrators_authorized_keys`, removes inheritance, and resets the security descriptor via SDDL (`O:BAG:SYD:P(A;;FA;;;BA)(A;;FA;;;SY)`) to grant Full Control exclusively to `Administrators` and `SYSTEM`, clearing all other explicit entries. Runs `icacls.exe /verify` and stops immediately with an error if verification fails.
8. Updates `C:\ProgramData\ssh\sshd_config` by placing global authentication directives (`PasswordAuthentication no` and `PubkeyAuthentication yes`) before the first `Match` block, preserving all subsequent match sections, and restarts the `sshd` service.
9. Renames the computer using `Rename-Computer -NewName <Name> -Force` (takes effect upon reboot).
10. Runs post-bootstrap verification: verifies `sshd` service status is Running, gathers network interface details, tests domain DNS resolution, and checks the host ed25519 public key fingerprint. Any verification failure (stopped service, failed domain DNS resolution, missing host key file, or host key fingerprint failure) throws an exception, exits nonzero, and skips reboot.
11. If post-bootstrap verification succeeds and `-Restart` is enabled, triggers `Restart-Computer -Force`. Otherwise informs the operator that a reboot is required.

## Safeguard reasoning

- Exact confirmation phrase (`BOOTSTRAP AND REBOOT`) prevents accidental execution of rename and reboot from stale history or misconfigured automation.
- `ShouldProcess` and `-WhatIf` allow operators to inspect effective configuration and planned actions without altering system state or requiring administrative rights.
- Remote address scope is validated during configuration loading and preview, rejecting malformed IP addresses, out-of-range CIDR prefixes, and unsupported keywords before any network or service changes occur.
- Public key validation confirms wire type matches the key type prefix, requires at least one usable public key, and rejects private keys and comment-only input, preventing lockouts from empty authorized key files.
- Global SSH directives (`PasswordAuthentication no` and `PubkeyAuthentication yes`) are placed before the first `Match` block in `sshd_config`, ensuring password authentication cannot remain active outside a match scope.
- Authorized keys permissions are reset to an explicit SDDL descriptor granting access only to `Administrators` and `SYSTEM`, stripping inheritance and removing all other explicit grants or denies. Verification with `icacls.exe /verify` halts execution before restart, rename, or reboot if the ACL is invalid.
- Post-bootstrap verification validates service state, domain DNS resolution, and host key fingerprints before rebooting. Verification failure halts execution with exit code 1 and aborts the reboot.
- Configurator refuses to overwrite existing local configuration files without `-Force`, preserving local edits.

## Rollback

The script modifies several system settings and files without creating automatic backups. If rollback is needed prior to domain join, run these cleanup commands:

```powershell
Rename-Computer -NewName 'OLD-COMPUTER-NAME' -Force
Remove-Item -Path 'C:\ProgramData\ssh\administrators_authorized_keys' -Force
Stop-Service sshd
Set-Service sshd -StartupType Disabled
Disable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
Restart-Computer
```

### Changes reverted by these commands

- Computer name is reset to the specified old name (takes effect upon reboot).
- The `administrators_authorized_keys` file is removed.
- The `sshd` service is stopped and set to disabled startup.
- The inbound `OpenSSH-Server-In-TCP` firewall rule is disabled.

### Changes that remain

The rollback commands do not return the machine to its prior state. The following changes remain:
- Network connection profiles remain set to Private.
- Windows Defender Firewall ICMP echo rule (`FPS-ICMP4-ERQ-In`) remains enabled.
- Installed `OpenSSH.Server~~~~0.0.1.0` Windows capability (or winget package) remains installed.
- Modifications to `C:\ProgramData\ssh\sshd_config` remain in place; previous file contents are not backed up.
- Unsaved work lost from an unplanned reboot cannot be recovered.

## Troubleshooting

- `Refusing rename and reboot`: Ensure `-ConfirmationPhrase 'BOOTSTRAP AND REBOOT'` is provided with exact case, or use `-WhatIf` for preview.
- `Public key source contains a private key`: Ensure the key in config or key file is an OpenSSH public key, not a private key.
- `Invalid OpenSSH public key format`: Ensure the key contains at least one usable key, is not comment-only, and that the base64-encoded wire type matches the prefix (e.g. `ssh-ed25519`).
- `Invalid AllowedRemoteAddresses`: Specify a valid IPv4 dotted-decimal address, IPv6 address, CIDR range (prefix length up to 32 for IPv4, 128 for IPv6), or a supported keyword (`Any`, `LocalSubnet`).
- `Invalid computer name`: Verify computer name is 1 to 15 characters, alphanumeric or hyphens, and not all digits.
- `Capability install failed`: Workstation may lack internet access or Windows Update connectivity; verify internet access for capability or `winget` fallback.
- `sshd service is not present after both install routes`: Manually verify Windows capability or install OpenSSH package before rerunning.
- `Authorized-key ACL verification failed`: `icacls.exe /verify` failed on `administrators_authorized_keys`. Ensure running from an elevated administrator console and check disk integrity.
- `sshd is not running`: The service failed to start or stopped during verification.
- `Host key file not found` or `Host key fingerprint failed`: Check that OpenSSH generated host keys in `C:\ProgramData\ssh\` on service startup.
- `Domain DNS resolution failed`: Check IP configuration, DNS servers, or gateway reachability. Verification failure aborts any requested reboot.

## Exit behavior

- `0`: Successful configuration generation (`configure.ps1`), dry-run preview (`-WhatIf`), help output (`-Help`), or completed bootstrap with passing post-verification report.
- `1`: Validation error (invalid computer name, invalid public key or wire type mismatch, malformed remote address scope, unknown config field), refusal to overwrite existing configuration without `-Force`, gate refusal (missing or incorrect confirmation phrase), missing prerequisite, non-Windows platform or non-elevated session, service or installation failure, ACL verification failure, or post-bootstrap verification failure (sshd stopped, domain DNS failure, missing host key, or fingerprint failure). When verification fails, the script exits 1 and skips any requested reboot.
