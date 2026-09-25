<#
.SYNOPSIS
Preview, install, remove, or run a per-account daily session policy.
.DESCRIPTION
Install, Uninstall, and Tick require -Apply to change state. -WhatIf also
prevents changes. Dot-source this file to load its pure decision functions.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Install', 'Uninstall', 'Status', 'Tick')][string] $Mode = 'Status',
    [string] $ConfigPath,
    [string] $TargetGroup,
    [string] $WindowStart,
    [string] $WindowEnd,
    [int] $DailyBudgetMinutes,
    [int[]] $WarningMinutes,
    [string] $StatePath,
    [string] $TaskName,
    [switch] $Apply,
    [switch] $Help
)

function ConvertTo-WindowMinute {
    param([string] $Value)
    if ($Value -notmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { throw 'Window time must be HH:mm.' }
    return ([int]$Value.Substring(0, 2) * 60 + [int]$Value.Substring(3, 2))
}

function Get-WindowDecision {
    param([datetime] $Now, [string] $Start, [string] $End)
    $first = ConvertTo-WindowMinute $Start
    $last = ConvertTo-WindowMinute $End
    if ($first -eq $last) { throw 'Window endpoints must differ.' }
    $minute = $Now.TimeOfDay.TotalMinutes
    if ($first -lt $last) { $inside = $minute -ge $first -and $minute -lt $last }
    else { $inside = $minute -ge $first -or $minute -lt $last }
    $left = 0.0
    if ($inside) {
        $left = $last - $minute
        if ($left -le 0) { $left += 1440 }
    }
    [pscustomobject]@{ Inside = $inside; MinutesLeft = $left }
}

function New-UsageState {
    param([datetime] $Now)
    [pscustomobject]@{ Day = $Now.ToString('yyyy-MM-dd'); Minutes = 0.0; LastTick = $null; Warned = @() }
}

function ConvertFrom-UsageJson {
    param([string] $Json)
    try {
        $jsonOptions = @{ InputObject = $Json; ErrorAction = 'Stop' }
        if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonOptions.DateKind = 'String' }
        $state = ConvertFrom-Json @jsonOptions
        if ($state.LastTick -is [datetime]) { $state.LastTick = $state.LastTick.ToString('o') }
        if ($state -isnot [pscustomobject]) { throw 'Invalid object.' }
        $names = @($state.PSObject.Properties.Name | Sort-Object)
        if (($names -join ',') -ne 'Day,LastTick,Minutes,Warned') { throw 'Invalid fields.' }
        $day = [datetime]::ParseExact($state.Day, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture)
        if ($state.Minutes -is [string] -or $state.Minutes -is [bool] -or $null -eq $state.Minutes -or
            [double]::IsNaN($state.Minutes) -or [double]::IsInfinity($state.Minutes) -or $state.Minutes -lt 0) { throw 'Invalid usage.' }
        if ($null -ne $state.LastTick) {
            $tick = [datetimeoffset]::ParseExact($state.LastTick, 'o', [cultureinfo]::InvariantCulture)
            if ($tick.ToString('yyyy-MM-dd') -ne $day.ToString('yyyy-MM-dd')) { throw 'Invalid tick day.' }
        }
        if ($state.Warned -isnot [array]) { throw 'Invalid warnings.' }
        foreach ($key in $state.Warned) {
            if ($key -isnot [string] -or $key -notmatch '^warn-[1-9][0-9]*$') { throw 'Invalid warning key.' }
        }
        return $state
    } catch { throw 'State is corrupt; leave it unchanged and skip enforcement for this account.' }
}

