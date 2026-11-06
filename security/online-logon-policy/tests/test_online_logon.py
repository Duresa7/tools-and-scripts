import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
SETTER = TOOL / "Set-WorkstationOnlineLogonPolicy.ps1"
CHECKER = TOOL / "Test-WorkstationOnlineLogonPolicy.ps1"
COMMON = TOOL / "OnlineLogon.Common.ps1"
PROVIDERS = [
    "{cb82ea12-9f71-446d-89e1-8d0924e1256e}",
    "{D6886603-9D2F-4EB2-B667-1971041FA96B}",
    "{8AF662BF-65A0-4D0A-A540-A338A999D36F}",
    "{BEC09223-B018-416D-A0AC-523971B639F5}",
    "{2135f72a-90b5-4ed3-a7f1-8bb705ac276a}",
    "{F8A1793B-7873-4046-B2A7-1F318747F427}",
]
SETTINGS = {
    "Domain": "ad.example.com",
    "Server": "dc.ad.example.com",
    "TargetOU": "OU=PilotComputers,DC=ad,DC=example,DC=com",
    "GpoName": "Require connected sign-in",
}
VALUES = """
$values = @{
    CachedLogonsCount = '0'; ForceUnlockLogon = 1; SyncForegroundPolicy = 1
    ExcludedCredentialProviders = (Get-ExcludedCredentialProviders)
    Enabled = 0; AllowDomainPINLogon = 0; BlockDomainPicturePassword = 1
    DisablePasswordChange = 0
}
"""


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(code: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh")
    if executable is None:
        candidate = Path.home() / ".local/bin/pwsh"
        if candidate.is_file():
            executable = str(candidate)
    assert executable, "Install PowerShell 7 and add pwsh to PATH to run these tests."
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def pure(code: str) -> object:
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(COMMON)}; "
        + code
        + " | ConvertTo-Json -Depth 8 -Compress"
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(SETTINGS), encoding="utf-8")
    return path


def test_provider_constants_and_password_retained() -> None:
    assert pure("Get-ExcludedCredentialProviders") == ",".join(PROVIDERS)


def test_registry_constants() -> None:
    settings = pure("@(Get-OnlineLogonRegistrySettings)")
    expected = [
        ("CachedLogonsCount", "String", "0"),
        ("ForceUnlockLogon", "DWord", 1),
        ("SyncForegroundPolicy", "DWord", 1),
        ("ExcludedCredentialProviders", "String", ",".join(PROVIDERS)),
        ("Enabled", "DWord", 0),
        ("AllowDomainPINLogon", "DWord", 0),
        ("BlockDomainPicturePassword", "DWord", 1),
    ]
    assert [(s["ValueName"], s["Type"], s["Value"]) for s in settings] == expected
    prefix = "HKLM\\SOFTWARE\\"
    assert [s["Key"] for s in settings] == [
        prefix + "Microsoft\\Windows NT\\CurrentVersion\\Winlogon",
        prefix + "Microsoft\\Windows NT\\CurrentVersion\\Winlogon",
        prefix + "Policies\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon",
        prefix + "Microsoft\\Windows\\CurrentVersion\\Policies\\System",
        prefix + "Policies\\Microsoft\\PassportForWork",
        prefix + "Policies\\Microsoft\\Windows\\System",
        prefix + "Policies\\Microsoft\\Windows\\System",
    ]


def test_matching_registry_passes() -> None:
    result = pure(VALUES + "Test-OnlineLogonRegistry $values")
    assert result["Pass"] is True
    assert all(result["Checks"].values())


@pytest.mark.parametrize(
    ("field", "value", "check"),
    [
        ("CachedLogonsCount", "'10'", "CachedLogonsDisabled"),
        ("ForceUnlockLogon", "0", "OnlineUnlockRequired"),
        ("SyncForegroundPolicy", "0", "ForegroundNetworkWait"),
        ("Enabled", "1", "HelloProvisioningDisabled"),
        ("AllowDomainPINLogon", "1", "ConveniencePinDisabled"),
        ("BlockDomainPicturePassword", "0", "PicturePasswordDisabled"),
        ("DisablePasswordChange", "1", "MachinePasswordChangesEnabled"),
        ("ExcludedCredentialProviders", "''", "AlternativeProvidersExcluded"),
    ],
)
def test_each_mismatch_fails(field: str, value: str, check: str) -> None:
    result = pure(VALUES + f"$values.{field}={value}; Test-OnlineLogonRegistry $values")
    assert result["Pass"] is False
    assert result["Checks"][check] is False


@pytest.mark.parametrize(
    "field",
    [
        "CachedLogonsCount",
        "ForceUnlockLogon",
        "SyncForegroundPolicy",
        "Enabled",
        "AllowDomainPINLogon",
        "BlockDomainPicturePassword",
        "ExcludedCredentialProviders",
    ],
)
def test_missing_required_value_fails(field: str) -> None:
    result = pure(
        VALUES + f"$values.Remove('{field}'); Test-OnlineLogonRegistry $values"
    )
    assert result["Pass"] is False


