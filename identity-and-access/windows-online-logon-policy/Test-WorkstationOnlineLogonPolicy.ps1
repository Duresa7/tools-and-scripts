[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string]$Domain,
    [string]$Server,
    [string]$TargetOU,
    [string]$GpoName
)

. (Join-Path $PSScriptRoot 'OnlineLogon.Common.ps1')

function Get-OnlineLogonWorkstationState {
    param([hashtable]$Settings)

    $ErrorActionPreference = 'Stop'
    $computer = Get-CimInstance Win32_ComputerSystem
    $values = @{}
    foreach ($setting in Get-OnlineLogonRegistrySettings) {
        $path = $setting.Key -replace '^HKLM\\', 'HKLM:\'
        try {
            $property = Get-ItemProperty -LiteralPath $path
            $values[$setting.ValueName] = $property.($setting.ValueName)
        } catch [System.Management.Automation.ItemNotFoundException] {
            $values[$setting.ValueName] = $null
        }
    }
    # Failure to read Netlogon must not be mistaken for the default value.
    $netlogon = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters'
    $values.DisablePasswordChange = $netlogon.DisablePasswordChange
    $registry = Test-OnlineLogonRegistry -Values $values
    $checks = $registry.Checks
    $checks.PartOfDomain = $computer.PartOfDomain -eq $true
    $checks.ExpectedDomain = $computer.Domain -eq $Settings.Domain
    $checks.SecureChannel = (Test-ComputerSecureChannel -Server $Settings.Server) -eq $true
    $checks.NetlogonAutomatic = (Get-Service Netlogon).StartType -eq 'Automatic'
    $checks.PolicyApplied = @(Get-CimInstance -Namespace 'root\rsop\computer' -ClassName RSOP_GPO | Where-Object name -eq $Settings.GpoName).Count -eq 1
    [pscustomobject]@{
        ReadAt = (Get-Date).ToString('o')
        Computer = $env:COMPUTERNAME
        Domain = $computer.Domain
        Policy = $Settings.GpoName
        Registry = $values
        Checks = $checks
        Pass = @($checks.Values | Where-Object { -not $_ }).Count -eq 0
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        $settings = Read-OnlineLogonConfig -Path $ConfigPath -Overrides $PSBoundParameters -AllowMissing:(-not $PSBoundParameters.ContainsKey('ConfigPath'))
        $result = Get-OnlineLogonWorkstationState -Settings $settings
        $result | ConvertTo-Json -Depth 6 -Compress
        if (-not $result.Pass) { exit 1 }
        exit 0
    } catch {
        [pscustomobject]@{ Pass = $false; Error = $_.Exception.Message } | ConvertTo-Json -Compress
        exit 1
    }
}
