<#
.SYNOPSIS
Check or maintain Windows recovery lockdown with a SYSTEM scheduled task.
.DESCRIPTION
Defaults to a read-only check. Install and uninstall require an exact Approval
phrase. Use -WhatIf to preview. Configuration precedence is parameters, explicit
JSON config, then defaults. Dot-sourcing defines functions without running them.
.EXAMPLE
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json -WhatIf
.EXAMPLE
.\recovery-lockdown.ps1 -Install -ConfigPath .\config.local.json -Approval INSTALL_RECOVERY_LOCKDOWN
#>
[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Check')]
param(
    [Parameter(ParameterSetName = 'Install', Mandatory = $true)] [switch] $Install,
    [Parameter(ParameterSetName = 'Uninstall', Mandatory = $true)] [switch] $Uninstall,
    [Parameter(ParameterSetName = 'Enforce', Mandatory = $true)] [switch] $Enforce,
    [Parameter(ParameterSetName = 'Check')] [switch] $Check,
    [string] $ConfigPath,
    [string] $Approval,
    [string] $TaskName,
    [string] $TaskPath,
    [string] $InstallRoot,
    [string] $ScriptName,
    [string] $LogName,
    [string] $DailyAt,
    [int] $StartupDelaySeconds,
    [int] $VerificationTimeoutSeconds,
    [string] $ReagentcPath,
    [string] $PowerShellPath
)

function Get-RecoveryDefaults {
    @{
        TaskName = 'Windows Recovery Guard'; TaskPath = '\'
        InstallRoot = '%ProgramData%\WindowsRecoveryGuard'
        ScriptName = 'recovery-lockdown.ps1'; LogName = 'recovery.log'
        DailyAt = '03:00'; StartupDelaySeconds = 60
        VerificationTimeoutSeconds = 120
        ReagentcPath = '%SystemRoot%\System32\reagentc.exe'
        PowerShellPath = '%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe'
    }
}

function Merge-RecoveryConfig {
    param([System.Collections.IDictionary] $Local = @{}, [System.Collections.IDictionary] $Overrides = @{})
    $result = Get-RecoveryDefaults
    foreach ($layer in @($Local, $Overrides)) {
        foreach ($key in $layer.Keys) {
            if ($key -like '_comment*') { continue }
            if (-not $result.ContainsKey($key)) { throw "Unknown configuration key: $key" }
            $result[$key] = $layer[$key]
        }
    }
    foreach ($key in @('TaskName', 'TaskPath', 'InstallRoot', 'ScriptName', 'LogName', 'DailyAt', 'ReagentcPath', 'PowerShellPath')) {
        if ($result[$key] -isnot [string] -or [string]::IsNullOrWhiteSpace($result[$key])) {
            throw "$key must be a nonempty string."
        }
        if ($result[$key] -match '[\x00-\x1f"]') { throw "$key contains unsafe characters." }
    }
    if ($result.TaskName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,100}$') { throw 'Invalid TaskName.' }
    if ($result.TaskPath -notmatch '^\\(?:[a-zA-Z0-9][a-zA-Z0-9 _-]*\\)*$') { throw 'Invalid TaskPath.' }
    if ($result.DailyAt -notmatch '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$') { throw 'DailyAt must be HH:mm.' }
    foreach ($key in @('ScriptName', 'LogName')) {
        if ($result[$key] -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]*\.[a-zA-Z0-9]+$') { throw "Invalid $key." }
    }
    if ($result.ScriptName -notmatch '\.ps1$' -or $result.LogName -notmatch '\.log$') {
        throw 'ScriptName must end in .ps1 and LogName in .log.'
    }
    foreach ($key in @('StartupDelaySeconds', 'VerificationTimeoutSeconds')) {
        $minimum = 1
        if ($key -eq 'VerificationTimeoutSeconds') { $minimum = 10 }
        if (($result[$key] -isnot [int] -and $result[$key] -isnot [long]) -or
            $result[$key] -lt $minimum -or $result[$key] -gt 3600) { throw "Invalid $key." }
    }
    foreach ($key in @('InstallRoot', 'ReagentcPath', 'PowerShellPath')) {
        $value = [Environment]::ExpandEnvironmentVariables($result[$key])
        # Permit unresolved standard placeholders during configuration on other platforms.
        $patternValue = $value.Replace('%ProgramData%', 'C:\ProgramData').Replace('%SystemRoot%', 'C:\Windows')
        if ($patternValue -notmatch '^[a-zA-Z]:\\[^:*?"<>|%]+$' -or
            $patternValue -match '(?:^|\\)\.{1,2}(?:\\|$)' -or $patternValue -match '[ .](?:\\|$)') {
            throw "$key must be an absolute local Windows path without traversal or wildcards."
        }
        $result[$key] = $value.TrimEnd('\')
    }
    return $result
}

function Read-RecoveryConfig {
    param([string] $Path)
    if (-not $Path) { return @{} }
    $payload = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $payload -or $payload -isnot [pscustomobject]) { throw 'Configuration must be a JSON object.' }
    $values = @{}
    foreach ($property in $payload.PSObject.Properties) { $values[$property.Name] = $property.Value }
    return $values
}

function ConvertFrom-ReagentcOutput {
    param([string] $Text, [int] $ExitCode = 0)
    if ($ExitCode -ne 0) { throw "reagentc failed with exit code $ExitCode." }
    $lines = [regex]::Matches($Text, '(?im)^\s*Windows RE status\s*:\s*([^\r\n]+)\s*$')
    if ($lines.Count -ne 1) { throw 'Expected exactly one English Windows RE status line.' }
    $status = $lines[0].Groups[1].Value.Trim()
    if ($status -ieq 'Enabled') { return 'Enabled' }
    if ($status -ieq 'Disabled') { return 'Disabled' }
    throw 'Unrecognized Windows RE status.'
}

function Test-RecoveryState {
    param([string] $WinRE, $ProviderSet, $AuthenticationRequirement)
    $failures = @()
    if ($WinRE -cne 'Disabled') { $failures += 'WinRE is not confirmed disabled.' }
    if ($null -eq $ProviderSet -or "$ProviderSet" -cne '1') { $failures += 'Policy provider is not confirmed set.' }
    if ($null -eq $AuthenticationRequirement -or "$AuthenticationRequirement" -cne '1') {
        $failures += 'Recovery authentication is not confirmed required.'
    }
    [pscustomobject]@{ Passed = ($failures.Count -eq 0); Failures = $failures }
}

function Assert-RecoveryPlatform {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Live operations require Windows.' }
    if (-not [Environment]::Is64BitProcess) { throw 'Use a 64-bit PowerShell process.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this operation from an elevated PowerShell session.'
    }
}

function Assert-RecoverySystem {
    if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') {
        throw 'Enforcement requires SYSTEM because the MDM bridge rejects other callers.'
    }
}

