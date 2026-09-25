[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string]$Domain,
    [string]$Server,
    [string]$TargetOU,
    [string]$GpoName
)

. (Join-Path $PSScriptRoot 'OnlineLogon.Common.ps1')

function Write-OnlineLogonConfig {
    param([string]$Path, [hashtable]$Settings)

    $Settings = Merge-OnlineLogonConfig -Config $Settings
    $payload = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Raw -ErrorAction Stop | ConvertFrom-Json
    foreach ($key in $Settings.Keys) { $payload.$key = $Settings[$key] }
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($payload | ConvertTo-Json) + [Environment]::NewLine)
    # Exclusive creation also rejects a destination created after the early check.
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if (Test-Path -LiteralPath $OutputPath) { throw 'Refusing to replace existing configuration.' }
        $settings = @{}
        foreach ($field in @('Domain', 'Server', 'TargetOU', 'GpoName')) {
            if ($PSBoundParameters.ContainsKey($field)) {
                $settings[$field] = $PSBoundParameters[$field]
            } else {
                $settings[$field] = Read-Host "CUSTOMIZE: $field"
            }
        }
        Write-OnlineLogonConfig -Path $OutputPath -Settings $settings
        Write-Output "Configuration written: $OutputPath"
        exit 0
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
}
