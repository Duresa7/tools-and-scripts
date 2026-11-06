<#
.SYNOPSIS
Rename a fresh Windows workstation and configure a key-only OpenSSH server.
.DESCRIPTION
Configures OpenSSH server, restricts to key authentication, applies administrators_authorized_keys ACL,
configures firewall inbound rule, sets network profile to Private, renames computer, and verifies configuration.
Requires -ConfirmationPhrase 'BOOTSTRAP AND REBOOT' to perform rename and reboot.
Supports -WhatIf for dry-run preview. Dot-source to load pure functions.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $ConfigPath,
    [string] $ComputerName,
    [string] $AuthorizedKey,
    [string] $AuthorizedKeyPath,
    [string] $AllowedRemoteAddresses,
    [string] $DomainDnsTestName,
    [switch] $Restart,
    [string] $ConfirmationPhrase,
    [switch] $Help
)

function Test-ComputerName {
    param([string] $Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    if ($Name.Length -lt 1 -or $Name.Length -gt 15) { return $false }
    if ($Name -match '^\d+$') { return $false }
    if ($Name -notmatch '^[A-Za-z0-9-]+$') { return $false }
    return $true
}

function Assert-ValidComputerName {
    param([string] $Name)
    if (-not (Test-ComputerName $Name)) {
        throw "Invalid computer name '$Name'. Must be 1 to 15 alphanumeric characters or hyphens, and cannot consist entirely of digits."
    }
}

function Test-PublicKey {
    param([string] $KeyText)
    if ([string]::IsNullOrWhiteSpace($KeyText)) { return $false }
    if ($KeyText -match '(?i)BEGIN.*PRIVATE KEY|PRIVATE KEY|Proc-Type|PuTTY-User-Key-File') {
        return $false
    }
    $lines = $KeyText.Trim() -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    if ($lines.Count -eq 0) { return $false }
    $keyTypePattern = '^(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)$'
    $keyCount = 0
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith('#')) { continue }
        $parts = $trimmed -split '\s+', 3
        if ($parts.Count -lt 2) { return $false }
        if ($parts[0] -cnotmatch $keyTypePattern) { return $false }
        $b64 = $parts[1]
        if ($b64.Length -lt 20) { return $false }
        try {
            $blob = [Convert]::FromBase64String($b64)
            if ($blob.Length -lt 4) { return $false }
            # SSH strings start with an unsigned, big-endian byte count.
            $typeLength = [uint64]$blob[0] * 16777216 + [uint64]$blob[1] * 65536 + [uint64]$blob[2] * 256 + [uint64]$blob[3]
            if ($typeLength -ne $parts[0].Length -or $blob.Length -le (4 + $typeLength)) { return $false }
            for ($i = 0; $i -lt $typeLength; $i++) {
                if ($blob[4 + $i] -ne [byte][char]$parts[0][$i]) { return $false }
            }
            $keyCount++
        } catch {
            return $false
        }
    }
    return $keyCount -gt 0
}

function Assert-ValidPublicKey {
    param([string] $KeyText)
    if ([string]::IsNullOrWhiteSpace($KeyText)) {
        throw 'Public key cannot be empty.'
    }
    if ($KeyText -match '(?i)BEGIN.*PRIVATE KEY|PRIVATE KEY|Proc-Type|PuTTY-User-Key-File') {
        throw 'Public key source contains a private key. Only public keys are accepted.'
    }
    if (-not (Test-PublicKey $KeyText)) {
        throw "Invalid OpenSSH public key format. Expected standard public key format (e.g., 'ssh-ed25519 AAAA... comment')."
    }
}