function Assert-NoReparsePoint {
    param([string] $Path)
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are not allowed.' }
        }
        $cursor = Split-Path -Path $cursor -Parent
    }
}

function Assert-ProtectedPath {
    param([string] $Path)
    Assert-NoReparsePoint $Path
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
        throw 'Installed paths must be owned by SYSTEM or Administrators.'
    }
    $writeRights = [Security.AccessControl.FileSystemRights]'Write, Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and
            ($rule.FileSystemRights -band $writeRights)) { throw 'Installed path permits untrusted writes.' }
    }
}

function Set-RecoveryAcl {
    param([string] $Path, [switch] $Directory)
    Assert-NoReparsePoint $Path
    if ($Directory) { $acl = New-Object Security.AccessControl.DirectorySecurity }
    else { $acl = New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $inherit = [Security.AccessControl.InheritanceFlags]::None
        if ($Directory) { $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' }
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
    Assert-ProtectedPath $Path
}

function Assert-TrustedInstallParent {
    param([string] $Path)
    $trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $dangerous = [Security.AccessControl.FileSystemRights]'Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'
    $cursor = $Path
    while ($cursor) {
        $acl = Get-Acl -LiteralPath $cursor -ErrorAction Stop
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
            throw 'Installation ancestors must have a trusted system owner.'
        }
        foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
            # Inherit-only entries do not grant rights on this ancestor itself.
            if (-not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
                $rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and
                ($rule.FileSystemRights -band $dangerous)) { throw 'Installation ancestor permits untrusted deletion or ACL changes.' }
        }
        $cursor = Split-Path -Path $cursor -Parent
    }
}

function Invoke-Reagentc {
    param([hashtable] $Settings, [ValidateSet('/info', '/disable')] [string] $Argument)
    $output = & $Settings.ReagentcPath $Argument 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "reagentc $Argument failed with exit code $code." }
    if ($Argument -eq '/info') { return ConvertFrom-ReagentcOutput ($output -join "`n") $code }
}

