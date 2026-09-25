<#
.SYNOPSIS
Verify the Authenticode signature, publisher, and hash of a downloaded installer.
.DESCRIPTION
Read-only verification of a downloaded installer file: checks file existence,
validates Authenticode signature status, verifies that the signer certificate
subject contains an exact CN or O match for the expected publisher, and optionally
verifies the file SHA-256 hash and host computer name.
Precedence: nonempty parameters override explicit JSON configuration, then defaults.
Exit codes: 0 when every check passes, 1 when a check fails, 2 on bad input or config.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath,
    [string] $InstallerPath,
    [string] $ExpectedPublisher,
    [string] $ExpectedSHA256,
    [string] $ExpectedComputer,
    [ValidateSet('Table', 'Json')]
    [string] $Format,
    [switch] $AsJson,
    [switch] $Help
)

function Get-DefaultConfiguration {
    [ordered]@{
        InstallerPath     = ''
        ExpectedPublisher = ''
        ExpectedSHA256    = ''
        ExpectedComputer  = ''
        Format            = 'Table'
    }
}

function Get-RealComputerName {
    # Returns the real machine name directly from the operating system, never an environment variable.
    return [System.Environment]::MachineName
}

function Test-ComputerMatch {
    [CmdletBinding()]
    param(
        [string] $ActualComputer,
        [string] $ExpectedComputer
    )
    if ([string]::IsNullOrWhiteSpace($ExpectedComputer)) {
        return $true
    }
    if ([string]::IsNullOrWhiteSpace($ActualComputer)) {
        return $false
    }
    $act = $ActualComputer.Trim()
    $exp = $ExpectedComputer.Trim()
    if ($act.Contains('.') -or $exp.Contains('.')) {
        return $act.Equals($exp, [System.StringComparison]::OrdinalIgnoreCase)
    }
    $actShort = $act.Split('.')[0]
    $expShort = $exp.Split('.')[0]
    return $actShort.Equals($expShort, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-HashMatch {
    [CmdletBinding()]
    param(
        [string] $ActualHash,
        [string] $ExpectedHash
    )
    if ([string]::IsNullOrWhiteSpace($ExpectedHash)) {
        return $true
    }
    if ([string]::IsNullOrWhiteSpace($ActualHash)) {
        return $false
    }
    $actual = $ActualHash.Trim().Replace('-', '').Replace(' ', '')
    $expected = $ExpectedHash.Trim().Replace('-', '').Replace(' ', '')
    return $actual.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)
}

function ConvertFrom-DistinguishedName {
    [CmdletBinding()]
    param([string] $Subject)

    $attributes = New-Object 'System.Collections.Generic.List[object]'
    $position = 0
    while ($position -lt $Subject.Length) {
        $start = $position
        while ($position -lt $Subject.Length -and $Subject[$position] -ne '=') {
            $position++
        }
        if ($position -eq $Subject.Length) { throw 'Missing attribute value.' }
        $name = $Subject.Substring($start, $position - $start).Trim()
        if ($name -notmatch '^(?:[A-Za-z][A-Za-z0-9.-]*|[0-9]+(?:\.[0-9]+)+)$') {
            throw 'Invalid attribute name.'
        }
        $position++
        while ($position -lt $Subject.Length -and [char]::IsWhiteSpace($Subject[$position])) {
            $position++
        }

        $quoted = $position -lt $Subject.Length -and $Subject[$position] -eq '"'
        if ($quoted) { $position++ }
        $closed = -not $quoted
        $value = New-Object System.Text.StringBuilder
        $significantLength = 0
        while ($position -lt $Subject.Length) {
            $character = $Subject[$position]
            if ($character -eq '\') {
                $position++
                if ($position -eq $Subject.Length) { throw 'Incomplete escape.' }
                [void]$value.Append($Subject[$position])
                $significantLength = $value.Length
            } elseif ($character -eq '"') {
                if (-not $quoted) { throw 'Unexpected quotation mark.' }
                if ($position + 1 -lt $Subject.Length -and $Subject[$position + 1] -eq '"') {
                    [void]$value.Append('"')
                    $significantLength = $value.Length
                    $position++
                } else {
                    $position++
                    $closed = $true
                    break
                }
            } elseif (-not $quoted -and $character -in @(',', '+', ';')) {
                break
            } else {
                [void]$value.Append($character)
                if ($quoted -or -not [char]::IsWhiteSpace($character)) {
                    $significantLength = $value.Length
                }
            }
            $position++
        }
        if (-not $closed) { throw 'Unterminated quoted value.' }
        $attributes.Add([pscustomobject]@{
            Name = $name
            Value = $value.ToString().Substring(0, $significantLength)
        })
        while ($position -lt $Subject.Length -and [char]::IsWhiteSpace($Subject[$position])) {
            $position++
        }
        if ($position -lt $Subject.Length) {
            if ($Subject[$position] -notin @(',', '+', ';')) {
                throw 'Unexpected text after attribute value.'
            }
            $position++
            if ([string]::IsNullOrWhiteSpace($Subject.Substring($position))) {
                throw 'Missing attribute after separator.'
            }
        }
    }
    # Validate the entire subject before exposing any attribute for matching.
    return $attributes.ToArray()
}

function Test-SubjectMatch {
    [CmdletBinding()]
    param(
        [string] $Subject,
        [string] $ExpectedPublisher
    )
    if ([string]::IsNullOrWhiteSpace($Subject) -or [string]::IsNullOrWhiteSpace($ExpectedPublisher)) {
        return $false
    }

    $target = $ExpectedPublisher.Trim()
    if ($target.Length -ge 2) {
        if (($target.StartsWith('"') -and $target.EndsWith('"')) -or
            ($target.StartsWith("'") -and $target.EndsWith("'"))) {
            $target = $target.Substring(1, $target.Length - 2)
        }
    }

    try {
        $attributes = @(ConvertFrom-DistinguishedName -Subject $Subject)
    } catch {
        return $false
    }
    foreach ($attribute in $attributes) {
        if ($attribute.Name -in @('CN', 'O') -and
            $attribute.Value.Equals($target, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Resolve-Configuration {
    [CmdletBinding()]
    param(
        [string] $ConfigPath,
        [System.Collections.IDictionary] $Overrides = @{}
    )
    $config = Get-DefaultConfiguration

    if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
        if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
            throw "Configuration file not found: '$ConfigPath'."
        }
        $raw = Get-Content -LiteralPath $ConfigPath -Raw -ErrorAction Stop
        try {
            $fileConfig = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
        } catch {
            throw "Failed to parse configuration file '$ConfigPath' as JSON: $($_.Exception.Message)"
        }

        foreach ($prop in $fileConfig.PSObject.Properties) {
            $name = $prop.Name
            if ($name -like '_comment*') { continue }
            if (-not $config.Contains($name)) {
                throw "Unknown configuration property in '$ConfigPath': '$name'."
            }
            if ($null -ne $prop.Value) {
                $config[$name] = [string]$prop.Value
            }
        }
    }

    if ($null -ne $Overrides) {
        foreach ($key in $Overrides.Keys) {
            $val = $Overrides[$key]
            if ($null -eq $val) { continue }
            if ($key -like '_comment*') { continue }
            if ($key -in @('ConfigPath', 'Help', 'OutputPath', 'Force', 'WhatIf', 'Confirm')) { continue }
            if ($key -eq 'AsJson') {
                if (($val -is [bool] -and $val) -or ($val -is [System.Management.Automation.SwitchParameter] -and $val.IsPresent)) {
                    $config['Format'] = 'Json'
                }
                continue
            }
            if (-not $config.Contains($key)) {
                throw "Unknown parameter override: '$key'."
            }
            if ($val -is [string]) {
                if (-not [string]::IsNullOrWhiteSpace($val)) {
                    $config[$key] = $val
                }
            } else {
                $config[$key] = [string]$val
            }
        }
    }

    # Normalize Format
    if ([string]::IsNullOrWhiteSpace($config['Format'])) {
        $config['Format'] = 'Table'
    } elseif ($config['Format'] -ieq 'Json') {
        $config['Format'] = 'Json'
    } elseif ($config['Format'] -ieq 'Table') {
        $config['Format'] = 'Table'
    } else {
        throw "Invalid Format '$($config['Format'])'. Allowed values are 'Table' and 'Json'."
    }

    # Validate ExpectedSHA256 if present
    if (-not [string]::IsNullOrWhiteSpace($config['ExpectedSHA256'])) {
        $hashNorm = $config['ExpectedSHA256'].Trim().Replace('-', '').Replace(' ', '')
        if ($hashNorm -notmatch '^[A-Fa-f0-9]{64}$') {
            throw "ExpectedSHA256 must be a 64-character hexadecimal SHA-256 string."
        }
        $config['ExpectedSHA256'] = $hashNorm
    }

    return [pscustomobject]$config
}

function Get-InstallerSignature {
    [CmdletBinding()]
    param([string] $InstallerPath)

    return Get-AuthenticodeSignature -LiteralPath $InstallerPath -ErrorAction Stop
}

function Invoke-InstallerSignatureCheck {
    [CmdletBinding()]
    param(
        [string] $InstallerPath,
        [string] $ExpectedPublisher,
        [string] $ExpectedSHA256,
        [string] $ExpectedComputer,
        [ValidateSet('Table', 'Json')]
        [string] $Format = 'Table'
    )

    if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
        throw "InstallerPath must be provided."
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedPublisher)) {
        throw "ExpectedPublisher must be provided."
    }

    $currentHost = Get-RealComputerName
    if (-not [string]::IsNullOrWhiteSpace($ExpectedComputer)) {
        if (-not (Test-ComputerMatch -ActualComputer $currentHost -ExpectedComputer $ExpectedComputer)) {
            throw "Host mismatch: current host '$currentHost' does not match expected computer '$ExpectedComputer'."
        }
    }

    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "Installer file not found at path: '$InstallerPath'."
    }

    $file = Get-Item -LiteralPath $InstallerPath

    $signature = Get-InstallerSignature -InstallerPath $InstallerPath

    if ($null -eq $signature -or $signature.Status -ne 'Valid') {
        $statusStr = if ($null -ne $signature) { $signature.Status.ToString() } else { 'Unknown' }
        throw "Installer signature is not valid: $statusStr."
    }

    if ($null -eq $signature.SignerCertificate -or [string]::IsNullOrWhiteSpace($signature.SignerCertificate.Subject)) {
        throw "Signer certificate or subject is missing from Authenticode signature."
    }

    $subject = $signature.SignerCertificate.Subject
    if (-not (Test-SubjectMatch -Subject $subject -ExpectedPublisher $ExpectedPublisher)) {
        throw "Signer certificate subject '$subject' does not match expected publisher '$ExpectedPublisher'."
    }

    $actualHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSHA256)) {
        if (-not (Test-HashMatch -ActualHash $actualHash -ExpectedHash $ExpectedSHA256)) {
            throw "File SHA-256 hash '$actualHash' does not match expected hash '$ExpectedSHA256'."
        }
    }

    return [pscustomobject]@{
        Computer  = $currentHost
        Path      = $file.FullName
        Bytes     = $file.Length
        SHA256    = $actualHash
        Signature = $signature.Status.ToString()
        Publisher = $signature.SignerCertificate.Subject
    }
}

