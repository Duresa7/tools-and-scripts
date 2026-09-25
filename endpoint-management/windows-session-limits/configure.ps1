<#
.SYNOPSIS
Create a local session policy configuration without replacing an existing file.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string] $TargetGroup,
    [string] $WindowStart,
    [string] $WindowEnd,
    [int] $DailyBudgetMinutes,
    [int[]] $WarningMinutes,
    [string] $StatePath,
    [string] $TaskName,
    [switch] $Help
)
if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if ($Help) { Write-Output 'configure.ps1 [-OutputPath PATH] [-TargetGroup NAME] [-WindowStart HH:mm] [-WindowEnd HH:mm] [-DailyBudgetMinutes N] [-WarningMinutes N,N] [-StatePath PATH] [-TaskName NAME] [-WhatIf]. Refuses to overwrite.'; exit 0 }
        $overrides = @{} + $PSBoundParameters
        . (Join-Path $PSScriptRoot 'session-limits.ps1')
        $example = Join-Path $PSScriptRoot 'config.example.json'
        $resolved = Resolve-Configuration $example $overrides
        $payload = Get-Content -LiteralPath $example -Raw | ConvertFrom-Json
        foreach ($property in $resolved.PSObject.Properties) { $payload.($property.Name) = $property.Value }
        if (Test-Path -LiteralPath $OutputPath) { throw 'Refusing to replace existing configuration.' }
        if ($PSCmdlet.ShouldProcess($OutputPath, 'Create local configuration')) {
            $stream = [IO.File]::Open($OutputPath, 'CreateNew', 'Write', 'None')
            try {
                $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($payload | ConvertTo-Json -Depth 4))
                $stream.Write($bytes, 0, $bytes.Length)
                $stream.Flush()
            } finally { $stream.Dispose() }
            Write-Output "Configuration written: $OutputPath. Review every CUSTOMIZE: marker before install."
        }
        exit 0
    } catch { Write-Error $_ -ErrorAction Continue; exit 1 }
}
