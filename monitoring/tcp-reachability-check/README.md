# TCP reachability check

Test TCP reachability across network endpoints from a specified source host using structured configuration.

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

Use this before deploying agents, running configuration automation, or executing service migrations. It runs read-only TCP connection probes against a declared set of target hosts and ports to confirm network reachability and firewall traversal before attempting heavier operations.

Open ports do not prove credentials, permissions, or application health. A successful TCP connection confirms that network routing and firewall policies allow traffic to reach the listening socket. It does not verify service authentication, remote procedure call readiness, or application-level protocol negotiation.

Tested status: **Locally checked; live matrix pending**. Automated checks don't establish operating-system compatibility.

## Prerequisites

- PowerShell 7 on Linux, macOS, or Windows, or Windows PowerShell 5.1 on Windows.
- Network routing and firewall rules permitting outbound TCP traffic to target ports.
- Read access to the local tool directory to load configuration.

## Guided setup

The configurator writes ignored `config.local.json` and refuses to replace an existing file unless an explicit overwrite flag is given. It copies documented template targets using RFC documentation addresses and hostnames.

| Field | Required answer |
|---|---|
| Platform | Windows, Linux, macOS |
| Runtime | PowerShell 7 on any OS; Windows PowerShell 5.1 on Windows |
| Privilege | Ordinary user |
| State change | None (read-only TCP probes); configurator writes local config |
| Preview | Not applicable; tool is inherently read-only |
| Rollback | Delete generated local config file; no network changes |
| Exit codes | `0` all targets reachable; `1` one or more targets unreachable, or parameter-binding failure; `2` configuration error or script input validation failure |

Run the configurator to initialize local configuration:

```powershell
.\configure.ps1
```

To configure specific parameters non-interactively or overwrite an older configuration file:

```powershell
.\configure.ps1 -OutputPath .\config.local.json -ExpectedSource "workstation01" -TimeoutSeconds 3 -Overwrite
```

## Manual setup

Copy [config.example.json](config.example.json) to `config.local.json` without replacing an existing file:

```powershell
if (Test-Path -LiteralPath .\config.local.json) { throw 'Local configuration already exists.' }
[System.IO.File]::Copy((Join-Path $PWD 'config.example.json'), (Join-Path $PWD 'config.local.json'), $false)
```

Edit `config.local.json` to replace the `CUSTOMIZE:` documentation addresses (192.0.2.x, example.net) with your environment's endpoints, target ports, optional labels, and expected source hostname.

## Inputs

| Configuration key | Parameter | Type | Meaning |
|---|---|---|---|
| `Targets` | `-Target`, `-Port`, `-Label` | Array / Arguments | Target endpoints to probe. Each target requires a host and port, with an optional label. |
| `ExpectedSource` | `-ExpectedSource` | String | Optional hostname where probes must run. Rejects execution if run on another host. |
| `TimeoutSeconds` | `-TimeoutSeconds` (alias `-Timeout`) | Double | Probe connection timeout in seconds (default: `3.0`). Must be greater than zero. |
| `Format` | `-Format` (alias `-OutputFormat`), `-Json` | String / Switch | Output display format: `Table` (default) or `Json`. |

The `-ConfigPath` parameter specifies an alternate JSON configuration file path (defaults to `config.local.json`). It is accepted on the command line only; configuration files do not define a `ConfigPath` key.

Precedence order: command-line parameters override local configuration, which overrides built-in defaults.

## Permissions

Run this script as an ordinary user. No root, administrator, or elevated privileges are required to initiate outbound TCP connection probes or write local configuration files.

## Dry run

The reachability checker is inherently read-only. It establishes a basic TCP three-way handshake and closes the connection immediately without sending application data, modifying firewall rules, or restarting services. No separate dry-run mode is required.

## Changes made

The checker script makes no state changes to the local operating system, network interfaces, or remote hosts. The only disk write occurs during guided setup when `configure.ps1` writes `config.local.json`.

## Safeguard reasoning

Network automation often fails when scripts execute on the wrong machine or when firewall paths are blocked. The script incorporates several safeguards:

- Source host guard: Setting `ExpectedSource` ensures probes execute only from the designated host (such as a deployment orchestrator), preventing misleading results from an operator laptop or jump box.
- Fail-closed design: Every target endpoint is probed before the exit code is decided; if any connection fails (refused, timed out, or unresolvable), the script reports all probe results and exits 1.
- Credential separation: The checker tests network transport only. It accepts no credentials and stores no secrets.
- Dot-sourcing safety: Script logic is guarded against accidental execution when functions are dot-sourced into other automation or test harnesses.
- Configurator non-overwrite: `configure.ps1` uses exclusive file creation to avoid overwriting existing customized settings.

Open ports do not prove credentials or application access. A service listening on TCP port 445 or 389 might still reject credentials, drop RPC binds, or suffer from directory replication errors. Use this check as a prerequisite gate, not as an end-to-end service validation.

## Rollback

Because the reachability check makes no system or network modifications, there is no state to revert. To remove local configuration, delete `config.local.json`.

## Troubleshooting

- `Connection refused`: The target IP was reached, but no process was listening on the specified port, or a host firewall rejected the packet.
- `Timed out`: Network routing is missing, packets were silently dropped by an intermediate firewall, or the target host is offline.
- `Name or service not known`: The DNS name could not be resolved. Check local DNS resolvers and search domains.
- `Current computer name does not match expected source`: Probes were executed on an unexpected machine. Verify the current hostname or remove `ExpectedSource` from configuration.
- `Invalid port`: Ports must be positive integers between 1 and 65535.
- `Configuration file not found`: Ensure `config.local.json` exists in the script directory or specify an explicit `-ConfigPath`.

## Exit behavior

- `0`: All target endpoints connected successfully.
- `1`: One or more target endpoints failed to connect (refused, timed out, or unresolvable). PowerShell also exits 1 if parameter binding fails before script execution begins (such as passing a non-numeric argument to `-Port`).
- `2`: Configuration or input validation error handled within the script (such as an out-of-range port, negative timeout, unsupported output format, missing configuration file, or source host mismatch).
