# Read-only TCP probes. Open ports do not prove credentials, permissions, or application health.
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string[]]$Target,
    [int[]]$Port,
    [string]$Label,
    [Alias('ExpectedComputerName')][string]$ExpectedSource,
    [Alias('Timeout')][double]$TimeoutSeconds = 0,
    [Alias('OutputFormat')][string]$Format,
    [switch]$Json
)

function Get-CurrentComputerName {
    try {
        return [System.Net.Dns]::GetHostName()
    } catch {
        try {
            return [Environment]::MachineName
        } catch {
            return 'localhost'
        }
    }
}

function Resolve-CliTargets {
    param(
        [string[]]$Targets,
        [int[]]$Ports,
        [string]$Label
    )

    $resolved = @()

    foreach ($entry in $Targets) {
        $trimmed = $entry.Trim()
        if ([string]::IsNullOrEmpty($trimmed)) {
            continue
        }

        # Check for [IPv6]:Port
        if ($trimmed -match '^\[([a-fA-F0-9:]+)\]:(\d+)$') {
            $hostPart = $matches[1]
            $portPart = [int]$matches[2]
            if ($portPart -lt 1 -or $portPart -gt 65535) {
                throw "Invalid port '$portPart' for target '$trimmed'. Port must be between 1 and 65535."
            }
            $resolved += [pscustomobject]@{
                Target = $hostPart
                Port   = $portPart
                Label  = if ($Label) { $Label } else { $null }
            }
            continue
        }

        # Check for [IPv6] without port
        if ($trimmed -match '^\[([a-fA-F0-9:]+)\]$') {
            $hostPart = $matches[1]
            if (-not $Ports -or $Ports.Count -eq 0) {
                throw "Port must be specified for target '$trimmed'."
            }
            foreach ($p in $Ports) {
                if ($p -lt 1 -or $p -gt 65535) {
                    throw "Invalid port '$p' for target '$trimmed'. Port must be between 1 and 65535."
                }
                $resolved += [pscustomobject]@{
                    Target = $hostPart
                    Port   = $p
                    Label  = if ($Label) { $Label } else { $null }
                }
            }
            continue
        }

        # Check for host:port (IPv4 or hostname with single colon)
        if ($trimmed.Contains(':') -and ($trimmed.IndexOf(':') -eq $trimmed.LastIndexOf(':'))) {
            $parts = $trimmed.Split(':')
            $hostPart = $parts[0]
            $portVal = 0
            if (-not [int]::TryParse($parts[1], [ref]$portVal) -or $portVal -lt 1 -or $portVal -gt 65535) {
                throw "Invalid port '$($parts[1])' for target '$trimmed'. Port must be between 1 and 65535."
            }
            if ([string]::IsNullOrEmpty($hostPart)) {
                throw "Target host cannot be empty in '$trimmed'."
            }
            $resolved += [pscustomobject]@{
                Target = $hostPart
                Port   = $portVal
                Label  = if ($Label) { $Label } else { $null }
            }
            continue
        }

        # Target without embedded port: requires $Ports
        if (-not $Ports -or $Ports.Count -eq 0) {
            throw "Port must be specified for target '$trimmed'."
        }
        foreach ($p in $Ports) {
            if ($p -lt 1 -or $p -gt 65535) {
                throw "Invalid port '$p' for target '$trimmed'. Port must be between 1 and 65535."
            }
            $resolved += [pscustomobject]@{
                Target = $trimmed
                Port   = $p
                Label  = if ($Label) { $Label } else { $null }
            }
        }
    }

    return $resolved
}