function Get-RecoveryState {
    param([hashtable] $Settings)
    $status = Invoke-Reagentc $Settings '/info'
    $provider = $null; $requirement = $null
    $providerKey = 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\Security'
    $policyKey = 'HKLM:\SOFTWARE\Policies\Microsoft\WinRE'
    if (Test-Path -LiteralPath $providerKey) {
        $provider = (Get-ItemProperty -LiteralPath $providerKey -ErrorAction Stop).RecoveryEnvironmentAuthentication_ProviderSet
    }
    if (Test-Path -LiteralPath $policyKey) {
        $requirement = (Get-ItemProperty -LiteralPath $policyKey -ErrorAction Stop).WinREAuthenticationRequirement
    }
    [pscustomobject]@{ WinRE = $status; ProviderSet = $provider; AuthenticationRequirement = $requirement }
}

function Set-RecoveryPolicy {
    $namespace = 'root\cimv2\mdm\dmmap'; $class = 'MDM_Policy_Config01_Security02'
    $instances = @(Get-CimInstance -Namespace $namespace -ClassName $class -ErrorAction Stop |
        Where-Object { $_.InstanceID -eq 'Security' -and $_.ParentID -eq './Vendor/MSFT/Policy/Config' })
    if ($instances.Count -gt 1) { throw 'Ambiguous MDM Security policy instances.' }
    if ($instances.Count -eq 0) {
        # The bridge requires a signed integer, not UInt32.
        New-CimInstance -Namespace $namespace -ClassName $class -Property @{
            ParentID = './Vendor/MSFT/Policy/Config'; InstanceID = 'Security'
            RecoveryEnvironmentAuthentication = [int] 1
        } -ErrorAction Stop | Out-Null
    } elseif ($instances[0].RecoveryEnvironmentAuthentication -ne 1) {
        Set-CimInstance -CimInstance $instances[0] -Property @{
            RecoveryEnvironmentAuthentication = [int] 1
        } -ErrorAction Stop | Out-Null
    }
}

function Get-RecoveryTaskArguments {
    param([hashtable] $Settings)
    $script = Join-Path $Settings.InstallRoot $Settings.ScriptName
    $config = Join-Path $Settings.InstallRoot 'config.local.json'
    return "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`" -Enforce -ConfigPath `"$config`" -Approval ENFORCE_RECOVERY_LOCKDOWN"
}

function Get-RecoveryTask {
    param([hashtable] $Settings)
    # Enumerating the folder distinguishes a missing task from a query failure.
    $tasks = @(Get-ScheduledTask -TaskPath $Settings.TaskPath -ErrorAction Stop |
        Where-Object { $_.TaskName -eq $Settings.TaskName })
    if ($tasks.Count -gt 1) { throw 'Ambiguous scheduled task name.' }
    if ($tasks.Count -eq 1) { return $tasks[0] }
    return $null
}

