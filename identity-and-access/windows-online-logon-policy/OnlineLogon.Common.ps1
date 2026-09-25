function Get-ExcludedCredentialProviders {
    @(
        '{cb82ea12-9f71-446d-89e1-8d0924e1256e}'
        '{D6886603-9D2F-4EB2-B667-1971041FA96B}'
        '{8AF662BF-65A0-4D0A-A540-A338A999D36F}'
        '{BEC09223-B018-416D-A0AC-523971B639F5}'
        '{2135f72a-90b5-4ed3-a7f1-8bb705ac276a}'
        '{F8A1793B-7873-4046-B2A7-1F318747F427}'
    ) -join ','
}

function Get-OnlineLogonRegistrySettings {
    $winlogon = 'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    @(
        @{ Key = $winlogon; ValueName = 'CachedLogonsCount'; Type = 'String'; Value = '0' }
        @{ Key = $winlogon; ValueName = 'ForceUnlockLogon'; Type = 'DWord'; Value = 1 }
        @{ Key = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\CurrentVersion\Winlogon'; ValueName = 'SyncForegroundPolicy'; Type = 'DWord'; Value = 1 }
        @{ Key = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; ValueName = 'ExcludedCredentialProviders'; Type = 'String'; Value = (Get-ExcludedCredentialProviders) }
        @{ Key = 'HKLM\SOFTWARE\Policies\Microsoft\PassportForWork'; ValueName = 'Enabled'; Type = 'DWord'; Value = 0 }
        @{ Key = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; ValueName = 'AllowDomainPINLogon'; Type = 'DWord'; Value = 0 }
        @{ Key = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; ValueName = 'BlockDomainPicturePassword'; Type = 'DWord'; Value = 1 }
    )
}

function Merge-OnlineLogonConfig {
    param([hashtable]$Config = @{}, [System.Collections.IDictionary]$Overrides = @{})

    $fields = @('Domain', 'Server', 'TargetOU', 'GpoName')
    foreach ($key in $Config.Keys) {
        if ($key -notin $fields -and $key -notlike '_comment*') {
            throw "Unknown configuration field: $key"
        }
    }
    $resolved = @{}
    foreach ($field in $fields) {
        $value = $Config[$field]
        if ($field -in $Overrides.Keys) { $value = $Overrides[$field] }
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value) -or $value -match '[\r\n]' -or $value -match 'CUSTOMIZE:') {
            throw "$field must be a nonempty configured string."
        }
        $resolved[$field] = $value.Trim()
    }
    foreach ($field in @('Domain', 'Server')) {
        if ($resolved[$field] -notmatch '^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$') {
            throw "$field must be a DNS name."
        }
    }
    $domainDN = ($resolved.Domain.Split('.') | ForEach-Object { "DC=$_" }) -join ','
    if ($resolved.TargetOU -notmatch '^OU=.+,' -or -not $resolved.TargetOU.EndsWith(
        ",$domainDN", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'TargetOU must be an OU distinguished name inside Domain.'
    }
    return $resolved
}

function Read-OnlineLogonConfig {
    param([string]$Path, [System.Collections.IDictionary]$Overrides = @{}, [switch]$AllowMissing)

    $config = @{}
    if (Test-Path -LiteralPath $Path) {
        $payload = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $payload -or $payload -isnot [pscustomobject]) {
            throw 'Configuration must be a JSON object.'
        }
        foreach ($property in $payload.PSObject.Properties) { $config[$property.Name] = $property.Value }
    } elseif (-not $AllowMissing) {
        throw 'Configuration file does not exist.'
    }
    Merge-OnlineLogonConfig -Config $config -Overrides $Overrides
}

function Test-OnlineLogonRegistry {
    param([hashtable]$Values)

    $providers = @(([string]$Values.ExcludedCredentialProviders -split ',') | ForEach-Object { $_.Trim() })
    $expected = (Get-ExcludedCredentialProviders) -split ','
    $checks = [ordered]@{
        CachedLogonsDisabled = $null -ne $Values.CachedLogonsCount -and [string]$Values.CachedLogonsCount -eq '0'
        OnlineUnlockRequired = $null -ne $Values.ForceUnlockLogon -and $Values.ForceUnlockLogon -eq 1
        ForegroundNetworkWait = $null -ne $Values.SyncForegroundPolicy -and $Values.SyncForegroundPolicy -eq 1
        AlternativeProvidersExcluded = @($expected | Where-Object { $_ -notin $providers }).Count -eq 0
        PasswordProviderAvailable = '{60b78e88-ead8-445c-9cfd-0b87f74ea6cd}' -notin $providers
        HelloProvisioningDisabled = $null -ne $Values.Enabled -and $Values.Enabled -eq 0
        ConveniencePinDisabled = $null -ne $Values.AllowDomainPINLogon -and $Values.AllowDomainPINLogon -eq 0
        PicturePasswordDisabled = $null -ne $Values.BlockDomainPicturePassword -and $Values.BlockDomainPicturePassword -eq 1
        # An absent DisablePasswordChange uses the Windows default: changes enabled.
        MachinePasswordChangesEnabled = $null -eq $Values.DisablePasswordChange -or $Values.DisablePasswordChange -eq 0
    }
    [pscustomobject]@{ Checks = $checks; Pass = @($checks.Values | Where-Object { -not $_ }).Count -eq 0 }
}