function Get-UsageDecision {
    param($State, [datetime] $Now, [string] $Start, [string] $End,
        [int] $BudgetMinutes, [int[]] $Warnings)
    if ($BudgetMinutes -le 0) { throw 'Budget must be positive.' }
    # Copy through validation so callers retain their original state, including on rollover.
    $next = ConvertFrom-UsageJson ($State | ConvertTo-Json -Compress)
    if ($next.Day -ne $Now.ToString('yyyy-MM-dd')) { $next = New-UsageState $Now }
    if ($next.LastTick) {
        $elapsed = ([datetimeoffset]$Now - [datetimeoffset]$next.LastTick).TotalMinutes
        if ($elapsed -gt 0 -and $elapsed -le 5) { $next.Minutes += $elapsed }
    }
    $next.LastTick = ([datetimeoffset]$Now).ToString('o')
    $window = Get-WindowDecision $Now $Start $End
    $remaining = [math]::Min(($BudgetMinutes - $next.Minutes), $window.MinutesLeft)
    $reason = ''
    if (-not $window.Inside) { $reason = 'outside window' }
    elseif ($next.Minutes -ge $BudgetMinutes) { $reason = 'budget exhausted' }
    $due = @()
    if (-not $reason) {
        foreach ($threshold in ($Warnings | Sort-Object -Descending -Unique)) {
            if ($threshold -le 0) { throw 'Warning thresholds must be positive.' }
            if ($remaining -le $threshold -and $next.Warned -notcontains "warn-$threshold") { $due += $threshold }
        }
    }
    [pscustomobject]@{ State = $next; Reason = $reason; Remaining = $remaining; Warnings = $due }
}

function Resolve-Configuration {
    param([string] $Path, [System.Collections.IDictionary] $Overrides = @{})
    $config = [ordered]@{
        TargetGroup = ''; WindowStart = '09:00'; WindowEnd = '21:00'
        DailyBudgetMinutes = 120; WarningMinutes = @(10, 3); StatePath = ''; TaskName = ''
    }
    if ($Path) {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($raw -isnot [pscustomobject]) { throw 'Configuration must be a JSON object.' }
        foreach ($property in $raw.PSObject.Properties) {
            if ($property.Name.StartsWith('_comment')) { continue }
            if (-not $config.Contains($property.Name)) { throw "Unknown configuration field: $($property.Name)" }
            $config[$property.Name] = $property.Value
        }
    }
    foreach ($key in @($config.Keys)) {
        if (@($Overrides.Keys) -contains $key) { $config[$key] = $Overrides[$key] }
    }
    foreach ($key in @('TargetGroup', 'StatePath', 'TaskName', 'WindowStart', 'WindowEnd')) {
        if ($config[$key] -isnot [string] -or [string]::IsNullOrWhiteSpace($config[$key])) { throw "$key is required." }
    }
    if ($config.TargetGroup -match '[\x00-\x1f]' -or $config.TaskName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9 ._-]{0,100}$') { throw 'Invalid group or task name.' }
    if ($config.StatePath -notmatch '^[a-zA-Z]:\\' -or $config.StatePath -match '["<>|?*\x00-\x1f]' -or
        $config.StatePath.Substring(2).Contains(':') -or $config.StatePath -match '(?:^|\\)\.\.?(?:\\|$)' -or
        $config.StatePath -match '[. ](?:\\|$)' -or $config.StatePath.EndsWith('\')) { throw 'StatePath must be an absolute local Windows folder without a trailing separator.' }
    if ($config.DailyBudgetMinutes -isnot [int] -and $config.DailyBudgetMinutes -isnot [long]) { throw 'Budget must be an integer.' }
    if ($config.DailyBudgetMinutes -lt 1 -or $config.DailyBudgetMinutes -gt 1440) { throw 'Budget must be 1 to 1440 minutes.' }
    if ($config.WarningMinutes -isnot [array]) { throw 'WarningMinutes must be an array.' }
    foreach ($value in $config.WarningMinutes) {
        if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt 1 -or $value -gt 1440) { throw 'Warnings must be integer minutes from 1 to 1440.' }
    }
    $null = Get-WindowDecision ([datetime]::Today) $config.WindowStart $config.WindowEnd
    return [pscustomobject]$config
}

function Read-UsageState {
    param([string] $Path, [datetime] $Now)
    if (-not (Test-Path -LiteralPath $Path)) { return New-UsageState $Now }
    ConvertFrom-UsageJson (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop)
}

function Write-AtomicJson {
    param([string] $Path, $Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Live operations require Windows.' }
}

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this operation from an elevated PowerShell session.' }
}

function Assert-NoReparsePoint {
    param([string] $Path)
    $itemPath = $Path
    while ($itemPath) {
        if (Test-Path -LiteralPath $itemPath) {
            if ((Get-Item -LiteralPath $itemPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are not allowed in the state path.' }
        }
        $itemPath = Split-Path -Parent $itemPath
    }
}

function Assert-ProtectedPath {
    param([string] $Path)
    Assert-NoReparsePoint $Path
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw 'State owner must be SYSTEM or Administrators.' }
    $writeMask = [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and ($rule.FileSystemRights -band $writeMask)) { throw 'Untrusted write permission on protected path.' }
    }
}

function New-ProtectedDirectory {
    param([string] $Path)
    Assert-NoReparsePoint $Path
    if (Test-Path -LiteralPath $Path) { throw 'StatePath already exists; refusing to reuse it during install.' }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'StatePath parent must already exist.' }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    # Use the Windows PowerShell overload to apply the ACL at directory creation.
    $directory = New-Object IO.DirectoryInfo($Path)
    if ($PSVersionTable.PSVersion.Major -le 5) { $directory.Create($acl) }
    else { [IO.FileSystemAclExtensions]::Create($directory, $acl) }
    Assert-ProtectedPath $Path
}