Set-Alias -Name Test-InstallerSignature -Value Invoke-InstallerSignatureCheck

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    if ($Help) {
        Write-Output 'installer-signature-check.ps1 [-ConfigPath PATH] [-InstallerPath PATH] [-ExpectedPublisher NAME] [-ExpectedSHA256 HASH] [-ExpectedComputer NAME] [-Format Table|Json] [-AsJson] [-Help]'
        exit 0
    }

    try {
        $overrides = @{}
        foreach ($key in @('InstallerPath', 'ExpectedPublisher', 'ExpectedSHA256', 'ExpectedComputer', 'Format', 'AsJson')) {
            if ($PSBoundParameters.ContainsKey($key)) {
                $overrides[$key] = $PSBoundParameters[$key]
            }
        }
        $config = Resolve-Configuration -ConfigPath $ConfigPath -Overrides $overrides

        if ([string]::IsNullOrWhiteSpace($config.InstallerPath)) {
            throw "InstallerPath is required. Provide it via config or -InstallerPath parameter."
        }
        if ([string]::IsNullOrWhiteSpace($config.ExpectedPublisher)) {
            throw "ExpectedPublisher is required. Provide it via config or -ExpectedPublisher parameter."
        }
    } catch {
        [Console]::Error.WriteLine("error: $($_.Exception.Message)")
        exit 2
    }

    try {
        $report = Invoke-InstallerSignatureCheck `
            -InstallerPath $config.InstallerPath `
            -ExpectedPublisher $config.ExpectedPublisher `
            -ExpectedSHA256 $config.ExpectedSHA256 `
            -ExpectedComputer $config.ExpectedComputer `
            -Format $config.Format

        if ($config.Format -eq 'Json') {
            $report | ConvertTo-Json -Depth 4
        } else {
            $report | Format-Table -AutoSize | Out-String | ForEach-Object { $_.TrimEnd() }
        }
        exit 0
    } catch {
        [Console]::Error.WriteLine("FAIL: $($_.Exception.Message)")
        exit 1
    }
}
