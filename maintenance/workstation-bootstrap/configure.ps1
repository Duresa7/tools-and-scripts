<#
.SYNOPSIS
Create a local workstation bootstrap configuration without replacing an existing file.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string] $ComputerName,
    [string] $AuthorizedKey,
    [string] $AuthorizedKeyPath,
    [string] $AllowedRemoteAddresses,
    [string] $DomainDnsTestName,
    [switch] $Restart,
    [switch] $Force,
    [switch] $Help
)

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if ($Help) {
            Write-Output 'configure.ps1 [-OutputPath PATH] [-ComputerName NAME] [-AuthorizedKey KEY] [-AuthorizedKeyPath PATH] [-AllowedRemoteAddresses SCOPE] [-DomainDnsTestName NAME] [-Restart] [-Force] [-WhatIf]. Refuses to overwrite.'
            exit 0
        }
        $overrides = @{} + $PSBoundParameters
        . (Join-Path $PSScriptRoot 'bootstrap-workstation.ps1')
        $example = Join-Path $PSScriptRoot 'config.example.json'
        $resolved = Resolve-Configuration $example $overrides
        $payload = Get-Content -LiteralPath $example -Raw | ConvertFrom-Json
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
            $mode = if ($Force) { [IO.FileMode]::Create } else { [IO.FileMode]::CreateNew }
            $stream = [IO.File]::Open($OutputPath, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($payload | ConvertTo-Json -Depth 4))
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally {
                $stream.Dispose()
            }
            Write-Output "Configuration written: $OutputPath. Review every CUSTOMIZE: marker before running bootstrap."
        }
        exit 0
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
