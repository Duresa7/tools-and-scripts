import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
SCRIPT = TOOL / "recovery-lockdown.ps1"


def pwsh() -> str:
    executable = shutil.which("pwsh")
    fallback = Path.home() / ".local/bin/pwsh"
    if executable is None and fallback.is_file():
        executable = str(fallback)
    assert executable, "Install PowerShell 7 and add pwsh to PATH before running tests."
    return executable


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run(code: str, *, source: bool = True) -> subprocess.CompletedProcess[str]:
    prefix = "$ErrorActionPreference='Stop'; "
    if source:
        prefix += f". {quote(SCRIPT)}; "
    return subprocess.run(
        [pwsh(), "-NoProfile", "-NonInteractive", "-Command", prefix + code],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def success(code: str) -> str:
    result = run(code)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout.strip()


def test_dot_source_has_no_side_effects() -> None:
    assert success("'functions-only'") == "functions-only"


@pytest.mark.parametrize("status", ["Enabled", "Disabled", "disabled"])
def test_parse_reagentc(status: str) -> None:
    text = f"Recovery configuration\r\n  Windows RE status:  {status}\r\nOther: value"
    assert success(f"ConvertFrom-ReagentcOutput {quote(text)}") == status.capitalize()


@pytest.mark.parametrize(
    ("text", "exit_code"),
    [
        ("", 0),
        ("Windows RE status: Unknown", 0),
        ("Windows RE status: Disabled\nWindows RE status: Enabled", 0),
        ("Windows RE status: Disabled", 5),
        ("Status der Windows RE: Deaktiviert", 0),
        ("prefix Windows RE status: Disabled", 0),
    ],
)
def test_parser_fails_closed(text: str, exit_code: int) -> None:
    result = run(f"ConvertFrom-ReagentcOutput {quote(text)} {exit_code}")
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("state", "provider", "policy", "passed", "failures"),
    [
        ("Disabled", "1", "1", True, 0),
        ("Enabled", "1", "1", False, 1),
        ("Disabled", "$null", "1", False, 1),
        ("Disabled", "1", "0", False, 1),
        ("Unknown", "$null", "$null", False, 3),
    ],
)
def test_evaluate_all_controls(
    state: str, provider: str, policy: str, passed: bool, failures: int
) -> None:
    result = json.loads(
        success(f"Test-RecoveryState '{state}' {provider} {policy} | ConvertTo-Json")
    )
    assert result["Passed"] is passed
    assert len(result["Failures"]) == failures


def test_config_precedence_and_no_mutation() -> None:
    result = json.loads(
        success(
            "$local=@{TaskName='Local task'; DailyAt='05:30'}; "
            "$resolved=Merge-RecoveryConfig $local @{TaskName='CLI task'}; "
            "@{resolved=$resolved; local=$local} | ConvertTo-Json"
        )
    )
    assert result["resolved"]["TaskName"] == "CLI task"
    assert result["resolved"]["DailyAt"] == "05:30"
    assert result["resolved"]["StartupDelaySeconds"] == 60
    assert result["local"]["TaskName"] == "Local task"


@pytest.mark.parametrize(
    "values",
    [
        "@{Password='not-accepted'}",
        "@{DailyAt='25:00'}",
        "@{StartupDelaySeconds=0}",
        "@{StartupDelaySeconds='60'}",
        "@{VerificationTimeoutSeconds=9}",
        "@{TaskName='folder/task'}",
        "@{TaskPath='missing-slashes'}",
        "@{ScriptName='../escape.ps1'}",
        "@{LogName='recovery.ps1'}",
        "@{InstallRoot='C:\\'}",
        "@{InstallRoot='C:\\safe\\..\\escape'}",
        "@{PowerShellPath='powershell.exe'}",
        "@{InstallRoot='C:\\path\" -Command danger'}",
    ],
)
def test_invalid_configuration_is_rejected(values: str) -> None:
    assert run(f"Merge-RecoveryConfig {values}").returncode != 0


