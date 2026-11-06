import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
ENTRY = TOOL / "session-limits.ps1"
PWSH = shutil.which("pwsh") or str(Path.home() / ".local/bin/pwsh")


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(code: str, *, success: bool = True) -> subprocess.CompletedProcess[str]:
    assert Path(PWSH).is_file(), "Install PowerShell 7 and add pwsh to PATH."
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def evaluate(code: str):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        + code
        + " | ConvertTo-Json -Depth 8 -Compress"
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("start", "end", "time", "inside", "left"),
    [
        ("09:00", "21:00", "08:59:59", False, 0),
        ("09:00", "21:00", "09:00:00", True, 720),
        ("09:00", "21:00", "20:59:30", True, 0.5),
        ("09:00", "21:00", "21:00:00", False, 0),
        ("22:00", "06:00", "21:59:59", False, 0),
        ("22:00", "06:00", "22:00:00", True, 480),
        ("22:00", "06:00", "23:30:00", True, 390),
        ("22:00", "06:00", "00:00:00", True, 360),
        ("22:00", "06:00", "05:59:30", True, 0.5),
        ("22:00", "06:00", "06:00:00", False, 0),
    ],
)
def test_window(start, end, time, inside, left):
    result = evaluate(
        f"Get-WindowDecision ([datetime]'2030-01-02T{time}') '{start}' '{end}'"
    )
    assert result == {"Inside": inside, "MinutesLeft": left}


@pytest.mark.parametrize("value", ["24:00", "9:00", "12:60", "", "noon"])
def test_invalid_time(value):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"ConvertTo-WindowMinute {quote(value)}",
        success=False,
    )
    assert result.returncode != 0


def decision(minutes=0, last="12:00:00", now="12:01:00", **changes):
    state = {
        "Day": "2030-01-02",
        "Minutes": minutes,
        "LastTick": f"2030-01-02T{last}.0000000+00:00" if last else None,
        "Warned": [],
    }
    state.update(changes)
    return evaluate(
        f"$s = ConvertFrom-UsageJson {quote(json.dumps(state))}; "
        f"Get-UsageDecision $s ([datetime]::SpecifyKind([datetime]'2030-01-02T{now}',"
        "[DateTimeKind]::Utc)) "
        "'09:00' '21:00' 120 @(10,3)"
    )


@pytest.mark.parametrize(
    ("last", "now", "added"),
    [
        (None, "12:01:00", 0),
        ("12:00:00", "12:01:30", 1.5),
        ("12:00:00", "12:05:00", 5),
        ("12:00:00", "12:05:01", 0),
        ("12:00:00", "14:00:00", 0),
        ("12:00:00", "11:59:00", 0),
        ("12:00:00", "12:00:00", 0),
    ],
)
def test_observed_usage_and_sleep_cap(last, now, added):
    assert decision(20, last, now)["State"]["Minutes"] == 20 + added


def test_rollover_resets_usage_and_warnings_even_in_overnight_window():
    result = evaluate(
        "$s = New-UsageState ([datetime]'2030-01-01T23:59:00'); "
        "$s.Minutes=120; $s.Warned=@('warn-10'); "
        "$s.LastTick='2030-01-01T23:59:00.0000000-05:00'; "
        "$d = Get-UsageDecision $s ([datetime]'2030-01-02T00:01:00') "
        "'22:00' '06:00' 120 @(10,3); "
        "[pscustomobject]@{ Decision=$d; Original=$s }"
    )
    assert result["Decision"]["State"]["Minutes"] == 0
    assert result["Decision"]["State"]["Warned"] == []
    assert result["Decision"]["Reason"] == ""
    assert result["Original"]["Minutes"] == 120


@pytest.mark.parametrize("minutes", [119, 120, 125])
def test_budget_exhaustion(minutes):
    result = decision(minutes)
    assert result["Reason"] == "budget exhausted"
    assert result["Warnings"] == []


def test_outside_window_wins_over_budget():
    result = decision(120, now="21:00:00")
    assert result["Reason"] == "outside window"


@pytest.mark.parametrize(
    ("minutes", "warned", "expected"),
    [(108, [], []), (109, [], [10]), (116, [], [10, 3]), (116, ["warn-10"], [3])],
)
def test_budget_warning_lead_time(minutes, warned, expected):
    assert decision(minutes, Warned=warned)["Warnings"] == expected