def test_absent_machine_password_setting_uses_enabled_default() -> None:
    result = pure(
        VALUES + "$values.Remove('DisablePasswordChange'); "
        "Test-OnlineLogonRegistry $values"
    )
    assert result["Pass"] is True


def test_password_provider_exclusion_fails() -> None:
    result = pure(
        VALUES + "$values.ExcludedCredentialProviders += "
        "',{60b78e88-ead8-445c-9cfd-0b87f74ea6cd}'; "
        "Test-OnlineLogonRegistry $values"
    )
    assert result["Pass"] is False
    assert result["Checks"]["PasswordProviderAvailable"] is False


def test_parameter_precedence_and_shared_name(config: Path) -> None:
    result = pure(
        f"Read-OnlineLogonConfig -Path {quote(config)} "
        "-Overrides @{GpoName='Pilot sign-in'; Server='other.ad.example.com'}"
    )
    assert result == SETTINGS | {
        "GpoName": "Pilot sign-in",
        "Server": "other.ad.example.com",
    }


@pytest.mark.parametrize(
    "change",
    [
        "$settings.Domain='';",
        "$settings.TargetOU='DC=ad,DC=example,DC=com';",
        "$settings.TargetOU='OU=PilotComputers,DC=example,DC=net';",
        "$settings.GpoName='CUSTOMIZE: policy';",
        "$settings.Password='not-allowed';",
    ],
)
def test_invalid_config_fails(config: Path, change: str) -> None:
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(COMMON)}; "
        f"$settings=Read-OnlineLogonConfig {quote(config)}; "
        + change
        + "Merge-OnlineLogonConfig $settings"
    )
    assert result.returncode != 0


@pytest.mark.parametrize("payload", ["[]", "null", "42", "{invalid"])
def test_bad_json_config_returns_json_failure(config: Path, payload: str) -> None:
    config.write_text(payload, encoding="utf-8")
    result = run_ps(f"& {quote(CHECKER)} -ConfigPath {quote(config)}")
    assert result.returncode == 1
    assert json.loads(result.stdout)["Pass"] is False


def test_missing_explicit_config_is_not_ignored(tmp_path: Path) -> None:
    result = run_ps(
        f"& {quote(CHECKER)} -ConfigPath {quote(tmp_path / 'missing.json')}"
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["Pass"] is False


def test_configurator_creates_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "config.local.json"
    arguments = " ".join(f"-{k} {quote(v)}" for k, v in SETTINGS.items())
    command = f"& {quote(TOOL / 'configure.ps1')} -OutputPath {quote(path)} {arguments}"
    first = run_ps(command)
    assert first.returncode == 0, first.stderr
    original = path.read_bytes()
    payload = json.loads(original)
    assert {key: payload[key] for key in SETTINGS} == SETTINGS
    assert sum("CUSTOMIZE:" in str(v) for v in payload.values()) == 4
    assert (
        not {"Password", "Token", "Credential", "ConfirmationPhrase"} & payload.keys()
    )
    second = run_ps(command)
    assert second.returncode == 1
    assert "Refusing to replace" in second.stderr
    assert path.read_bytes() == original


def test_exclusive_create_refuses_existing_file(config: Path) -> None:
    original = config.read_bytes()
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(TOOL / 'configure.ps1')}; "
        f"$settings=Read-OnlineLogonConfig {quote(config)}; "
        f"Write-OnlineLogonConfig -Path {quote(config)} -Settings $settings"
    )
    assert result.returncode != 0
    assert config.read_bytes() == original


@pytest.mark.parametrize(
    "script", ["configure.ps1", SETTER.name, CHECKER.name, COMMON.name]
)
def test_dot_source_does_not_execute(script: str) -> None:
    result = run_ps(
        "function Get-CimInstance { throw 'Unexpected read' }; "
        "function Import-Module { throw 'Unexpected module load' }; "
        f". {quote(TOOL / script)}; 'loaded'"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "loaded"


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ("", 1),
        ("-ConfirmationPhrase 'require online domain sign-in'", 1),
        ("-WhatIf", 0),
        ("-WhatIf -ConfirmationPhrase 'REQUIRE ONLINE DOMAIN SIGN-IN'", 0),
    ],
)
def test_gate_and_whatif_precede_module_load(
    config: Path, arguments: str, code: int
) -> None:
    result = run_ps(
        "function Import-Module { throw 'Unexpected module load' }; "
        f"& {quote(SETTER)} -ConfigPath {quote(config)} {arguments}"
    )
    assert result.returncode == code, result.stderr
    assert "Unexpected module load" not in result.stderr
    if code == 0:
        assert "What if:" in result.stdout
    else:
        assert "Refusing changes" in result.stderr