function Assert-RecoveryTask {
    param($Task, [hashtable] $Settings, [switch] $Schedule)
    if ($null -eq $Task) { throw 'Scheduled task is missing.' }
    $actions = @($Task.Actions)
    if ($Task.Description -ne 'Managed by windows-recovery-lockdown.' -or $actions.Count -ne 1 -or
        $actions[0].Execute -ine $Settings.PowerShellPath -or
        $actions[0].Arguments -cne (Get-RecoveryTaskArguments $Settings) -or
        $Task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18', 'NT AUTHORITY\SYSTEM') -or
        "$($Task.Principal.LogonType)" -ne 'ServiceAccount' -or "$($Task.Principal.RunLevel)" -ne 'Highest') {
        throw 'Task does not match this installation; refusing to manage it.'
    }
    if ($Schedule) {
        $boot = @($Task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' })
        $daily = @($Task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' })
        if (@($Task.Triggers).Count -ne 2 -or $boot.Count -ne 1 -or $daily.Count -ne 1) { throw 'Task triggers do not match.' }
        if ([Xml.XmlConvert]::ToTimeSpan($boot[0].Delay).TotalSeconds -ne $Settings.StartupDelaySeconds -or
            ([datetime]$daily[0].StartBoundary).ToString('HH:mm') -ne $Settings.DailyAt -or
            $daily[0].DaysInterval -ne 1 -or -not $boot[0].Enabled -or -not $daily[0].Enabled -or
            -not $Task.Settings.Enabled -or -not $Task.Settings.StartWhenAvailable -or
            "$($Task.Settings.MultipleInstances)" -ne 'IgnoreNew' -or
            "$($Task.State)" -eq 'Disabled') { throw 'Task schedule or settings do not match.' }
    }
}

function Install-RecoveryTask {
    param([hashtable] $Settings, [string] $SourcePath)
    if (Test-Path -LiteralPath $Settings.InstallRoot) { throw 'InstallRoot already exists; refusing to replace it.' }
    if ($null -ne (Get-RecoveryTask $Settings)) { throw 'Task already exists; refusing to replace it.' }
    $parent = Split-Path -Path $Settings.InstallRoot -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'InstallRoot parent must already exist.' }
    Assert-NoReparsePoint $parent
    Assert-TrustedInstallParent $parent
    $windows = [Environment]::GetFolderPath('Windows').TrimEnd('\')
    if ($Settings.InstallRoot -ieq $windows -or $Settings.InstallRoot.StartsWith($windows + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'InstallRoot must be outside the Windows directory.'
    }
    foreach ($path in @($Settings.ReagentcPath, $Settings.PowerShellPath, $SourcePath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A required executable or source file is missing.' }
        Assert-NoReparsePoint $path
    }
    $registered = $false
    try {
        New-Item -ItemType Directory -Path $Settings.InstallRoot -ErrorAction Stop | Out-Null
        Set-RecoveryAcl $Settings.InstallRoot -Directory
        $target = Join-Path $Settings.InstallRoot $Settings.ScriptName
        Copy-Item -LiteralPath $SourcePath -Destination $target -ErrorAction Stop
        Set-RecoveryAcl $target
        if ((Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) { throw 'Installed script copy did not verify.' }
        $config = Join-Path $Settings.InstallRoot 'config.local.json'
        $Settings | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8 -ErrorAction Stop
        Set-RecoveryAcl $config
        $action = New-ScheduledTaskAction -Execute $Settings.PowerShellPath -Argument (Get-RecoveryTaskArguments $Settings)
        $boot = New-ScheduledTaskTrigger -AtStartup
        $boot.Delay = [Xml.XmlConvert]::ToString([TimeSpan]::FromSeconds($Settings.StartupDelaySeconds))
        $daily = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([TimeSpan]::Parse($Settings.DailyAt)))
        $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
        $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath -Action $action -Trigger @($boot, $daily) -Principal $principal -Settings $taskSettings -Description 'Managed by windows-recovery-lockdown.' -ErrorAction Stop | Out-Null
        $registered = $true
        Assert-RecoveryTask (Get-RecoveryTask $Settings) $Settings -Schedule
        $before = (Get-ScheduledTaskInfo -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath -ErrorAction Stop).LastRunTime
        Start-ScheduledTask -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath -ErrorAction Stop
        $deadline = [datetime]::UtcNow.AddSeconds($Settings.VerificationTimeoutSeconds)
        do {
            Start-Sleep -Seconds 1
            $info = Get-ScheduledTaskInfo -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath -ErrorAction Stop
            $task = Get-RecoveryTask $Settings
            if ($info.LastRunTime -gt $before -and "$($task.State)" -ne 'Running' -and $info.LastTaskResult -ne 267009) {
                if ($info.LastTaskResult -ne 0) { throw "Initial task run failed with result $($info.LastTaskResult)." }
                $state = Get-RecoveryState $Settings
                $result = Test-RecoveryState $state.WinRE $state.ProviderSet $state.AuthenticationRequirement
                if (-not $result.Passed) { throw ($result.Failures -join ' ') }
                return
            }
        } while ([datetime]::UtcNow -lt $deadline)
        throw 'Timed out waiting for the initial task run.'
    } catch {
        # Once registered, the task may have changed recovery state. Keep evidence
        # and enforcement in place rather than silently weakening protection.
        if ($registered) { Write-Warning 'Task and files retained. Inspect the log, then check or uninstall explicitly.' }
        else { Write-Warning 'Installation failed before task registration. Inspect and remove any partial installation directory before retrying.' }
        throw
    }
}

function Invoke-RecoveryTool {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [ValidateSet('Check', 'Install', 'Uninstall', 'Enforce')] [string] $Mode = 'Check',
        [hashtable] $Settings, [string] $Approval, [string] $SourcePath
    )
    $ErrorActionPreference = 'Stop'
    if ($Mode -ne 'Check') {
        $phrase = $Mode.ToUpperInvariant() + '_RECOVERY_LOCKDOWN'
        if (-not $WhatIfPreference -and $Approval -cne $phrase) { throw "Approval must equal $phrase." }
        if (-not $PSCmdlet.ShouldProcess("$($Settings.TaskPath)$($Settings.TaskName) at $($Settings.InstallRoot)", $Mode)) { return 0 }
    }
    Assert-RecoveryPlatform
    if ($Mode -eq 'Install') {
        Install-RecoveryTask $Settings $SourcePath
        Write-Host 'Installation and initial enforcement verified.'
        return 0
    }
    if ($Mode -eq 'Uninstall') {
        $task = Get-RecoveryTask $Settings
        if ($null -eq $task) { Write-Host 'Task is already absent. Recovery state and files retained.'; return 0 }
        Assert-RecoveryTask $task $Settings
        Disable-ScheduledTask -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath | Out-Null
        Stop-ScheduledTask -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath
        $deadline = [datetime]::UtcNow.AddSeconds($Settings.VerificationTimeoutSeconds)
        while ("$((Get-RecoveryTask $Settings).State)" -eq 'Running') {
            if ([datetime]::UtcNow -ge $deadline) { throw 'Task did not stop; it remains disabled.' }
            Start-Sleep -Seconds 1
        }
        Unregister-ScheduledTask -TaskName $Settings.TaskName -TaskPath $Settings.TaskPath -Confirm:$false
        if ($null -ne (Get-RecoveryTask $Settings)) { throw 'Task removal could not be verified.' }
        Write-Host 'Task removed. Recovery state and installed files retained.'
        return 0
    }
    Assert-ProtectedPath $Settings.InstallRoot
    Assert-TrustedInstallParent (Split-Path -Path $Settings.InstallRoot -Parent)
    Assert-ProtectedPath (Join-Path $Settings.InstallRoot $Settings.ScriptName)
    Assert-ProtectedPath (Join-Path $Settings.InstallRoot 'config.local.json')
    if ($Mode -eq 'Enforce') {
        Assert-RecoverySystem
        $log = Join-Path $Settings.InstallRoot $Settings.LogName
        if (Test-Path -LiteralPath $log) { Assert-ProtectedPath $log }
        try {
            $status = Invoke-Reagentc $Settings '/info'
            if ($status -eq 'Enabled') { Invoke-Reagentc $Settings '/disable' }
            Set-RecoveryPolicy
            $state = Get-RecoveryState $Settings
            $result = Test-RecoveryState $state.WinRE $state.ProviderSet $state.AuthenticationRequirement
            $line = '{0:o} WinRE={1} ProviderSet={2} AuthenticationRequirement={3} Passed={4}' -f [datetime]::Now, $state.WinRE, $state.ProviderSet, $state.AuthenticationRequirement, $result.Passed
            Add-Content -LiteralPath $log -Value $line -ErrorAction Stop
            Write-Host $line
            if (-not $result.Passed) { return 2 }
            return 0
        } catch {
            Add-Content -LiteralPath $log -Value ('{0:o} Enforcement failed; check task result and run an elevated check.' -f [datetime]::Now) -ErrorAction Continue
            throw
        }
    }
    Assert-RecoveryTask (Get-RecoveryTask $Settings) $Settings -Schedule
    $installed = Merge-RecoveryConfig (Read-RecoveryConfig (Join-Path $Settings.InstallRoot 'config.local.json'))
    foreach ($key in $Settings.Keys) {
        if ($installed[$key] -cne $Settings[$key]) { throw "Installed configuration differs: $key" }
    }
    $state = Get-RecoveryState $Settings
    $result = Test-RecoveryState $state.WinRE $state.ProviderSet $state.AuthenticationRequirement
    Write-Host ($state | ConvertTo-Json -Compress)
    foreach ($failure in $result.Failures) { Write-Host "FAIL: $failure" }
    if (-not $result.Passed) { return 2 }
    Write-Host 'PASS: recovery state and task configuration match.'
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $overrides = @{}
        foreach ($key in (Get-RecoveryDefaults).Keys) {
            if ($PSBoundParameters.ContainsKey($key)) { $overrides[$key] = $PSBoundParameters[$key] }
        }
        $settings = Merge-RecoveryConfig (Read-RecoveryConfig $ConfigPath) $overrides
        $mode = $PSCmdlet.ParameterSetName
        exit (Invoke-RecoveryTool -Mode $mode -Settings $settings -Approval $Approval -SourcePath $PSCommandPath -WhatIf:$WhatIfPreference)
    } catch {
        [Console]::Error.WriteLine("error: $($_.Exception.Message)")
        exit 1
    }
}
