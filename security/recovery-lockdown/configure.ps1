<#
.SYNOPSIS
Create config.local.json for Windows recovery lockdown without changing Windows.
.DESCRIPTION
Provide settings as parameters or edit the annotated output. The output parent
must exist. Existing output is always refused, including with -Force.
.EXAMPLE
.\configure.ps1 -TaskName 'Windows Recovery Guard' -DailyAt '04:15'
#>
[CmdletBinding()]
param(
    [string] $OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string] $ConfigPath = (Join-Path $PSScriptRoot 'config.example.json'),
    [string] $TaskName, [string] $TaskPath, [string] $InstallRoot,
    [string] $ScriptName, [string] $LogName, [string] $DailyAt,
    [int] $StartupDelaySeconds, [int] $VerificationTimeoutSeconds,
    [string] $ReagentcPath, [string] $PowerShellPath
)

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $ErrorActionPreference = 'Stop'
        $arguments = @{} + $PSBoundParameters
        # Dot-source in a child scope so its parameter block cannot reset inputs.
        $payload = & {
            param($InputConfig, $InputArguments)
            . (Join-Path $PSScriptRoot 'recovery-lockdown.ps1')
            $local = Read-RecoveryConfig $InputConfig
            $overrides = @{}
            foreach ($key in (Get-RecoveryDefaults).Keys) {
                if ($InputArguments.ContainsKey($key)) { $overrides[$key] = $InputArguments[$key] }
            }
            $resolved = Merge-RecoveryConfig $local $overrides
            foreach ($key in $local.Keys) {
                if ($key -like '_comment*') { $resolved[$key] = $local[$key] }
            }
            return $resolved
        } $ConfigPath $arguments
        $json = ($payload | ConvertTo-Json -Depth 4) + [Environment]::NewLine
        # CreateNew closes the race between checking existence and writing.
        $stream = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally { $stream.Dispose() }
        Write-Output "configuration-written: $OutputPath"
        Write-Output 'next-step: review every CUSTOMIZE marker, then preview installation with -WhatIf.'
    } catch {
        [Console]::Error.WriteLine("error: unable to create configuration; existing files are never replaced. $($_.Exception.Message)")
        exit 1
    }
}
