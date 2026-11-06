import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
ENTRY = TOOL / "bootstrap-workstation.ps1"
CONFIGURATOR = TOOL / "configure.ps1"
PWSH = shutil.which("pwsh") or str(Path.home() / ".local/bin/pwsh")

VALID_ED25519_KEY = (
    "ssh-ed25519"
    " AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"
    " admin@example.com"
)
VALID_RSA_KEY = (
    "ssh-rsa"
    " AAAAB3NzaC1yc2EAAAADAQABAAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"
    "ISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+P0BBQkNERUZHSElKS0xNTk9QUVJTV"
    "FVWV1hZWltcXV5fYGFiY2Q="
    " user@example.net"
)
VALID_ECDSA_KEY = (
    "ecdsa-sha2-nistp256"
    " AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBAAECAwQFBgcICQo"
    "LDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+P0A="
    " test@example.org"
)


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
    "name",
    [
        "WS-WORKSTATION",
        "PC-01",
        "A",
        "W11-PRO-001",
        "15-CHARS-NAME1",
    ],
)
def test_computer_name_validation_valid(name):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidComputerName {quote(name)}"
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "12345",
        "0",
        "TOOLONGCOMPUTERNAME123",
        "BAD_NAME",
        "BAD.NAME",
        "BAD NAME",
        "NAME!",
    ],
)
def test_computer_name_validation_invalid(name):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidComputerName {quote(name)}",
        success=False,
    )
    assert result.returncode != 0
    assert "Invalid computer name" in result.stderr


@pytest.mark.parametrize(
    "key_text",
    [
        VALID_ED25519_KEY,
        VALID_RSA_KEY,
        VALID_ECDSA_KEY,
    ],
)
def test_public_key_validation_valid(key_text):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidPublicKey {quote(key_text)}"
    )
    assert result.returncode == 0


def pem(label, line):
    # Assembled at runtime so secret scanners don't flag these fake fixtures.
    marker = "-" * 5
    return f"{marker}BEGIN {label}{marker}\n{line}\n{marker}END {label}{marker}"


@pytest.mark.parametrize(
    "private_key",
    [
        pem(
            "OPENSSH PRIVATE KEY",
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
        ),
        pem(
            "RSA PRIVATE KEY",
            "MIIEowIBAAKCAQEA0ExamplePrivateKeyMaterialForTestCasesOnlyNotARealKey",
        ),
        pem(
            "EC PRIVATE KEY",
            "MHcCAQEEIExampleEcPrivateKeyMaterialForUnitTestingPurposesOnly",
        ),
        (
            "Proc-Type: 4,ENCRYPTED\n"
            "DEK-Info: DES-EDE3-CBC,1234567890ABCDEF\n"
            "ExampleKeyMaterial"
        ),
        ("PuTTY-User-Key-File-2: ssh-rsa\nEncryption: none\nComment: imported-key"),
    ],
)
def test_public_key_validation_rejects_private_keys(private_key):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidPublicKey {quote(private_key)}",
        success=False,
    )
    assert result.returncode != 0
    assert "contains a private key" in result.stderr


@pytest.mark.parametrize(
    "malformed_key",
    [
        "",
        "   ",
        "ssh-ed25519",
        "not a valid public key at all",
        "ssh-rsa not base64!@#$%",
    ],
)
def test_public_key_validation_rejects_malformed(malformed_key):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidPublicKey {quote(malformed_key)}",
        success=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "initial",
    [
        "",
        "# PasswordAuthentication yes\n# PubkeyAuthentication no\n",
        "PasswordAuthentication yes\nPubkeyAuthentication no\n",
        "Port 22\nListenAddress 0.0.0.0\n",
    ],
)
def test_sshd_config_rendering(initial):
    rendered = evaluate(f"Update-SshdConfigContent {quote(initial)}")
    assert "PasswordAuthentication no" in rendered
    assert "PubkeyAuthentication yes" in rendered
    # Test idempotency: re-rendering yields identical output
    rerendered = evaluate(f"Update-SshdConfigContent {quote(rendered)}")
    assert rendered == rerendered