function Resolve-ConfigTargets {
    param($ConfigTargets)

    if (-not $ConfigTargets) {
        throw "No targets specified in configuration."
    }

    $resolved = @()
    foreach ($item in @($ConfigTargets)) {
        if ($null -eq $item) {
            continue
        }

        $targetHost = if ($item.PSObject.Properties['Target']) {
            $item.Target
        } elseif ($item.PSObject.Properties['target']) {
            $item.target
        } else {
            $null
        }

        $targetPortRaw = if ($item.PSObject.Properties['Port']) {
            $item.Port
        } elseif ($item.PSObject.Properties['port']) {
            $item.port
        } else {
            $null
        }

        $targetLabel = if ($item.PSObject.Properties['Label']) {
            $item.Label
        } elseif ($item.PSObject.Properties['label']) {
            $item.label
        } else {
            $null
        }

        if ([string]::IsNullOrWhiteSpace($targetHost)) {
            throw "Configuration contains a target with an empty or missing 'Target' field."
        }

        if ($null -eq $targetPortRaw) {
            throw "Configuration target '$targetHost' is missing required 'Port' field."
        }

        $targetPort = 0
        if (-not [int]::TryParse([string]$targetPortRaw, [ref]$targetPort) -or $targetPort -lt 1 -or $targetPort -gt 65535) {
            throw "Invalid port '$targetPortRaw' for target '$targetHost'. Port must be between 1 and 65535."
        }

        $resolved += [pscustomobject]@{
            Target = [string]$targetHost
            Port   = $targetPort
            Label  = if ($targetLabel) { [string]$targetLabel } else { $null }
        }
    }

    if ($resolved.Count -eq 0) {
        throw "Configuration target list is empty. Provide at least one target."
    }

    return $resolved
}

function Test-TcpTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][int]$Port,
        [double]$TimeoutSeconds = 3.0
    )

    $client = New-Object System.Net.Sockets.TcpClient
    $connected = $false
    $pending = $null
    $timeoutMs = [int]($TimeoutSeconds * 1000)
    if ($timeoutMs -lt 1) {
        $timeoutMs = 1
    }

    try {
        $cleanTarget = $Target.Trim('[', ']')
        $pending = $client.BeginConnect($cleanTarget, $Port, $null, $null)
        if ($pending.AsyncWaitHandle.WaitOne($timeoutMs)) {
            $client.EndConnect($pending)
            $connected = $client.Connected
        }
    } catch {
        $connected = $false
    } finally {
        $client.Dispose()
        if ($null -ne $pending) {
            $pending.AsyncWaitHandle.Close()
        }
    }

    return $connected
}