def test_window_warning_uses_earlier_limit():
    result = decision(0, last=None, now="20:58:00")
    assert result["Warnings"] == [10, 3]
    assert result["Remaining"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        "null",
        "[]",
        "{}",
        '{"Day":"2030-01-02","Minutes":-1,"LastTick":null,"Warned":[]}',
        '{"Day":"2030-01-02","Minutes":"0","LastTick":null,"Warned":[]}',
        '{"Day":"invalid","Minutes":0,"LastTick":null,"Warned":[]}',
        '{"Day":"2030-01-02","Minutes":0,"LastTick":"bad","Warned":[]}',
        '{"Day":"2030-01-02","Minutes":0,"LastTick":null,"Warned":null}',
    ],
)
def test_corrupt_state_is_preserved(tmp_path, payload):
    path = tmp_path / "usage.json"
    path.write_text(payload)
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Read-UsageState {quote(path)} ([datetime]'2030-01-02')",
        success=False,
    )
    assert result.returncode != 0
    assert "corrupt" in result.stderr
    assert path.read_text() == payload


def test_missing_state_and_atomic_replacement(tmp_path):
    path = tmp_path / "usage.json"
    result = evaluate(
        f"$s = Read-UsageState {quote(path)} ([datetime]'2030-01-02'); "
        f"Write-AtomicJson {quote(path)} $s; $s.Minutes=17.5; "
        f"Write-AtomicJson {quote(path)} $s; "
        f"Read-UsageState {quote(path)} ([datetime]'2030-01-02')"
    )
    assert result["Minutes"] == 17.5
    assert len(list(tmp_path.iterdir())) == 1


def test_config_precedence_and_defaults(tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text(
        json.dumps(
            {
                "TargetGroup": "example-policy",
                "StatePath": "C:\\ProgramData\\ExamplePolicy",
                "TaskName": "Example policy",
                "DailyBudgetMinutes": 90,
            }
        )
    )
    result = evaluate(f"Resolve-Configuration {quote(path)} @{{DailyBudgetMinutes=60}}")
    assert result["DailyBudgetMinutes"] == 60
    assert result["WindowStart"] == "09:00"
    assert result["TargetGroup"] == "example-policy"


@pytest.mark.parametrize(
    "override",
    [
        "WindowEnd='09:00'",
        "DailyBudgetMinutes=0",
        "WarningMinutes=@(-1)",
        "StatePath='relative'",
        "TaskName='bad\\name'",
        "TargetGroup=''",
    ],
)
def test_invalid_configuration(override):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Resolve-Configuration {quote(TOOL / 'config.example.json')} @{{{override}}}",
        success=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("script", ["configure.ps1", "session-limits.ps1"])
def test_help_and_dot_source_are_inert(script):
    result = run_ps(f"& {quote(TOOL / script)} -Help")
    assert "-" in result.stdout
    result = run_ps(f". {quote(TOOL / script)}; Write-Output 'loaded'")
    assert result.stdout.strip() == "loaded"


def test_configurator_no_overwrite_and_whatif(tmp_path):
    output = tmp_path / "config.local.json"
    command = (
        f"& {quote(TOOL / 'configure.ps1')} -OutputPath {quote(output)} "
        "-DailyBudgetMinutes 45"
    )
    run_ps(command + " -WhatIf")
    assert not output.exists()
    run_ps(command)
    contents = output.read_text()
    assert json.loads(contents)["DailyBudgetMinutes"] == 45
    result = run_ps(command, success=False)
    assert result.returncode == 1
    assert "Refusing to replace" in result.stderr
    assert output.read_text() == contents


@pytest.mark.parametrize("mode", ["Install", "Uninstall", "Tick"])
@pytest.mark.parametrize("flags", ["", "-Apply -WhatIf"])
def test_preview_gate_precedes_windows_calls(mode, flags):
    result = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} "
        f"-Mode {mode} {flags}"
    )
    assert "Preview:" in result.stdout or "What if:" in result.stdout


def test_tick_directory_failure_skips_actions(tmp_path):
    evaluate_tick(tmp_path, "throw 'unreachable'", "", expected="directory lookup")


def test_tick_corruption_skips_actions(tmp_path):
    (tmp_path / "S-1-5-21-1.json").write_text("broken")
    evaluate_tick(tmp_path, "$true", "", expected="corrupt")
    assert (tmp_path / "S-1-5-21-1.json").read_text() == "broken"


def evaluate_tick(tmp_path, membership, extra, expected):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "function Assert-ProtectedPath {} "
        "function Get-InteractiveSession { [pscustomobject]@{Id=1} } "
        "function Resolve-SessionSid { 'S-1-5-21-1' } "
        f"function Test-TargetMember {{ {membership} }} "
        "function Send-SessionWarning { throw 'unexpected message' } "
        "function Stop-InteractiveSession { throw 'unexpected sign-out' } "
        f"$c=[pscustomobject]@{{StatePath={quote(tmp_path)};TargetGroup='example'}}; "
        f"{extra}; Invoke-EnforcementTick $c"
    )
    assert result.returncode == 0
    assert expected in (tmp_path / "enforcement.log").read_text()