function Get-SessionTask {
    param([string] $Name)
    @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -eq $Name }) | Select-Object -First 1
}

function Get-TaskArguments {
    param($Config)
    '-NoProfile -NonInteractive -File "{0}\session-limits.ps1" -Mode Tick -ConfigPath "{0}\config.local.json" -Apply' -f $Config.StatePath
}

function Assert-OwnedTask {
    param($Task, $Config)
    $executable = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if ($Task.Description -ne 'Windows session limits managed task' -or @($Task.Actions).Count -ne 1 -or
        $Task.Actions[0].Execute -ne $executable -or $Task.Actions[0].Arguments -ne (Get-TaskArguments $Config) -or
        $Task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18')) { throw 'Task does not match this installation; refusing to change it.' }
}

function Install-SessionTask {
    param($Config, [string] $SourcePath)
    if (Get-SessionTask $Config.TaskName) { throw 'Task already exists; refusing to replace it.' }
    New-ProtectedDirectory $Config.StatePath
    $registered = $false
    try {
        $destination = Join-Path $Config.StatePath 'session-limits.ps1'
        Copy-Item -LiteralPath $SourcePath -Destination $destination -ErrorAction Stop
        Write-AtomicJson (Join-Path $Config.StatePath 'config.local.json') $Config
        Assert-ProtectedPath $destination
        Assert-ProtectedPath (Join-Path $Config.StatePath 'config.local.json')
        if ((Get-FileHash -LiteralPath $SourcePath).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) { throw 'Installed script hash mismatch.' }
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-TaskArguments $Config)
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $null = Register-ScheduledTask -TaskName $Config.TaskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Windows session limits managed task' -ErrorAction Stop
        $registered = $true
        $task = Get-SessionTask $Config.TaskName
        Assert-OwnedTask $task $Config
        if ($task.Triggers[0].Repetition.Interval -ne 'PT1M' -or $task.Settings.MultipleInstances -ne 'IgnoreNew') { throw 'Task settings failed verification.' }
    } catch {
        if ($registered) { Unregister-ScheduledTask -TaskName $Config.TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop }
        throw
    }
    Write-Output 'Installed and verified. Enforcement starts within one minute.'
}

function Uninstall-SessionTask {
    param($Config)
    $task = Get-SessionTask $Config.TaskName
    if (-not $task) { Write-Output 'Task is already absent.'; return }
    Assert-OwnedTask $task $Config
    Disable-ScheduledTask -TaskName $Config.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
    Stop-ScheduledTask -TaskName $Config.TaskName -TaskPath '\' -ErrorAction Stop
    Unregister-ScheduledTask -TaskName $Config.TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    if (Get-SessionTask $Config.TaskName) { throw 'Task removal failed verification.' }
    Write-Output 'Task removed. Installed files and usage state are retained.'
}