def test_gate_refusal():
    # Calling entry point without confirmation phrase fails
    result = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')}",
        success=False,
    )
    assert result.returncode == 1
    assert "Refusing rename and reboot" in result.stderr
    assert "BOOTSTRAP AND REBOOT" in result.stderr

    # Calling entry point with wrong phrase fails
    result_wrong = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} "
        "-ConfirmationPhrase 'wrong phrase'",
        success=False,
    )
    assert result_wrong.returncode == 1
    assert "Refusing rename and reboot" in result_wrong.stderr

    # Calling entry point with lowercase phrase fails (case-sensitive check)
    result_case = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} "
        "-ConfirmationPhrase 'bootstrap and reboot'",
        success=False,
    )
    assert result_case.returncode == 1
    assert "Refusing rename and reboot" in result_case.stderr

    # Calling Assert-ConfirmationGate directly
    run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "Assert-ConfirmationGate -ConfirmationPhrase 'wrong'",
        success=False,
    )
    run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "Assert-ConfirmationGate -ConfirmationPhrase 'BOOTSTRAP AND REBOOT'"
    )
    run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        "Assert-ConfirmationGate -ConfirmationPhrase 'wrong' -WhatIf"
    )

    # Calling entry point with -WhatIf succeeds without confirmation phrase
    result_whatif = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} -WhatIf"
    )
    assert "What if:" in result_whatif.stdout


def test_config_precedence(tmp_path):
    config_file = tmp_path / "config.test.json"
    config_file.write_text(
        json.dumps(
            {
                "ComputerName": "CFG-WORKSTATION",
                "AuthorizedKey": VALID_ED25519_KEY,
                "AllowedRemoteAddresses": "198.51.100.0/24",
                "DomainDnsTestName": "ad.example.net",
                "Restart": False,
            }
        )
    )

    # File values applied
    result = evaluate(f"Resolve-Configuration {quote(config_file)}")
    assert result["ComputerName"] == "CFG-WORKSTATION"
    assert result["AllowedRemoteAddresses"] == "198.51.100.0/24"
    assert result["DomainDnsTestName"] == "ad.example.net"
    assert result["Restart"] is False

    # CLI overrides file values
    overridden = evaluate(
        f"Resolve-Configuration {quote(config_file)} "
        "@{ComputerName='CLI-OVERRIDE'; Restart=$true}"
    )
    assert overridden["ComputerName"] == "CLI-OVERRIDE"
    assert overridden["AllowedRemoteAddresses"] == "198.51.100.0/24"
    assert overridden["Restart"] is True


def test_configurator_non_overwrite_and_whatif(tmp_path):
    output = tmp_path / "config.local.json"
    command = (
        f"& {quote(CONFIGURATOR)} -OutputPath {quote(output)} -ComputerName 'CUSTOM-WS'"
    )

    # -WhatIf does not create file
    run_ps(command + " -WhatIf")
    assert not output.exists()

    # Normal execution creates file
    run_ps(command)
    assert output.exists()
    contents = output.read_text()
    data = json.loads(contents)
    assert data["ComputerName"] == "CUSTOM-WS"

    # Refuses to overwrite without -Force
    second_run = run_ps(command, success=False)
    assert second_run.returncode == 1
    assert "Refusing to replace" in second_run.stderr
    assert output.read_text() == contents

    # -Force allows overwrite
    overwrite_cmd = (
        f"& {quote(CONFIGURATOR)} -OutputPath {quote(output)} "
        "-ComputerName 'REPLACED-WS' -Force"
    )
    run_ps(overwrite_cmd)
    new_data = json.loads(output.read_text())
    assert new_data["ComputerName"] == "REPLACED-WS"


@pytest.mark.parametrize("script", ["configure.ps1", "bootstrap-workstation.ps1"])
def test_help_and_dot_source_are_inert(script):
    result = run_ps(f"& {quote(TOOL / script)} -Help")
    assert result.returncode == 0
    assert "-" in result.stdout

    result_source = run_ps(f". {quote(TOOL / script)}; Write-Output 'loaded'")
    assert result_source.stdout.strip() == "loaded"


def test_unknown_config_field_rejected(tmp_path):
    config_file = tmp_path / "bad.json"
    config_file.write_text(
        json.dumps(
            {
                "ComputerName": "WS-TEST",
                "AuthorizedKey": VALID_ED25519_KEY,
                "UnknownField": "disallowed",
            }
        )
    )
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Resolve-Configuration {quote(config_file)}",
        success=False,
    )
    assert result.returncode != 0
    assert "Unknown configuration field" in result.stderr


@pytest.mark.parametrize(
    "key_text",
    [
        "# only a comment\n# another comment",
        "ssh-ed25519 " + "YWJj" * 12,
        VALID_RSA_KEY.replace("ssh-rsa ", "ssh-ed25519 ", 1),
        VALID_ED25519_KEY.replace("ssh-ed25519 ", "SSH-ED25519 ", 1),
        "ssh-ed25519 /////3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoL",
    ],
)
def test_public_key_requires_matching_wire_type(key_text):
    result = run_ps(
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"Assert-ValidPublicKey {quote(key_text)}",
        success=False,
    )
    assert result.returncode != 0
    assert "Invalid OpenSSH public key" in result.stderr


