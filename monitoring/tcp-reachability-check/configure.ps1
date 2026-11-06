[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string]$ExpectedSource = '',
    [double]$TimeoutSeconds = 3.0,
    [string]$Format = 'Table',
    [switch]$Overwrite
)

function Write-TcpReachabilityConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ExpectedSource = '',
        [double]$TimeoutSeconds = 3.0,
        [string]$Format = 'Table',
        [switch]$Overwrite
    )

    $resolvedPath = if ([System.IO.Path]::IsPathRooted($Path)) {
        $Path
    } else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
    }

    if ((Test-Path -LiteralPath $resolvedPath) -and (-not $Overwrite)) {
        throw "Refusing to replace existing configuration: $resolvedPath"
    }

    $examplePath = Join-Path $PSScriptRoot 'config.example.json'
    if (-not (Test-Path -LiteralPath $examplePath)) {
        throw "Template configuration not found: $examplePath"
    }

    $payload = Get-Content -LiteralPath $examplePath -Raw -ErrorAction Stop | ConvertFrom-Json

    if ($ExpectedSource) {
        $payload.ExpectedSource = $ExpectedSource
    }
    if ($TimeoutSeconds -gt 0) {
        $payload.TimeoutSeconds = $TimeoutSeconds
    }
    if ($Format) {
        $payload.Format = $Format
    }

    $parentDir = [System.IO.Path]::GetDirectoryName($resolvedPath)
    if ($parentDir -and (-not (Test-Path -LiteralPath $parentDir))) {
        [System.IO.Directory]::CreateDirectory($parentDir) | Out-Null
    }

    $json = ($payload | ConvertTo-Json -Depth 10) + [Environment]::NewLine
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)

    $mode = if ($Overwrite) {
        [System.IO.FileMode]::Create
    } else {
        [System.IO.FileMode]::CreateNew
    }

    # Exclusive creation prevents race conditions if destination appears
    $stream = [System.IO.File]::Open($resolvedPath, $mode, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
    } finally {
        $stream.Dispose()
    }

    return $resolvedPath
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        $writtenPath = Write-TcpReachabilityConfig -Path $OutputPath `
            -ExpectedSource $ExpectedSource `
            -TimeoutSeconds $TimeoutSeconds `
            -Format $Format `
            -Overwrite:$Overwrite
        Write-Output "Configuration written: $writtenPath"
        exit 0
    } catch {
        [Console]::Error.WriteLine("Error: " + $_.Exception.Message)
        exit 1
    }
}