def test_example_config_and_all_markers() -> None:
    payload = json.loads((TOOL / "config.example.json").read_text())
    for key in payload:
        if not key.startswith("_comment"):
            assert payload[f"_comment_{key}"].startswith("CUSTOMIZE:")
    result = success(
        "Merge-RecoveryConfig "
        f"(Read-RecoveryConfig {quote(TOOL / 'config.example.json')}) "
        "| ConvertTo-Json"
    )
    assert json.loads(result)["TaskName"] == payload["TaskName"]


def test_configurator_writes_overrides_and_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "config.local.json"
    code = (
        f"& {quote(TOOL / 'configure.ps1')} -OutputPath {quote(output)} "
        "-TaskName 'Configured task' -DailyAt '04:15'"
    )
    first = run(code, source=False)
    assert first.returncode == 0, first.stderr
    original = output.read_bytes()
    assert json.loads(original)["TaskName"] == "Configured task"
    assert json.loads(original)["DailyAt"] == "04:15"
    second = run(code, source=False)
    assert second.returncode == 1
    assert "never replaced" in second.stderr
    assert output.read_bytes() == original


def test_invalid_config_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "config.local.json"
    result = run(
        f"& {quote(TOOL / 'configure.ps1')} -OutputPath {quote(output)} "
        "-DailyAt '99:00'",
        source=False,
    )
    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("mode", ["Install", "Uninstall", "Enforce"])
def test_explicit_gate_precedes_all_platform_calls(mode: str) -> None:
    result = run(
        "function Assert-RecoveryPlatform { throw 'UNEXPECTED-PLATFORM-CALL' }; "
        f"Invoke-RecoveryTool -Mode {mode} -Settings (Get-RecoveryDefaults)"
    )
    assert result.returncode != 0
    assert f"{mode.upper()}_RECOVERY_LOCKDOWN" in result.stderr
    assert "UNEXPECTED-PLATFORM-CALL" not in result.stderr


@pytest.mark.parametrize("mode", ["Install", "Uninstall", "Enforce"])
def test_whatif_does_not_reach_state_changes(mode: str) -> None:
    output = success(
        "function Assert-RecoveryPlatform { throw 'Unexpected platform call' }; "
        f"Invoke-RecoveryTool -Mode {mode} -Settings (Get-RecoveryDefaults) -WhatIf"
    )
    assert "What if:" in output
    assert output.endswith("0")