def test_public_key_allows_comments_with_a_valid_key():
    key_text = "# key owner\n" + VALID_ED25519_KEY
    assert evaluate(f"Test-PublicKey {quote(key_text)}")


@pytest.mark.parametrize(
    "scope",
    [
        "bad-scope",
        "192.0.2.999",
        "192.0.2.0/33",
        "2001:db8::/129",
        "127.1",
        "192.0.2.1,",
        "LocalSubnet/24",
        "192.0.2.0/-1",
        "2001:xyz::1",
    ],
)
def test_preview_rejects_invalid_remote_scope(scope):
    result = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} "
        f"-AllowedRemoteAddresses {quote(scope)} -WhatIf",
        success=False,
    )
    assert result.returncode == 1
    assert "AllowedRemoteAddresses" in result.stderr
    assert "What if:" not in result.stdout


@pytest.mark.parametrize(
    "scope",
    [
        "192.0.2.1",
        "192.0.2.0/0",
        "192.0.2.1/32",
        "2001:db8::1",
        "2001:db8::/64",
        "::/0",
        "::1/128",
        "Any",
        "LocalSubnet",
        "DNS",
        "DHCP",
        "WINS",
        "DefaultGateway",
        "Internet",
        "Intranet",
        "IntranetRemoteAccess",
        "PlayToDevice",
        "CaptivePortal",
        "LocalSubnet4",
        "LocalSubnet6",
        "DNS6",
    ],
)
def test_preview_accepts_remote_scope(scope):
    result = run_ps(
        f"& {quote(ENTRY)} -ConfigPath {quote(TOOL / 'config.example.json')} "
        f"-AllowedRemoteAddresses {quote(scope)} -WhatIf"
    )
    assert "What if:" in result.stdout


@pytest.mark.parametrize(
    "global_section",
    [
        "Port 22\n",
        "passwordauthentication yes\n  PubkeyAuthentication no\n",
        "PasswordAuthentication yes\nPasswordAuthentication yes\n",
    ],
)
@pytest.mark.parametrize(
    "match_section",
    [
        "Match Group administrators\n    AuthorizedKeysFile __PROGRAMDATA__/ssh/keys\n",
        "  Match User example\n    PasswordAuthentication yes\n"
        "    PubkeyAuthentication no\nMatch all\n    X11Forwarding no\n",
    ],
)
def test_sshd_global_authentication_preserves_match_blocks(
    global_section, match_section
):
    rendered = evaluate(
        f"Update-SshdConfigContent {quote(global_section + match_section)}"
    )
    assert rendered.endswith(match_section)
    global_result = rendered[: -len(match_section)]
    assert global_result.count("PasswordAuthentication no") == 1
    assert global_result.count("PubkeyAuthentication yes") == 1
    assert "PasswordAuthentication yes" not in global_result
    assert rendered == evaluate(f"Update-SshdConfigContent {quote(rendered)}")