function Resolve-PublicKey {
    param(
        [string] $AuthorizedKey,
        [string] $AuthorizedKeyPath
    )
    $hasKey = -not [string]::IsNullOrWhiteSpace($AuthorizedKey)
    $hasPath = -not [string]::IsNullOrWhiteSpace($AuthorizedKeyPath)
    if ($hasKey -and $hasPath) {
        throw 'Specify either AuthorizedKey or AuthorizedKeyPath, not both.'
    }
    if ($hasKey) {
        $key = $AuthorizedKey.Trim()
        Assert-ValidPublicKey $key
        return $key
    }
    if ($hasPath) {
        if (-not (Test-Path -LiteralPath $AuthorizedKeyPath)) {
            throw "Public key file not found: $AuthorizedKeyPath"
        }
        $raw = (Get-Content -LiteralPath $AuthorizedKeyPath -Raw).Trim()
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw "Public key file is empty: $AuthorizedKeyPath"
        }
        Assert-ValidPublicKey $raw
        return $raw
    }
    throw 'An authorized public key must be provided via AuthorizedKey or AuthorizedKeyPath.'
}

function Update-SshdConfigContent {
    param([string] $Content)
    if ($null -eq $Content) { $Content = '' }
    $firstMatch = [regex]::Match($Content, '(?im)^[ \t]*Match[ \t]+')
    $globalSection = $Content
    $matchSection = ''
    if ($firstMatch.Success) {
        $globalSection = $Content.Substring(0, $firstMatch.Index)
        $matchSection = $Content.Substring($firstMatch.Index)
    }
    $globalSection = [regex]::Replace($globalSection, '(?im)^[ \t]*#?[ \t]*(PasswordAuthentication|PubkeyAuthentication)[ \t]+[^\r\n]*(?:\r?\n|$)', '')
    if ($globalSection.Length -gt 0 -and -not $globalSection.EndsWith("`n")) {
        $globalSection += "`n"
    }
    return $globalSection + "PasswordAuthentication no`nPubkeyAuthentication yes`n" + $matchSection
}

function Assert-ConfirmationGate {
    param(
        [string] $ConfirmationPhrase,
        [switch] $WhatIf
    )
    if ($WhatIf) { return }
    if ($ConfirmationPhrase -cne 'BOOTSTRAP AND REBOOT') {
        throw "Refusing rename and reboot. Exact confirmation phrase 'BOOTSTRAP AND REBOOT' is required, or use -WhatIf."
    }
}

function Assert-ValidRemoteAddressScope {
    param([string] $Scope)
    if ($Scope -match '^(Any|LocalSubnet|DNS|DHCP|WINS|DefaultGateway|Internet|Intranet|IntranetRemoteAccess|PlayToDevice|CaptivePortal)[46]?$') { return }
    $parts = $Scope -split '/', 2
    $address = $null
    $valid = $parts[0] -match '^[0-9A-Fa-f:.]+$' -and [Net.IPAddress]::TryParse($parts[0], [ref]$address)
    if ($valid) {
        $maxPrefix = 128
        if ($address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) {
            $maxPrefix = 32
            # TryParse accepts abbreviated and numeric IPv4 forms; firewall inputs must be dotted decimal.
            $valid = $parts[0] -match '^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$'
        }
        if ($parts.Count -eq 2) {
            $prefix = 0
            $valid = $valid -and $parts[1] -match '^[0-9]{1,3}$' -and [int]::TryParse($parts[1], [ref]$prefix) -and $prefix -le $maxPrefix
        }
    }
    if (-not $valid) {
        throw 'Invalid AllowedRemoteAddresses: expected one IPv4/IPv6 address, CIDR, or supported firewall keyword.'
    }
}