def test_ldap_escaping():
    assert evaluate("ConvertTo-LdapLiteral 'a*(b)\\c'") == r"a\2a\28b\29\5cc"


@pytest.mark.parametrize("minutes", [110, 120])
def test_tick_warns_or_signs_out_and_counts_account_once(tmp_path, minutes):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "function Assert-ProtectedPath {} "
        "function Get-Date { [datetime]::SpecifyKind("
        "[datetime]'2030-01-02T12:01:00', [DateTimeKind]::Utc) } "
        "function Get-InteractiveSession { "
        "[pscustomobject]@{Id=1}; [pscustomobject]@{Id=2} } "
        "function Resolve-SessionSid { 'S-1-5-21-1' } "
        "function Test-TargetMember { $true } "
        "function Send-SessionWarning { param($Id,$Message); 'MESSAGE '+$Id } "
        "function Stop-InteractiveSession { param($Id); 'SIGNOUT '+$Id } "
        f"$c=[pscustomobject]@{{StatePath={quote(tmp_path)};TargetGroup='example';"
        "WindowStart='09:00';WindowEnd='21:00';DailyBudgetMinutes=120;"
        "WarningMinutes=@(10,3)}; "
        "$s=New-UsageState (Get-Date); "
        f"$s.Minutes={minutes}; $s.LastTick='2030-01-02T12:00:00.0000000+00:00'; "
        "Write-AtomicJson (Join-Path $c.StatePath 'S-1-5-21-1.json') $s; "
        "Invoke-EnforcementTick $c"
    )
    assert result.stdout.count("MESSAGE") == 2
    assert result.stdout.count("SIGNOUT") == (2 if minutes == 120 else 0)
    state = json.loads((tmp_path / "S-1-5-21-1.json").read_text())
    assert state["Minutes"] == minutes + 1
    assert state["Warned"] == ([] if minutes == 120 else ["warn-10"])


def test_no_sessions_is_successful_and_releases_lock(tmp_path):
    run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "function Assert-ProtectedPath {} "
        "function Get-InteractiveSession { @() } "
        "function Test-TargetMember { throw 'unexpected directory call' } "
        f"$c=[pscustomobject]@{{StatePath={quote(tmp_path)}}}; "
        "Invoke-EnforcementTick $c; Invoke-EnforcementTick $c"
    )
    assert [path.name for path in tmp_path.iterdir()] == ["tick.lock"]


def test_uninstall_rejects_foreign_task_before_mutating():
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"$env:SystemRoot={quote(TOOL)}; "
        "function Get-SessionTask { [pscustomobject]@{Description='other'} } "
        "function Disable-ScheduledTask { throw 'unexpected mutation' } "
        "$c=[pscustomobject]@{TaskName='example';StatePath='example'}; "
        "Uninstall-SessionTask $c",
        success=False,
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert "unexpected mutation" not in result.stderr


def test_install_refuses_existing_task_before_creating_files():
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "function Get-SessionTask { [pscustomobject]@{TaskName='example'} } "
        "function New-ProtectedDirectory { throw 'unexpected mutation' } "
        "$c=[pscustomobject]@{TaskName='example';StatePath='example'}; "
        "Install-SessionTask $c 'unused'",
        success=False,
    )
    assert result.returncode != 0
    assert "Task already exists" in result.stderr
    assert "unexpected mutation" not in result.stderr


def test_state_write_failure_prevents_signout(tmp_path):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "function Assert-ProtectedPath {} "
        "function Get-InteractiveSession { [pscustomobject]@{Id=1} } "
        "function Resolve-SessionSid { 'S-1-5-21-1' } "
        "function Test-TargetMember { $true } "
        "function Read-UsageState { $s=New-UsageState (Get-Date); "
        "$s.Minutes=120; return $s } "
        "function Write-AtomicJson { throw 'disk failure' } "
        "function Stop-InteractiveSession { throw 'unexpected signout' } "
        f"$c=[pscustomobject]@{{StatePath={quote(tmp_path)};TargetGroup='example';"
        "WindowStart='09:00';WindowEnd='21:00';DailyBudgetMinutes=120;"
        "WarningMinutes=@(10,3)}; Invoke-EnforcementTick $c",
        success=False,
    )
    assert result.returncode != 0
    assert "disk failure" in result.stderr
    assert "unexpected signout" not in result.stderr