function Get-InteractiveSession {
    if (-not ('SessionLimits.NativeSessions' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace SessionLimits {
    public class Session { public int Id; public string User; public string Domain; }
    public static class NativeSessions {
        [StructLayout(LayoutKind.Sequential)]
        private struct Info { public int Id; public IntPtr Name; public int State; }
        [DllImport("wtsapi32.dll", EntryPoint="WTSEnumerateSessionsW", SetLastError=true)]
        private static extern bool Enumerate(IntPtr server, int reserved, int version, out IntPtr buffer, out int count);
        [DllImport("wtsapi32.dll", EntryPoint="WTSQuerySessionInformationW", SetLastError=true)]
        private static extern bool Query(IntPtr server, int id, int info, out IntPtr buffer, out int bytes);
        [DllImport("wtsapi32.dll")]
        private static extern void WTSFreeMemory(IntPtr buffer);
        private static string Read(int id, int kind) {
            IntPtr buffer; int bytes;
            if (!Query(IntPtr.Zero, id, kind, out buffer, out bytes)) throw new Win32Exception();
            try { return Marshal.PtrToStringUni(buffer) ?? ""; } finally { WTSFreeMemory(buffer); }
        }
        public static Session[] Active() {
            IntPtr buffer; int count;
            if (!Enumerate(IntPtr.Zero, 0, 1, out buffer, out count)) throw new Win32Exception();
            try {
                var result = new List<Session>();
                int size = Marshal.SizeOf(typeof(Info));
                for (int index = 0; index < count; index++) {
                    var info = (Info)Marshal.PtrToStructure(IntPtr.Add(buffer, index * size), typeof(Info));
                    if (info.State != 0 || info.Id == 0) continue;
                    string user = Read(info.Id, 5);
                    if (user.Length > 0) result.Add(new Session { Id=info.Id, User=user, Domain=Read(info.Id, 7) });
                }
                return result.ToArray();
            } finally { WTSFreeMemory(buffer); }
        }
    }
}
'@
    }
    [SessionLimits.NativeSessions]::Active()
}

function Resolve-SessionSid {
    param($Session)
    $account = New-Object Security.Principal.NTAccount($Session.Domain, $Session.User)
    $account.Translate([Security.Principal.SecurityIdentifier]).Value
}

function ConvertTo-LdapLiteral {
    param([string] $Value)
    $Value.Replace('\', '\5c').Replace('*', '\2a').Replace('(', '\28').Replace(')', '\29').Replace([string][char]0, '\00')
}

function Test-TargetMember {
    param([string] $Sid, [string] $Group)
    $groupSearch = $null; $userSearch = $null
    try {
        $groupSearch = New-Object DirectoryServices.DirectorySearcher
        $groupSearch.Filter = '(&(objectClass=group)(sAMAccountName={0}))' -f (ConvertTo-LdapLiteral $Group)
        $groupSearch.ClientTimeout = [timespan]::FromSeconds(10)
        $group = $groupSearch.FindOne()
        if (-not $group) { throw 'Target group was not found.' }
        $userSearch = New-Object DirectoryServices.DirectorySearcher
        $userSearch.Filter = '(&(objectClass=user)(objectSid={0}))' -f (ConvertTo-LdapLiteral $Sid)
        $userSearch.ClientTimeout = [timespan]::FromSeconds(10)
        $user = $userSearch.FindOne()
        if (-not $user) { return $false }
        return $user.Properties['memberof'] -contains $group.Properties['distinguishedname'][0]
    } finally {
        if ($groupSearch) { $groupSearch.Dispose() }
        if ($userSearch) { $userSearch.Dispose() }
    }
}

function Write-EnforcementLog {
    param($Config, [string] $Message)
    Add-Content -LiteralPath (Join-Path $Config.StatePath 'enforcement.log') -Value "$(Get-Date -Format o) $Message" -ErrorAction Stop
}

function Send-SessionWarning {
    param([int] $Id, [string] $Message)
    & (Join-Path $env:SystemRoot 'System32\msg.exe') $Id /TIME:60 $Message 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Session message failed.' }
}

function Stop-InteractiveSession {
    param([int] $Id)
    & (Join-Path $env:SystemRoot 'System32\logoff.exe') $Id 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Session sign-out failed.' }
}

function Invoke-EnforcementTick {
    param($Config)
    Assert-ProtectedPath $Config.StatePath
    $lock = [IO.File]::Open((Join-Path $Config.StatePath 'tick.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        $accounts = @{}
        foreach ($session in @(Get-InteractiveSession)) {
            try { $sid = Resolve-SessionSid $session } catch { Write-EnforcementLog $Config 'WARN identity lookup failed; session skipped.'; continue }
            if (-not $accounts.ContainsKey($sid)) { $accounts[$sid] = @() }
            $accounts[$sid] += $session
        }
        $now = Get-Date
        foreach ($sid in $accounts.Keys) {
            try { $member = Test-TargetMember $sid $Config.TargetGroup }
            catch { Write-EnforcementLog $Config "WARN directory lookup failed; skipped $sid"; continue }
            if (-not $member) { continue }
            $path = Join-Path $Config.StatePath "$sid.json"
            try { $state = Read-UsageState $path $now }
            catch { Write-EnforcementLog $Config "WARN corrupt or unreadable state; skipped $sid"; continue }
            $decision = Get-UsageDecision $state $now $Config.WindowStart $Config.WindowEnd $Config.DailyBudgetMinutes $Config.WarningMinutes
            # Persist usage before any disruptive action, so write failure cannot sign someone out.
            Write-AtomicJson $path $decision.State
            Write-EnforcementLog $Config "$sid used=$($decision.State.Minutes) remaining=$($decision.Remaining) reason=$($decision.Reason)"
            $sessions = $accounts[$sid]
            foreach ($session in $sessions) {
                # Session IDs can be recycled; check the owner again immediately before acting.
                $current = @(Get-InteractiveSession | Where-Object { $_.Id -eq $session.Id })
                if ($current.Count -ne 1 -or (Resolve-SessionSid $current[0]) -ne $sid) { continue }
                if ($decision.Reason) {
                    try { Send-SessionWarning $session.Id "Session limit reached: $($decision.Reason). Signing out now." }
                    catch { Write-EnforcementLog $Config 'WARN final message failed.' }
                    Stop-InteractiveSession $session.Id
                } elseif ($decision.Warnings.Count -gt 0) {
                    Send-SessionWarning $session.Id "$([math]::Ceiling($decision.Remaining)) minutes remain. Please save your work."
                }
            }
            foreach ($threshold in $decision.Warnings) { $decision.State.Warned += "warn-$threshold" }
            Write-AtomicJson $path $decision.State
        }
    } finally { $lock.Dispose() }
}

function Invoke-SessionLimits {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([string] $Operation, $Config, [switch] $Apply, [string] $SourcePath)
    if ($Operation -ne 'Status') {
        $Config | ConvertTo-Json -Depth 4 | Write-Output
        if (-not $Apply) { Write-Output "Preview: $Operation needs -Apply; no state changed."; return }
        if (-not $PSCmdlet.ShouldProcess($Config.TaskName, "$Operation session policy")) { return }
    }
    Assert-Windows
    if ($Operation -eq 'Status') {
        $task = Get-SessionTask $Config.TaskName
        if (-not $task) { Write-Output 'Task absent.'; return }
        Assert-OwnedTask $task $Config
        $task | Select-Object TaskName, State
        Get-ScheduledTaskInfo -TaskName $Config.TaskName -TaskPath '\'
        return
    }
    Assert-Elevated
    switch ($Operation) {
        'Install' { Install-SessionTask $Config $SourcePath }
        'Uninstall' { Uninstall-SessionTask $Config }
        'Tick' { Invoke-EnforcementTick $Config }
        default { throw 'Unknown operation.' }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        if ($Help) {
            Write-Output 'session-limits.ps1 -Mode Install|Uninstall|Status|Tick [-ConfigPath PATH] [-Apply] [-WhatIf]'
            Write-Output 'Parameters override explicit JSON config, then defaults. Install, Uninstall and Tick preview without -Apply.'
            exit 0
        }
        $configuration = Resolve-Configuration $ConfigPath $PSBoundParameters
        Invoke-SessionLimits -Operation $Mode -Config $configuration -Apply:$Apply -SourcePath $PSCommandPath -WhatIf:$WhatIfPreference
        exit 0
    } catch { Write-Error $_ -ErrorAction Continue; exit 1 }
}