function Resolve-Configuration {
    param(
        [string] $Path,
        [System.Collections.IDictionary] $Overrides = @{}
    )
    $defaults = [ordered]@{
        ComputerName = ''
        AuthorizedKey = ''
        AuthorizedKeyPath = ''
        AllowedRemoteAddresses = '192.0.2.0/24'
        DomainDnsTestName = 'ad.example.com'
        Restart = $false
    }
    $config = [ordered]@{}
    foreach ($k in $defaults.Keys) {
        $config[$k] = $defaults[$k]
    }

    if ($Path) {
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "Configuration file not found: $Path"
        }
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($raw -isnot [pscustomobject]) { throw 'Configuration must be a JSON object.' }
        foreach ($property in $raw.PSObject.Properties) {
            if ($property.Name.StartsWith('_comment')) { continue }
            if (-not $config.Contains($property.Name)) {
                throw "Unknown configuration field: $($property.Name)"
            }
            $config[$property.Name] = $property.Value
        }
    }

    if ($Overrides) {
        foreach ($key in @($config.Keys)) {
            if ($Overrides.ContainsKey($key) -and $null -ne $Overrides[$key]) {
                $val = $Overrides[$key]
                if ($val -is [System.Management.Automation.SwitchParameter]) {
                    $config[$key] = [bool]$val.IsPresent
                } elseif ($val -is [bool]) {
                    $config[$key] = $val
                } elseif ($key -eq 'Restart' -and $val -is [string]) {
                    $config[$key] = [bool]::Parse($val)
                } else {
                    $config[$key] = $val
                }
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($config.ComputerName)) {
        throw 'ComputerName is required.'
    }
    Assert-ValidComputerName $config.ComputerName

    $null = Resolve-PublicKey $config.AuthorizedKey $config.AuthorizedKeyPath

    Assert-ValidRemoteAddressScope $config.AllowedRemoteAddresses

    if ([string]::IsNullOrWhiteSpace($config.DomainDnsTestName)) {
        throw 'DomainDnsTestName is required.'
    }

    if ($config.Restart -isnot [bool]) {
        throw 'Restart must be a boolean.'
    }

    return [pscustomobject]$config
}

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Live workstation operations require Windows.'
    }
}

function Assert-Elevated {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { return }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$identity
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this from an elevated PowerShell session.'
    }
}

function Get-HardwareReport {
    Assert-Windows
    $tpm = Get-Tpm
    $secureBoot = try { Confirm-SecureBootUEFI } catch { 'not UEFI' }
    $os = Get-CimInstance Win32_OperatingSystem
    [pscustomobject]@{
        TpmPresent = $tpm.TpmPresent
        TpmReady = $tpm.TpmReady
        SecureBoot = $secureBoot
        WindowsCaption = $os.Caption
        WindowsBuild = $os.BuildNumber
    }
}

function Set-PrivateNetworkProfile {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()
    Assert-Windows
    if ($PSCmdlet.ShouldProcess('Network profiles', 'Set non-domain profiles to Private')) {
        Get-NetConnectionProfile | Where-Object NetworkCategory -ne 'DomainAuthenticated' |
            ForEach-Object {
                Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
                Write-Output "  $($_.InterfaceAlias): Private"
            }
    }
}

function Install-OpenSshServer {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param()
    Assert-Windows
    if ($PSCmdlet.ShouldProcess('OpenSSH Server', 'Install OpenSSH.Server capability')) {
        $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
        if ($cap.State -ne 'Installed') {
            Write-Output 'Installing the Windows capability...'
            try { Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null }
            catch { Write-Warning "Capability install failed: $($_.Exception.Message)" }
            $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
        }
        if ($cap.State -ne 'Installed') {
            Write-Output 'Capability route failed. Trying winget...'
            winget install --id Microsoft.OpenSSH.Preview --exact --accept-source-agreements --accept-package-agreements --silent
        }
        if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
            throw 'sshd service is not present after both install routes. Stop here and report.'
        }
        Set-Service sshd -StartupType Automatic
        Start-Service sshd
    }
}

function Set-OpenSshFirewallRule {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([string] $AllowedRemoteAddresses = '192.0.2.0/24')
    Assert-ValidRemoteAddressScope $AllowedRemoteAddresses
    Assert-Windows
    if ($PSCmdlet.ShouldProcess('Firewall', "Configure OpenSSH inbound rule (remote scope: $AllowedRemoteAddresses)")) {
        $ruleParams = @{
            Name = 'OpenSSH-Server-In-TCP'
            DisplayName = 'OpenSSH Server (sshd)'
            Direction = 'Inbound'
            Protocol = 'TCP'
            LocalPort = 22
            Action = 'Allow'
            Profile = 'Any'
            Enabled = 'True'
            RemoteAddress = $AllowedRemoteAddresses
        }
        if (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule @ruleParams
        } else {
            New-NetFirewallRule @ruleParams | Out-Null
        }
        Enable-NetFirewallRule -Name 'FPS-ICMP4-ERQ-In' -ErrorAction SilentlyContinue
    }
}

