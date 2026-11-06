<#
.SYNOPSIS
Create a local installer signature check configuration without replacing an existing file.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string] $InstallerPath,
    [string] $ExpectedPublisher,
    [string] $ExpectedSHA256,
    [string] $ExpectedComputer,
    [string] $Format,
    [switch] $Force,
    [switch] $Help
)

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if ($Help) {
            Write-Output 'configure.ps1 [-OutputPath PATH] [-InstallerPath PATH] [-ExpectedPublisher NAME] [-ExpectedSHA256 HASH] [-ExpectedComputer NAME] [-Format Table|Json] [-Force] [-WhatIf]. Refuses to overwrite.'
            exit 0
        }
        $overrides = @{}
        foreach ($key in @('InstallerPath', 'ExpectedPublisher', 'ExpectedSHA256', 'ExpectedComputer', 'Format')) {
            if ($PSBoundParameters.ContainsKey($key)) {
                $overrides[$key] = $PSBoundParameters[$key]
            }
        }
        . (Join-Path $PSScriptRoot 'installer-signature-check.ps1')
        $example = Join-Path $PSScriptRoot 'config.example.json'
        if (-not (Test-Path -LiteralPath $example -PathType Leaf)) {
            throw "Example configuration file not found at '$example'."
        }
        $resolved = Resolve-Configuration -ConfigPath $example -Overrides $overrides
        $payload = Get-Content -LiteralPath $example -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        foreach ($property in $resolved.PSObject.Properties) {
            $payload.($property.Name) = $property.Value
        }
        if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
            throw "Refusing to replace existing configuration at '$OutputPath'. Use -Force to overwrite."
        }
        if ($PSCmdlet.ShouldProcess($OutputPath, 'Create local configuration')) {
            $parent = Split-Path -Parent $OutputPath
            if ($parent -and -not (Test-Path -LiteralPath $parent)) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
            $mode = if ($Force) { [System.IO.FileMode]::Create } else { [System.IO.FileMode]::CreateNew }
            $stream = [System.IO.File]::Open($OutputPath, $mode, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try {
                $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(($payload | ConvertTo-Json -Depth 4))
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally {
                $stream.Dispose()
            }
            Write-Output "Configuration written: $OutputPath. Review every CUSTOMIZE: marker before running check."
        }
        exit 0
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
