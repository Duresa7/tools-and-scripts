# Installer signature check

Validate the Authenticode signature, publisher subject component, and SHA-256 hash of a downloaded installer.

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

Verify a downloaded installer on Windows before executing it or handing it off to automated deployment tools. It checks that the installer file exists, has a valid Authenticode digital signature, and was signed by the expected publisher. It can also enforce that the file matches an expected SHA-256 digest and that the check runs on an expected host computer.

Tested status: Locally checked; live matrix pending.

## Prerequisites

- Windows endpoint running Windows PowerShell 5.1 or PowerShell 7+.
- Read access to the downloaded installer binary.
- Python 3.11+, pytest, and PowerShell 7 (`pwsh`) for running the local test suite.

No live operating-system compatibility result is claimed. Local tests run on PowerShell 7. Windows integration still needs dedicated validation.

## Guided setup

| Field | Value |
|---|---|
| Platform | Windows |
| Runtime | Windows PowerShell 5.1+ or PowerShell 7+ |
| Privilege | Standard user account (no elevation required) |
| State changed | Check: none. Configurator: local JSON file and any missing parent folders. |
| Preview | Check: read-only. Configurator: `-WhatIf`. |
| Rollback | Check: none needed. Configurator: delete generated config or restore your backup. |
| Exit codes | 0 for all checks passed; 1 for verification failure; 2 for invalid input or configuration |

Generate local configuration without replacing existing files:

```powershell
.\configure.ps1
notepad .\config.local.json
.\installer-signature-check.ps1 -ConfigPath .\config.local.json
```

The configurator writes `config.local.json` with customizable example values and refuses to overwrite an existing file. Review every `CUSTOMIZE:` marker. Use `-OutputPath` to select another path, `-WhatIf` to preview the write, or `-Force` to overwrite if intended.

## Manual setup

Copy `config.example.json` to `config.local.json`:

```powershell
if (Test-Path .\config.local.json) { throw 'Local configuration already exists.' }
Copy-Item .\config.example.json .\config.local.json
notepad .\config.local.json
```

Set `InstallerPath` and `ExpectedPublisher`. `ExpectedSHA256` and `ExpectedComputer` are optional and can be left empty if not needed.
The tool reads only the configuration file explicitly passed with `-ConfigPath`. It does not silently search for or load configuration files.

## Inputs

Nonempty parameters override explicit JSON configuration, which overrides default settings. Empty or whitespace-only string overrides are ignored. In particular, `-ExpectedSHA256 ''` and `-ExpectedComputer ''` cannot disable a configured check. Set the corresponding JSON value to an empty string to disable it. The configurator uses the same precedence.

| JSON field and parameter | Description and default |
|---|---|
| `InstallerPath` | Required path to the target installer binary. |
| `ExpectedPublisher` | Required publisher name. Compared case-insensitively against the complete decoded `CN` or `O` attribute value. Leading/trailing whitespace and one matching pair of outer single or double quotes in the expected name are removed. |
| `ExpectedSHA256` | Optional SHA-256 hash. After trimming whitespace and removing spaces and hyphens, it must contain 64 hexadecimal characters. Case-insensitive. Default is empty (skipped). |
| `ExpectedComputer` | Optional host name. Compared case-insensitively against `[System.Environment]::MachineName`, never an environment variable. Surrounding whitespace is ignored. If either name contains a dot, full names must match; otherwise, first labels must match. Default is empty (skipped). |
| `Format` | Output format: `Table` or `Json`. Default is `Table`. |
| `-AsJson` | Switch parameter to select JSON output directly from the command line. |
| `-Help` | Prints syntax and parameter summary. |

Subject attributes are separated by unquoted, unescaped commas, plus signs, or semicolons. Plus signs support multi-valued RDNs, such as `CN=Example Corp + O=Other, C=US`. Quoted values support doubled quotes (`CN="Example ""Quoted"" Corp"`); a backslash escapes the following character. Quoted or escaped separators remain part of the value. Malformed subjects fail verification. Windows uses plus signs and doubled quotes in its [documented subject format](https://learn.microsoft.com/en-us/windows/win32/api/wincrypt/nf-wincrypt-certnametostra).

A short host name does not match a fully qualified name. For example, `host.example.com` does not match `host.example.net` or `host`. Use the name returned by `[System.Environment]::MachineName`; the tool does not resolve DNS names or append a domain suffix.

## Permissions

Standard user privileges. No administrative elevation is required. The script requires read permissions on the installer file and the configuration file. The configurator needs write access to its output directory.

## Dry run

The tool is strictly read-only and never makes system modifications. Running the script performs the verification checks and prints results without modifying any system state or file.

## Changes made

The check does not modify files, registry entries, scheduled tasks, certificates, or services. The configurator creates the requested JSON file and missing parent folders. With `-Force`, it replaces that configuration file without a backup.

## Safeguard reasoning

- Fail-closed checks: any invalid signature status, missing certificate, hash mismatch, host mismatch, or missing file halts execution immediately.
- Signature verification always calls `Get-AuthenticodeSignature`. There is no parameter for supplying a signature result. Local tests dot-source the script and override the internal `Get-InstallerSignature` function.
- Component-level publisher matching: publisher names are parsed as distinct X.500 distinguished name components (`CN` or `O`). Loose substring matching and look-alike prefix/suffix spoofing are rejected.
- Reliable host verification: when `ExpectedComputer` is set, the tool queries the operating system machine name via `[System.Environment]::MachineName`, preventing environment variable manipulation (`COMPUTERNAME`).
- Configurator protection: `configure.ps1` checks for existing files and refuses to overwrite without an explicit `-Force` parameter.

## Rollback

The check needs no rollback. Delete the generated local configuration if it is no longer needed. Restore your own backup if the configurator replaced a file with `-Force`.

## Troubleshooting

- Exit code 2 with `InstallerPath is required`: provide `-InstallerPath` on the command line or configure `InstallerPath` in JSON.
- Exit code 2 with `ExpectedPublisher is required`: specify the expected publisher via `-ExpectedPublisher` or in JSON.
- Exit code 2 with `ExpectedSHA256 must be a 64-character hexadecimal SHA-256 string`: check that the normalized hash contains exactly 64 hexadecimal characters.
- Exit code 1 with `Installer file not found`: verify that the file path exists and is accessible.
- Exit code 1 with `Installer signature is not valid`: the file has an invalid Authenticode signature, is unsigned, or has been altered.
- Exit code 1 with `Signer certificate subject ... does not match expected publisher`: inspect the certificate subject with `(Get-AuthenticodeSignature <path>).SignerCertificate.Subject`. Ensure `ExpectedPublisher` matches the exact Common Name (`CN`) or Organization (`O`).
- Exit code 1 with `File SHA-256 hash ... does not match expected hash`: the file contents do not match the expected digest.
- Exit code 1 with `Host mismatch`: the script was executed on a machine whose host name does not match `ExpectedComputer`.

## Exit behavior

- `0`: All verification checks passed (file exists, signature is valid, publisher matches, hash matches if specified, host matches if specified).
- `1`: One or more verification checks failed (signature invalid, publisher mismatch, hash mismatch, host mismatch, or missing installer file).
- `2`: Bad input or configuration (missing configuration file, invalid JSON, unknown configuration properties, invalid hash format, or missing required fields).

PowerShell rejects unknown parameters or invalid `-Format` values before the script's exit handling runs; these produce a native PowerShell invocation error. The configurator exits `0` after writing or previewing and `1` on configuration or write failure.