function Set-SshdConfiguration {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string] $PublicKey,
        [string] $SshdConfigPath = 'C:\ProgramData\ssh\sshd_config',
        [string] $AuthKeysPath = 'C:\ProgramData\ssh\administrators_authorized_keys'
    )
    Assert-Windows
    if ($PSCmdlet.ShouldProcess($AuthKeysPath, 'Install administrators_authorized_keys and update sshd_config')) {
        $authDir = Split-Path -Parent $AuthKeysPath
        if (-not (Test-Path -LiteralPath $authDir)) {
            New-Item -ItemType Directory -Path $authDir -Force | Out-Null
        }
        Set-Content -Path $AuthKeysPath -Value $PublicKey -Encoding ascii
        # Replace the complete DACL, including explicit grants and denies, using locale-independent SIDs.
        $acl = Get-Acl -LiteralPath $AuthKeysPath -ErrorAction Stop
        $acl.SetSecurityDescriptorSddlForm('O:BAG:SYD:P(A;;FA;;;BA)(A;;FA;;;SY)')
        Set-Acl -LiteralPath $AuthKeysPath -AclObject $acl -ErrorAction Stop
        icacls.exe $AuthKeysPath /verify | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Authorized-key ACL verification failed: icacls exited with status $LASTEXITCODE."
        }

        $currentConfig = if (Test-Path -LiteralPath $SshdConfigPath) {
            Get-Content -LiteralPath $SshdConfigPath -Raw
        } else { '' }
        $newConfig = Update-SshdConfigContent $currentConfig
        Set-Content -Path $SshdConfigPath -Value $newConfig -Encoding ascii
        Restart-Service sshd
    }
}

function Invoke-ComputerRename {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([string] $ComputerName)
    Assert-Windows
    if ($env:COMPUTERNAME -ne $ComputerName) {
        if ($PSCmdlet.ShouldProcess($ComputerName, "Rename computer to '$ComputerName'")) {
            Rename-Computer -NewName $ComputerName -Force
            Write-Output "Renamed computer to '$ComputerName' (takes effect at reboot)."
        }
    } else {
        Write-Output "Computer name is already '$ComputerName'."
    }
}

function Get-BootstrapVerificationReport {
    param(
        [string] $DomainDnsTestName = 'ad.example.com',
        [string] $HostKeyPath = 'C:\ProgramData\ssh\ssh_host_ed25519_key.pub'
    )
    Assert-Windows
    $report = [ordered]@{}
    $service = Get-Service sshd -ErrorAction Stop
    if ($service.Status -ne 'Running') { throw 'sshd is not running.' }
    $report.SshdStatus = [string]$service.Status
    $ipConfigs = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway })
    $report.Interfaces = @($ipConfigs | ForEach-Object {
        [pscustomobject]@{
            Interface = $_.InterfaceAlias
            Address = $_.IPv4Address.IPAddress
            Gateway = $_.IPv4DefaultGateway.NextHop
            DnsServers = ($_.DNSServer.ServerAddresses -join ', ')
        }
    })
    $report.DomainDns = try {
        $addresses = @(Resolve-DnsName $DomainDnsTestName -Type A -ErrorAction Stop |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_.IPAddress) })
        if ($addresses.Count -eq 0) { throw 'No address records returned.' }
        $addresses[0].IPAddress
    } catch {
        throw "Domain DNS resolution failed: $($_.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $HostKeyPath -PathType Leaf)) {
        throw "Host key file not found: $HostKeyPath"
    }
    $report.HostKey = try {
        $fingerprint = ssh-keygen -lf $HostKeyPath
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($fingerprint -join ''))) {
            throw "ssh-keygen exited with status $LASTEXITCODE or returned no fingerprint."
        }
        $fingerprint -replace '\s+\S+$', ''
    } catch { throw "Host key fingerprint failed: $($_.Exception.Message)" }

    return [pscustomobject]$report
}

