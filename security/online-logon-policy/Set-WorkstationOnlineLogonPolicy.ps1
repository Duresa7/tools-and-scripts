[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.local.json'),
    [string]$Domain,
    [string]$Server,
    [string]$TargetOU,
    [string]$GpoName,
    [string]$ConfirmationPhrase
)

. (Join-Path $PSScriptRoot 'OnlineLogon.Common.ps1')

function Invoke-SetOnlineLogonPolicy {
    [CmdletBinding(SupportsShouldProcess)]
    param([hashtable]$Settings, [string]$ConfirmationPhrase)

    $ErrorActionPreference = 'Stop'
    $Settings = Merge-OnlineLogonConfig -Config $Settings
    if (-not $WhatIfPreference -and $ConfirmationPhrase -cne 'REQUIRE ONLINE DOMAIN SIGN-IN') {
        throw 'Refusing changes. Supply -ConfirmationPhrase ''REQUIRE ONLINE DOMAIN SIGN-IN'' after testing local recovery access, or use -WhatIf.'
    }
    if (-not $PSCmdlet.ShouldProcess(
        "$($Settings.Domain): $($Settings.TargetOU)",
        "Apply '$($Settings.GpoName)': disable cached sign-in and alternative credential providers")) {
        return
    }

    Import-Module GroupPolicy -ErrorAction Stop
    $gpoArgs = @{ Domain = $Settings.Domain; Server = $Settings.Server }
    # Resolve the scope before creating or editing any policy.
    $inheritance = Get-GPInheritance -Target $Settings.TargetOU @gpoArgs
    $matches = @(Get-GPO -All @gpoArgs | Where-Object DisplayName -eq $Settings.GpoName)
    if ($matches.Count -gt 1) { throw 'More than one GPO has the configured name; no changes made.' }
    $gpo = $matches | Select-Object -First 1
    if (-not $gpo) {
        $gpo = New-GPO -Name $Settings.GpoName -Comment 'Domain workstation sign-in requires a reachable domain controller. Local recovery accounts remain available.' @gpoArgs
    }
    $gpo.GpoStatus = 'UserSettingsDisabled'
    foreach ($setting in Get-OnlineLogonRegistrySettings) {
        Set-GPRegistryValue -Guid $gpo.Id @setting @gpoArgs | Out-Null
    }
    $link = @($inheritance.GpoLinks | Where-Object GpoId -eq $gpo.Id)
    if ($link.Count -gt 0) {
        Set-GPLink -Guid $gpo.Id -Target $Settings.TargetOU -LinkEnabled Yes @gpoArgs | Out-Null
    } else {
        New-GPLink -Guid $gpo.Id -Target $Settings.TargetOU -LinkEnabled Yes @gpoArgs | Out-Null
    }

    $readback = @(foreach ($setting in Get-OnlineLogonRegistrySettings) {
        $actual = Get-GPRegistryValue -Guid $gpo.Id -Key $setting.Key -ValueName $setting.ValueName @gpoArgs
        if ([string]$actual.Type -ne $setting.Type -or [string]$actual.Value -cne [string]$setting.Value) {
            throw "GPO readback failed for $($setting.ValueName). Changes may be partially applied."
        }
        [pscustomobject]@{ Key = $setting.Key; ValueName = $setting.ValueName; Type = [string]$actual.Type; Value = $actual.Value }
    })
    $verified = Get-GPO -Guid $gpo.Id @gpoArgs
    $links = @((Get-GPInheritance -Target $Settings.TargetOU @gpoArgs).GpoLinks | Where-Object { $_.GpoId -eq $gpo.Id -and $_.Enabled -eq $true })
    if ([string]$verified.GpoStatus -ne 'UserSettingsDisabled' -or $links.Count -ne 1) {
        throw 'GPO status or enabled link readback failed. Changes may be partially applied.'
    }
    [pscustomobject]@{ Policy = $Settings.GpoName; TargetOU = $Settings.TargetOU; Registry = $readback; Pass = $true }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        $settings = Read-OnlineLogonConfig -Path $ConfigPath -Overrides $PSBoundParameters -AllowMissing:(-not $PSBoundParameters.ContainsKey('ConfigPath'))
        $arguments = @{ Settings = $settings; ConfirmationPhrase = $ConfirmationPhrase; WhatIf = $WhatIfPreference }
        if ($PSBoundParameters.ContainsKey('Confirm')) { $arguments.Confirm = $PSBoundParameters.Confirm }
        $result = Invoke-SetOnlineLogonPolicy @arguments
        if ($null -ne $result) { $result | ConvertTo-Json -Depth 6 }
        exit 0
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
}