GPO_MOCKS = """
$global:policy = [pscustomobject]@{
    Id='policy-id'; GpoStatus='AllSettingsEnabled'
    DisplayName='Require connected sign-in'
}
$global:stored = @{}
$global:linked = $false
$global:created = 0
$global:updatedLink = 0
function Import-Module {}
function Get-GPO {
    param([switch]$All, $Guid, $Domain, $Server)
    if ($Guid -or $global:existing) { $global:policy }
}
function New-GPO { $global:created++; $global:policy }
function Get-GPInheritance {
    if ($global:badScope) { throw 'OU unavailable' }
    $links = @()
    if ($global:linked) {
        $links = @([pscustomobject]@{GpoId='policy-id'; Enabled=$true})
    }
    [pscustomobject]@{GpoLinks=$links}
}
function Set-GPRegistryValue {
    param($Guid, $Key, $ValueName, $Type, $Value, $Domain, $Server)
    $global:stored[$ValueName] = [pscustomobject]@{Type=$Type; Value=$Value}
}
function New-GPLink { $global:linked = $true }
function Set-GPLink { $global:updatedLink++; $global:linked = $true }
function Get-GPRegistryValue {
    param($Guid, $Key, $ValueName, $Domain, $Server)
    if ($global:corrupt) { return [pscustomobject]@{Type='String'; Value='bad'} }
    $global:stored[$ValueName]
}
"""


@pytest.mark.parametrize("existing", [False, True])
def test_create_or_reuse_and_verify(config: Path, existing: bool) -> None:
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(SETTER)}; "
        + GPO_MOCKS
        + f"$global:existing=${str(existing).lower()}; "
        + f"$global:linked=${str(existing).lower()}; "
        + f"$settings=Read-OnlineLogonConfig {quote(config)}; "
        + "$result=Invoke-SetOnlineLogonPolicy -Settings $settings "
        "-ConfirmationPhrase 'REQUIRE ONLINE DOMAIN SIGN-IN'; "
        "@{Result=$result; Created=$global:created; "
        "UpdatedLink=$global:updatedLink; Count=$global:stored.Count} "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["Result"]["Pass"] is True
    assert payload["Count"] == 7
    assert payload["Created"] == int(not existing)
    assert payload["UpdatedLink"] == int(existing)


@pytest.mark.parametrize("failure", ["corrupt", "badScope"])
def test_write_failure_is_nonzero(config: Path, failure: str) -> None:
    result = run_ps(
        GPO_MOCKS
        + f"$global:{failure}=$true; "
        + f"& {quote(SETTER)} -ConfigPath {quote(config)} "
        "-ConfirmationPhrase 'REQUIRE ONLINE DOMAIN SIGN-IN'"
    )
    assert result.returncode == 1
    expected = "readback failed" if failure == "corrupt" else "OU unavailable"
    assert expected in result.stderr


WORKSTATION_MOCKS = """
function Get-CimInstance {
    param($ClassName, $Namespace)
    if ($Namespace) { [pscustomobject]@{name='Require connected sign-in'} }
    else { [pscustomobject]@{Domain='ad.example.com'; PartOfDomain=$true} }
}
function Get-ItemProperty {
    param($LiteralPath)
    [pscustomobject]@{
        CachedLogonsCount='0'; ForceUnlockLogon=1; SyncForegroundPolicy=1
        ExcludedCredentialProviders=(Get-ExcludedCredentialProviders)
        Enabled=0; AllowDomainPINLogon=0; BlockDomainPicturePassword=1
        DisablePasswordChange=0
    }
}
function Get-Service { [pscustomobject]@{StartType='Automatic'} }
function Test-ComputerSecureChannel { $true }
function Set-ItemProperty { throw 'Unexpected write' }
function Set-GPRegistryValue { throw 'Unexpected write' }
"""


@pytest.mark.parametrize(
    ("change", "expected_check"),
    [
        ("", None),
        ("function Test-ComputerSecureChannel { $false };", "SecureChannel"),
        (
            "function Get-Service { [pscustomobject]@{StartType='Manual'} };",
            "NetlogonAutomatic",
        ),
    ],
)
def test_checker_json_and_exit_behavior(
    config: Path, change: str, expected_check: str | None
) -> None:
    result = run_ps(
        WORKSTATION_MOCKS + change + f"& {quote(CHECKER)} -ConfigPath {quote(config)}"
    )
    payload = json.loads(result.stdout)
    assert result.returncode == int(expected_check is not None), result.stderr
    assert payload["Pass"] is (expected_check is None)
    if expected_check:
        assert payload["Checks"][expected_check] is False


def test_checker_read_error_fails_closed(config: Path) -> None:
    result = run_ps(
        WORKSTATION_MOCKS
        + "function Get-ItemProperty { throw 'Access denied' }; "
        + f"& {quote(CHECKER)} -ConfigPath {quote(config)}"
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"Pass": False, "Error": "Access denied"}