function Invoke-WorkstationBootstrap {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        $Config,
        [string] $ConfirmationPhrase
    )
    Assert-ValidRemoteAddressScope $Config.AllowedRemoteAddresses
    $Config | ConvertTo-Json -Depth 4 | Write-Output

    Assert-ConfirmationGate -ConfirmationPhrase $ConfirmationPhrase -WhatIf:$WhatIfPreference

    if ($WhatIfPreference) {
        Write-Output "What if: Would configure OpenSSH, firewall (scope: $($Config.AllowedRemoteAddresses)), administrators_authorized_keys, and rename computer to '$($Config.ComputerName)'."
        return
    }

    Assert-Windows
    Assert-Elevated

    Write-Output '== Hardware report (informational)'
    $hw = Get-HardwareReport
    Write-Output "TPM present: $($hw.TpmPresent)  ready: $($hw.TpmReady)"
    Write-Output "Secure Boot: $($hw.SecureBoot)"
    Write-Output "Windows: $($hw.WindowsCaption) build $($hw.WindowsBuild)"

    Write-Output "`n== Network profile to Private"
    Set-PrivateNetworkProfile

    Write-Output "`n== OpenSSH Server"
    Install-OpenSshServer

    Write-Output "`n== Firewall: OpenSSH inbound rule and ICMP echo"
    Set-OpenSshFirewallRule -AllowedRemoteAddresses $Config.AllowedRemoteAddresses

    Write-Output "`n== Key-only access for administrators"
    $pubKey = Resolve-PublicKey $Config.AuthorizedKey $Config.AuthorizedKeyPath
    Set-SshdConfiguration -PublicKey $pubKey

    Write-Output "`n== Computer name"
    Invoke-ComputerRename -ComputerName $Config.ComputerName

    Write-Output "`n== Verification report"
    $report = Get-BootstrapVerificationReport -DomainDnsTestName $Config.DomainDnsTestName
    foreach ($iface in $report.Interfaces) {
        Write-Output "  Interface : $($iface.Interface)"
        Write-Output "  Address   : $($iface.Address)"
        Write-Output "  Gateway   : $($iface.Gateway)"
        Write-Output "  DNS       : $($iface.DnsServers)"
    }
    Write-Output "  Domain DNS : $($report.DomainDns)"
    Write-Output "  Host key   : $($report.HostKey)"
    Write-Output "  sshd       : $($report.SshdStatus)"

    if ($Config.Restart) {
        Write-Output "`n== Restarting computer"
        if ($PSCmdlet.ShouldProcess($Config.ComputerName, 'Restart computer')) {
            Restart-Computer -Force
        }
    } else {
        Write-Output "`nReboot is required for the new computer name to take effect."
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if ($Help) {
            Write-Output 'bootstrap-workstation.ps1 [-ConfigPath PATH] [-ComputerName NAME] [-AuthorizedKey KEY] [-AuthorizedKeyPath PATH] [-AllowedRemoteAddresses SCOPE] [-DomainDnsTestName NAME] [-Restart] [-ConfirmationPhrase PHRASE] [-WhatIf]'
            Write-Output "Renames workstation and configures key-only OpenSSH server. Requires exact confirmation phrase 'BOOTSTRAP AND REBOOT' or -WhatIf."
            exit 0
        }
        $configuration = Resolve-Configuration $ConfigPath $PSBoundParameters
        Invoke-WorkstationBootstrap -Config $configuration -ConfirmationPhrase $ConfirmationPhrase -WhatIf:$WhatIfPreference
        exit 0
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