function Invoke-TcpReachabilityCheck {
    param(
        [Parameter(Mandatory = $true)][array]$Targets,
        [double]$TimeoutSeconds = 3.0,
        [string]$ExpectedSource = $null
    )

    $currentComputer = Get-CurrentComputerName
    if (-not [string]::IsNullOrEmpty($ExpectedSource)) {
        $validSources = @()
        try {
            $dnsHost = [System.Net.Dns]::GetHostName()
            if (-not [string]::IsNullOrEmpty($dnsHost)) {
                $validSources += $dnsHost
            }
        } catch {
        }

        $onWindows = $false
        if ($null -ne $IsWindows) {
            $onWindows = [bool]$IsWindows
        } elseif ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
            $onWindows = $true
        }

        if ($onWindows) {
            try {
                $machName = [Environment]::MachineName
                if (-not [string]::IsNullOrEmpty($machName) -and $validSources -notcontains $machName) {
                    $validSources += $machName
                }
            } catch {
            }
        }

        if ($validSources.Count -eq 0) {
            $validSources += $currentComputer
        }

        $matched = $false
        foreach ($name in $validSources) {
            if ($name -eq $ExpectedSource) {
                $matched = $true
                break
            }
        }

        if (-not $matched) {
            throw "Current computer name '$currentComputer' does not match expected source '$ExpectedSource'."
        }
    }

    $results = @()
    foreach ($t in $Targets) {
        $connected = Test-TcpTarget -Target $t.Target -Port $t.Port -TimeoutSeconds $TimeoutSeconds
        $results += [pscustomobject]@{
            Source    = $currentComputer
            Target    = $t.Target
            Port      = $t.Port
            Connected = $connected
            Label     = $t.Label
        }
    }

    return $results
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $config = $null
        if ($PSBoundParameters.ContainsKey('ConfigPath')) {
            if (-not (Test-Path -LiteralPath $ConfigPath)) {
                throw "Configuration file not found: $ConfigPath"
            }
            try {
                $config = Get-Content -LiteralPath $ConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json
            } catch {
                throw "Failed to parse configuration file: $($_.Exception.Message)"
            }
        } else {
            $defaultConfig = Join-Path $PSScriptRoot 'config.local.json'
            if (Test-Path -LiteralPath $defaultConfig) {
                try {
                    $config = Get-Content -LiteralPath $defaultConfig -Raw -ErrorAction Stop | ConvertFrom-Json
                } catch {
                    throw "Failed to parse configuration file: $($_.Exception.Message)"
                }
            }
        }

        $resolvedExpectedSource = if ($PSBoundParameters.ContainsKey('ExpectedSource')) {
            $ExpectedSource
        } elseif ($config -and $config.PSObject.Properties['ExpectedSource'] -and -not [string]::IsNullOrEmpty($config.ExpectedSource)) {
            $config.ExpectedSource
        } elseif ($config -and $config.PSObject.Properties['ExpectedComputerName'] -and -not [string]::IsNullOrEmpty($config.ExpectedComputerName)) {
            $config.ExpectedComputerName
        } else {
            $null
        }

        $resolvedTimeout = if ($PSBoundParameters.ContainsKey('TimeoutSeconds') -or $PSBoundParameters.ContainsKey('Timeout')) {
            $TimeoutSeconds
        } elseif ($config -and $config.PSObject.Properties['TimeoutSeconds']) {
            [double]$config.TimeoutSeconds
        } elseif ($config -and $config.PSObject.Properties['Timeout']) {
            [double]$config.Timeout
        } else {
            3.0
        }

        if ($resolvedTimeout -le 0) {
            throw "Invalid timeout '$resolvedTimeout'. Timeout must be greater than zero."
        }

        $resolvedFormat = if ($Json.IsPresent) {
            'Json'
        } elseif ($PSBoundParameters.ContainsKey('Format') -or $PSBoundParameters.ContainsKey('OutputFormat')) {
            $Format
        } elseif ($config -and $config.PSObject.Properties['Format']) {
            [string]$config.Format
        } elseif ($config -and $config.PSObject.Properties['OutputFormat']) {
            [string]$config.OutputFormat
        } else {
            'Table'
        }

        if ($resolvedFormat -ne 'Table' -and $resolvedFormat -ne 'Json') {
            throw "Invalid output format '$resolvedFormat'. Supported formats are 'Table' and 'Json'."
        }

        $resolvedTargets = @()
        if ($PSBoundParameters.ContainsKey('Target')) {
            $resolvedTargets = Resolve-CliTargets -Targets $Target -Ports $Port -Label $Label
        } elseif ($PSBoundParameters.ContainsKey('Port')) {
            throw "Target host must be specified when supplying -Port."
        } elseif ($config -and $config.PSObject.Properties['Targets']) {
            $resolvedTargets = Resolve-ConfigTargets -ConfigTargets $config.Targets
        } else {
            throw "No targets specified. Provide targets via command-line or configuration file."
        }

        if ($resolvedTargets.Count -eq 0) {
            throw "No targets specified. Provide targets via command-line or configuration file."
        }

        $results = Invoke-TcpReachabilityCheck -Targets $resolvedTargets `
            -TimeoutSeconds $resolvedTimeout `
            -ExpectedSource $resolvedExpectedSource

        if ($resolvedFormat -eq 'Json') {
            Write-Output (ConvertTo-Json -InputObject @($results) -Depth 5)
        } else {
            $results | Format-Table -Property Source, Target, Port, Connected, Label | Out-String | Write-Output
        }

        $anyFailed = $false
        foreach ($r in $results) {
            if (-not $r.Connected) {
                $anyFailed = $true
                break
            }
        }

        if ($anyFailed) {
            exit 1
        } else {
            exit 0
        }
    } catch {
        [Console]::Error.WriteLine("Error: " + $_.Exception.Message)
        exit 2
    }
}