def test_install_cli_preview(tmp_path: Path) -> None:
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({"TaskName": "Preview task"}))
    result = run(
        f"& {quote(SCRIPT)} -Install -ConfigPath {quote(config)} "
        "-TaskName 'Override task' -WhatIf",
        source=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Override task" in result.stdout


def test_task_arguments_preserve_paths_with_spaces(tmp_path: Path) -> None:
    root = tmp_path / "folder with spaces"
    output = success(
        f"$s=Get-RecoveryDefaults; $s.InstallRoot={quote(root)}; "
        "Get-RecoveryTaskArguments $s"
    )
    assert f'-File "{root / "recovery-lockdown.ps1"}"' in output
    assert f'-ConfigPath "{root / "config.local.json"}"' in output
    assert "-Enforce" in output
    assert "-Approval ENFORCE_RECOVERY_LOCKDOWN" in output


MOCK_PLATFORM = """
function Assert-RecoveryPlatform {}
function Assert-ProtectedPath {}
function Assert-RecoverySystem {}
function Assert-TrustedInstallParent {}
"""


@pytest.mark.parametrize("enabled", [True, False])
def test_enforcement_disables_only_when_needed_and_reads_back(
    tmp_path: Path, enabled: bool
) -> None:
    before = "Enabled" if enabled else "Disabled"
    output = success(
        MOCK_PLATFORM
        + f"""
$script:disabled=0; $script:policy=0; $script:readback=0
function Invoke-Reagentc {{
    param($Settings, $Argument)
    if ($Argument -eq '/info') {{ return '{before}' }}
    if ($Argument -eq '/disable') {{ $script:disabled++ }}
}}
function Set-RecoveryPolicy {{ $script:policy++ }}
function Get-RecoveryState {{
    $script:readback++
    [pscustomobject]@{{WinRE='Disabled'; ProviderSet=1; AuthenticationRequirement=1}}
}}
$s=Get-RecoveryDefaults; $s.InstallRoot={quote(tmp_path)}
$code=Invoke-RecoveryTool -Mode Enforce -Settings $s -Approval ENFORCE_RECOVERY_LOCKDOWN
@{{code=$code; disabled=$script:disabled; policy=$script:policy;
readback=$script:readback}} | ConvertTo-Json -Compress
"""
    )
    counts = json.loads(output.splitlines()[-1])
    assert counts == {
        "code": 0,
        "disabled": int(enabled),
        "policy": 1,
        "readback": 1,
    }
    assert "Passed=True" in (tmp_path / "recovery.log").read_text()


def test_enforcement_failure_is_logged_and_propagated(tmp_path: Path) -> None:
    result = run(
        MOCK_PLATFORM
        + f"""
function Invoke-Reagentc {{ 'Disabled' }}
function Set-RecoveryPolicy {{ throw 'Bridge failed' }}
$s=Get-RecoveryDefaults; $s.InstallRoot={quote(tmp_path)}
Invoke-RecoveryTool -Mode Enforce -Settings $s -Approval ENFORCE_RECOVERY_LOCKDOWN
"""
    )
    assert result.returncode != 0
    assert "Bridge failed" in result.stderr
    assert "Enforcement failed" in (tmp_path / "recovery.log").read_text()


def test_failed_readback_returns_two(tmp_path: Path) -> None:
    output = success(
        MOCK_PLATFORM
        + f"""
function Invoke-Reagentc {{ 'Disabled' }}
function Set-RecoveryPolicy {{}}
function Get-RecoveryState {{
    [pscustomobject]@{{WinRE='Enabled'; ProviderSet=1; AuthenticationRequirement=0}}
}}
$s=Get-RecoveryDefaults; $s.InstallRoot={quote(tmp_path)}
Invoke-RecoveryTool -Mode Enforce -Settings $s -Approval ENFORCE_RECOVERY_LOCKDOWN
"""
    )
    assert output.endswith("2")
    assert "Passed=False" in (tmp_path / "recovery.log").read_text()


@pytest.mark.parametrize("existing", [True, False])
def test_mdm_policy_uses_signed_integer(existing: bool) -> None:
    instance = (
        "[pscustomobject]@{InstanceID='Security'; "
        "ParentID='./Vendor/MSFT/Policy/Config'; RecoveryEnvironmentAuthentication=0}"
        if existing
        else ""
    )
    output = success(
        f"function Get-CimInstance {{ {instance} }}; "
        """
function New-CimInstance { param($Namespace, $ClassName, $Property, $ErrorAction)
    $script:value=$Property.RecoveryEnvironmentAuthentication
}
function Set-CimInstance { param($CimInstance, $Property, $ErrorAction)
    $script:value=$Property.RecoveryEnvironmentAuthentication
}
Set-RecoveryPolicy
@{type=$script:value.GetType().Name; value=$script:value} | ConvertTo-Json
"""
    )
    assert json.loads(output) == {"type": "Int32", "value": 1}


def test_install_refuses_existing_directory_before_task_calls(tmp_path: Path) -> None:
    result = run(
        f"$s=Get-RecoveryDefaults; $s.InstallRoot={quote(tmp_path)}; "
        "function Get-RecoveryTask { throw 'Unexpected task call' }; "
        f"Install-RecoveryTask $s {quote(SCRIPT)}"
    )
    assert result.returncode != 0
    assert "InstallRoot already exists" in result.stderr


def test_uninstall_refuses_foreign_task_before_mutating() -> None:
    result = run(
        MOCK_PLATFORM
        + """
function Get-RecoveryTask {
    [pscustomobject]@{Description='Unrelated task'; Actions=@()}
}
function Disable-ScheduledTask { throw 'Unexpected mutation' }
$s=Get-RecoveryDefaults
Invoke-RecoveryTool -Mode Uninstall -Settings $s -Approval UNINSTALL_RECOVERY_LOCKDOWN
"""
    )
    assert result.returncode != 0
    assert "refusing to manage" in result.stderr
    assert "Unexpected mutation" not in result.stderr


def test_uninstall_stops_then_removes_and_verifies() -> None:
    output = success(
        MOCK_PLATFORM
        + """
$script:events=@(); $script:removed=$false
function Get-RecoveryTask {
    if (-not $script:removed) { [pscustomobject]@{State='Ready'} }
}
function Assert-RecoveryTask {}
function Disable-ScheduledTask { $script:events+='disable' }
function Stop-ScheduledTask { $script:events+='stop' }
function Unregister-ScheduledTask { $script:events+='remove'; $script:removed=$true }
$args=@{Mode='Uninstall'; Settings=(Get-RecoveryDefaults)
Approval='UNINSTALL_RECOVERY_LOCKDOWN'}
$code=Invoke-RecoveryTool @args
@{code=$code; events=$script:events} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(output.splitlines()[-1]) == {
        "code": 0,
        "events": ["disable", "stop", "remove"],
    }


def test_check_is_read_only(tmp_path: Path) -> None:
    output = success(
        MOCK_PLATFORM
        + f"""
function Get-RecoveryTask {{ 'task' }}
function Assert-RecoveryTask {{}}
function Get-RecoveryState {{
    [pscustomobject]@{{WinRE='Disabled'; ProviderSet=1; AuthenticationRequirement=1}}
}}
function Set-RecoveryPolicy {{ throw 'Unexpected mutation' }}
function Add-Content {{ throw 'Unexpected log write' }}
$s=Get-RecoveryDefaults; $s.InstallRoot={quote(tmp_path)}
function Read-RecoveryConfig {{ $s }}
function Merge-RecoveryConfig {{ param($Local) $Local }}
Invoke-RecoveryTool -Mode Check -Settings $s
"""
    )
    assert output.endswith("0")
    assert "PASS:" in output
    assert not list(tmp_path.iterdir())


TASK_FIXTURE = """
$s=Get-RecoveryDefaults; $s.InstallRoot=$TestRoot
$boot=[pscustomobject]@{
    CimClass=@{CimClassName='MSFT_TaskBootTrigger'}; Delay='PT1M'; Enabled=$true
}
$daily=[pscustomobject]@{
    CimClass=@{CimClassName='MSFT_TaskDailyTrigger'}
    StartBoundary=([datetime]::Today.AddHours(3).ToString('s'))
    DaysInterval=1; Enabled=$true
}
$task=[pscustomobject]@{
    Description='Managed by windows-recovery-lockdown.'
    Actions=@(@{Execute=$s.PowerShellPath; Arguments=(Get-RecoveryTaskArguments $s)})
    Principal=@{UserId='SYSTEM'; LogonType='ServiceAccount'; RunLevel='Highest'}
    Triggers=@($boot, $daily)
    Settings=@{Enabled=$true; StartWhenAvailable=$true; MultipleInstances='IgnoreNew'}
    State='Ready'
}
"""


@pytest.mark.parametrize(
    "change",
    [
        "",
        "$task.Principal.UserId='operator'",
        "$task.Actions[0].Arguments='different command'",
        "$boot.Delay='PT5M'",
        "$daily.DaysInterval=2",
        "$task.Triggers=@($boot)",
        "$task.Settings.Enabled=$false",
    ],
)
def test_task_identity_and_schedule(tmp_path: Path, change: str) -> None:
    result = run(
        f"$TestRoot={quote(tmp_path)}; "
        + TASK_FIXTURE
        + change
        + "; Assert-RecoveryTask $task $s -Schedule"
    )
    if not change:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0


@pytest.mark.parametrize("task_result", [0, 2])
def test_install_registers_both_triggers_and_verifies_first_run(
    tmp_path: Path, task_result: int
) -> None:
    destination = tmp_path / "installed"
    result = run(
        MOCK_PLATFORM
        + f"""
$script:registered=$null; $script:started=$false
function Assert-NoReparsePoint {{}}
function Set-RecoveryAcl {{}}
function Get-RecoveryTask {{ $script:registered }}
function New-ScheduledTaskAction {{
    param($Execute, $Argument)
    [pscustomobject]@{{Execute=$Execute; Arguments=$Argument}}
}}
function New-ScheduledTaskTrigger {{
    param([switch]$AtStartup, [switch]$Daily, $At)
    if ($AtStartup) {{
        [pscustomobject]@{{
            CimClass=@{{CimClassName='MSFT_TaskBootTrigger'}}
            Delay=''; Enabled=$true
        }}
    }} else {{
        [pscustomobject]@{{
            CimClass=@{{CimClassName='MSFT_TaskDailyTrigger'}}
            StartBoundary=$At.ToString('s'); DaysInterval=1; Enabled=$true
        }}
    }}
}}
function New-ScheduledTaskPrincipal {{
    param($UserId, $LogonType, $RunLevel)
    [pscustomobject]@{{UserId=$UserId; LogonType=$LogonType; RunLevel=$RunLevel}}
}}
function New-ScheduledTaskSettingsSet {{
    param([switch]$StartWhenAvailable, $MultipleInstances,
        [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries)
    [pscustomobject]@{{Enabled=$true; StartWhenAvailable=$true
        MultipleInstances=$MultipleInstances}}
}}
function Register-ScheduledTask {{
    param($TaskName, $TaskPath, $Action, $Trigger, $Principal,
        $Settings, $Description, $ErrorAction)
    $script:registered=[pscustomobject]@{{
        Description=$Description; Actions=@($Action); Triggers=$Trigger
        Principal=$Principal; Settings=$Settings; State='Ready'
    }}
}}
function Get-ScheduledTaskInfo {{
    $time=[datetime]::MinValue
    if ($script:started) {{ $time=[datetime]::Now }}
    [pscustomobject]@{{LastRunTime=$time; LastTaskResult={task_result}}}
}}
function Start-ScheduledTask {{ $script:started=$true }}
function Start-Sleep {{}}
function Get-RecoveryState {{
    [pscustomobject]@{{WinRE='Disabled'; ProviderSet=1; AuthenticationRequirement=1}}
}}
$s=Get-RecoveryDefaults; $s.InstallRoot={quote(destination)}
$s.ReagentcPath={quote(SCRIPT)}; $s.PowerShellPath={quote(SCRIPT)}
$s.DailyAt='04:15'; $s.StartupDelaySeconds=90
Install-RecoveryTask $s {quote(SCRIPT)}
if (-not $script:started) {{ throw 'First run was not requested' }}
'verified'
"""
    )
    assert (destination / "config.local.json").is_file()
    assert (destination / "recovery-lockdown.ps1").read_bytes() == SCRIPT.read_bytes()
    settings = json.loads((destination / "config.local.json").read_text("utf-8-sig"))
    assert settings["DailyAt"] == "04:15"
    assert settings["StartupDelaySeconds"] == 90
    if task_result == 0:
        assert result.returncode == 0, result.stderr
        assert "verified" in result.stdout
    else:
        assert result.returncode != 0
        assert "Initial task run failed" in result.stderr
        assert "Task and files retained" in result.stdout


@pytest.mark.parametrize("filename", ["recovery-lockdown.ps1", "configure.ps1"])
def test_help_and_powershell_parser(filename: str) -> None:
    result = run(
        f"$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::"
        f"ParseFile({quote(TOOL / filename)},[ref]$t,[ref]$e); "
        "if ($e.Count) { throw ($e.Message -join '; ') }; "
        f"Get-Help {quote(TOOL / filename)} -Full | Out-String",
        source=False,
    )
    assert result.returncode == 0, result.stderr
    assert "config.local.json" in result.stdout or "Approval" in result.stdout