def simulated_bootstrap(tmp_path, *, failure="", what_if=False):
    ssh_dir = tmp_path / "ProgramData" / "ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "sshd_config").write_text("Port 22\n")
    (ssh_dir / "ssh_host_ed25519_key.pub").write_text(VALID_ED25519_KEY)
    acl_output = tmp_path / "acl.txt"
    code = (
        f"$ErrorActionPreference='Stop'; . {quote(ENTRY)}; "
        f"$failure={quote(failure)}; $aclOutput={quote(acl_output)}; "
        f"New-PSDrive -Name C -PSProvider FileSystem -Root {quote(tmp_path)} "
        "| Out-Null; "
        f"$config=Resolve-Configuration {quote(TOOL / 'config.example.json')}; "
        "$config.Restart=$true; "
        r"""
function Assert-Windows {}
function Assert-Elevated {}
function Get-Tpm { @{TpmPresent=$true; TpmReady=$true} }
function Confirm-SecureBootUEFI { $true }
function Get-CimInstance { @{Caption='Windows'; BuildNumber='test'} }
function Get-NetConnectionProfile { @() }
function Get-WindowsCapability { @{State='Installed'} }
function Get-Service {
    if ($failure -eq 'service') { return @{Status='Stopped'} }
    @{Status='Running'}
}
function Set-Service {}
function Start-Service {}
function Get-NetFirewallRule { $null }
function New-NetFirewallRule {}
function Enable-NetFirewallRule {}
function Restart-Service { 'SERVICE_RESTARTED' }
function Rename-Computer { 'COMPUTER_RENAMED' }
function Restart-Computer { 'COMPUTER_REBOOTED' }
function Get-NetIPConfiguration { @() }
function Resolve-DnsName {
    if ($failure -eq 'dns') { throw 'simulated DNS failure' }
    if ($failure -eq 'dns-empty') { return }
    @{IPAddress='192.0.2.53'}
}
function ssh-keygen {
    $global:LASTEXITCODE = 0
    if ($failure -eq 'fingerprint') { $global:LASTEXITCODE = 1; return }
    '256 SHA256:example user@example.com (ED25519)'
}
$global:acl = [pscustomobject]@{
    Sddl='O:BAG:SYD:P(A;;FA;;;BA)(A;;FA;;;SY)(A;;FR;;;WD)(D;;FW;;;BU)'
}
$global:acl | Add-Member ScriptMethod SetSecurityDescriptorSddlForm {
    param($sddl)
    $this.Sddl = $sddl
}
function Get-Acl { $global:acl }
function Set-Acl {
    [CmdletBinding()]
    param($LiteralPath, $AclObject)
    if ($failure -eq 'acl-write') { Write-Error 'simulated ACL write failure'; return }
    $global:acl = $AclObject
}
function icacls.exe {
    $global:LASTEXITCODE = 0
    if ($failure -eq 'icacls') { $global:LASTEXITCODE = 5 }
    if ($failure -eq 'acl-throw') { throw 'simulated ACL failure' }
}
if ($failure -eq 'host-key') {
    Remove-Item 'C:\ProgramData\ssh\ssh_host_ed25519_key.pub'
}
try {
"""
        "Invoke-WorkstationBootstrap -Config $config "
        "-ConfirmationPhrase 'BOOTSTRAP AND REBOOT' "
        + ("-WhatIf" if what_if else "")
        + "\n} finally { $global:acl.Sddl | Set-Content -LiteralPath $aclOutput }"
    )
    return run_ps(code, success=False), acl_output


def test_authorized_keys_acl_replaces_all_previous_entries(tmp_path):
    result, acl_output = simulated_bootstrap(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    sddl = acl_output.read_text().strip()
    assert sddl.split("D:")[1] == "P(A;;FA;;;BA)(A;;FA;;;SY)"
    assert "SERVICE_RESTARTED" in result.stdout
    assert "COMPUTER_RENAMED" in result.stdout


@pytest.mark.parametrize("failure", ["icacls", "acl-write", "acl-throw"])
def test_acl_failure_stops_restart_rename_and_reboot(tmp_path, failure):
    result, _ = simulated_bootstrap(tmp_path, failure=failure)
    assert result.returncode != 0
    assert "ACL" in result.stderr or "icacls" in result.stderr
    assert "SERVICE_RESTARTED" not in result.stdout
    assert "COMPUTER_RENAMED" not in result.stdout
    assert "COMPUTER_REBOOTED" not in result.stdout


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("dns", "Domain DNS resolution failed"),
        ("dns-empty", "Domain DNS resolution failed"),
        ("host-key", "Host key file not found"),
        ("fingerprint", "Host key fingerprint failed"),
        ("service", "sshd is not running"),
    ],
)
def test_verification_failure_exits_without_reboot(tmp_path, failure, message):
    result, _ = simulated_bootstrap(tmp_path, failure=failure)
    assert result.returncode != 0
    assert message in result.stderr
    assert "COMPUTER_REBOOTED" not in result.stdout


def test_successful_verification_allows_requested_reboot(tmp_path):
    result, _ = simulated_bootstrap(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "192.0.2.53" in result.stdout
    assert "SHA256:example" in result.stdout
    assert "COMPUTER_REBOOTED" in result.stdout


def test_whatif_skips_windows_changes_and_verification(tmp_path):
    result, acl_output = simulated_bootstrap(tmp_path, failure="dns", what_if=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert "COMPUTER_REBOOTED" not in result.stdout
    assert "SERVICE_RESTARTED" not in result.stdout
    assert "COMPUTER_RENAMED" not in result.stdout
    assert "(A;;FR;;;WD)" in acl_output.read_text()
    ssh_dir = tmp_path / "ProgramData" / "ssh"
    assert (ssh_dir / "sshd_config").read_text() == "Port 22\n"
    assert not (ssh_dir / "administrators_authorized_keys").exists()


def test_firewall_any_replaces_an_existing_restricted_scope():
    result = evaluate(
        "function Assert-Windows {}; "
        "function Get-NetFirewallRule { @{Name='OpenSSH-Server-In-TCP'} }; "
        "function Set-NetFirewallRule { param($RemoteAddress) $RemoteAddress }; "
        "function Enable-NetFirewallRule {}; "
        "Set-OpenSshFirewallRule -AllowedRemoteAddresses Any"
    )
    assert result == "Any"
