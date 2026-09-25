import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = TOOL_DIR / "installer-signature-check.ps1"
CONFIGURATOR_PATH = TOOL_DIR / "configure.ps1"
EXAMPLE_CONFIG = TOOL_DIR / "config.example.json"

PWSH = shutil.which("pwsh")
if PWSH is None:
    candidate = Path.home() / ".local/bin/pwsh"
    if candidate.is_file():
        PWSH = str(candidate)


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(code: str, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    assert PWSH and Path(PWSH).is_file(), (
        "Install PowerShell 7 and add pwsh to PATH to run these tests."
    )
    wrapped_code = f"""
try {{
{code}
if ($null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}}
"""
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", wrapped_code],
        capture_output=True,
        text=True,
        check=check,
        timeout=30,
    )


def dot_source_eval(expr: str) -> subprocess.CompletedProcess[str]:
    code = f"$ErrorActionPreference = 'Stop'; . {quote(SCRIPT_PATH)}; {expr}"
    return run_ps(code)


@pytest.mark.parametrize("entry_point", ["script", "function"])
def test_signature_object_cannot_bypass_verification(tmp_path: Path, entry_point: str):
    installer = tmp_path / "unsigned.bin"
    installer.write_bytes(b"Unsigned installer")
    command = (
        f"& {quote(SCRIPT_PATH)}"
        if entry_point == "script"
        else "Invoke-InstallerSignatureCheck"
    )
    res = dot_source_eval(f"""
$sig = [pscustomobject]@{{
    Status = 'Valid'
    SignerCertificate = [pscustomobject]@{{ Subject = 'CN=Example Corp, C=US' }}
}}
{command} -InstallerPath {quote(installer)} -ExpectedPublisher 'Example Corp' `
    -SignatureObject $sig
""")
    assert res.returncode != 0, res.stdout + res.stderr
    assert "parameter name 'SignatureObject'" in res.stderr


def test_subject_matching_exact_cn():
    res = dot_source_eval(
        "Test-SubjectMatch 'CN=Example Corp, O=Different Org, C=US' 'Example Corp'"
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "True"


@pytest.mark.parametrize(
    ("subject", "publisher", "matches"),
    [
        ('OU="O=Example Corp, unrelated", CN=Other Corp, C=US', "Example Corp", False),
        ('OU="CN=Example Corp, unrelated", CN=Other Corp, C=US', "Example Corp", False),
        ("CN=Example Corp + O=Other, C=US", "Example Corp", True),
        ("CN=Other + O=Example Corp, C=US", "example corp", True),
        ('CN="Example ""Quoted"" Corp", C=US', 'Example "Quoted" Corp', True),
        ('CN="Example, Corp + Partners", C=US', "Example, Corp + Partners", True),
        (r"CN=Example\, Corp \+ Partners, C=US", "Example, Corp + Partners", True),
        (r'CN="Example \"Quoted\" Corp", C=US', 'Example "Quoted" Corp', True),
        (r"CN=Example\\Corp, C=US", "Example\\Corp", True),
        ("CN=Example Corp Ltd + O=Other, C=US", "Example Corp", False),
        ('CN="Example ""Quoted"" Corp Ltd", C=US', 'Example "Quoted" Corp', False),
        (r"OU=Other\, O=Example Corp, CN=Other, C=US", "Example Corp", False),
        ('OU="Other + O=Example Corp", CN=Other, C=US', "Example Corp", False),
        ('CN=Other, OU="unterminated, O=Example Corp', "Example Corp", False),
        ('CN=Example Corp, OU="unterminated', "Example Corp", False),
        ('CN="Example Corp"suffix, C=US', "Example Corp", False),
        ("CN=Example Corp, OU=trailing\\", "Example Corp", False),
        ("CN=Example Corp,", "Example Corp", False),
    ],
)
def test_distinguished_name_boundaries_and_escapes(subject, publisher, matches):
    res = dot_source_eval(f"Test-SubjectMatch {quote(subject)} {quote(publisher)}")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(matches)


def test_subject_matching_exact_o():
    res = dot_source_eval(
        "Test-SubjectMatch 'CN=Setup Utility, O=Example Corp, C=US' 'Example Corp'"
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "True"


def test_subject_matching_quoted_names():
    subj = 'CN="Example, Corp", O="Example Corp", C=US'
    res1 = dot_source_eval(f"Test-SubjectMatch {quote(subj)} 'Example, Corp'")
    assert res1.returncode == 0
    assert res1.stdout.strip() == "True"

    res2 = dot_source_eval(
        "Test-SubjectMatch 'CN=\"Example, Corp\", C=US' '\"Example, Corp\"'"
    )
    assert res2.returncode == 0
    assert res2.stdout.strip() == "True"

    res3 = dot_source_eval(
        "Test-SubjectMatch 'CN=Example Corp, C=US' '\"Example Corp\"'"
    )
    assert res3.returncode == 0
    assert res3.stdout.strip() == "True"


def test_subject_matching_lookalike_rejected():
    lookalikes = [
        "CN=Example Corp Inc, C=US",
        "CN=Example Corp., C=US",
        "CN=Example Corp (US), C=US",
        "CN=Setup, O=Example Corp Lookalike, C=US",
        "CN=Example Corp Fake, O=Example Corp Fake, C=US",
    ]
    for sub in lookalikes:
        res = dot_source_eval(f"Test-SubjectMatch {quote(sub)} 'Example Corp'")
        assert res.returncode == 0
        assert res.stdout.strip() == "False", f"Lookalike succeeded unexpectedly: {sub}"


def test_subject_matching_substring_tricks_rejected():
    tricks = [
        ("CN=MyExample Corp, C=US", "Example Corp"),
        ("CN=Example CorpSuffix, C=US", "Example Corp"),
        ("CN=Example Corp, C=US", "Corp"),
        ("CN=Example Corp, C=US", "Example"),
        ("CN=Example, C=US", "Example Corp"),
        ("OU=Example Corp, C=US", "Example Corp"),
    ]
    for sub, exp in tricks:
        res = dot_source_eval(f"Test-SubjectMatch {quote(sub)} {quote(exp)}")
        assert res.returncode == 0
        assert res.stdout.strip() == "False", (
            f"Substring trick succeeded unexpectedly: {sub} vs {exp}"
        )


def test_hash_comparison_case_insensitive():
    h1 = hashlib.sha256(b"Example installer").hexdigest()
    h2 = h1.upper()
    other_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    res1 = dot_source_eval(f"Test-HashMatch {quote(h1)} {quote(h2)}")
    assert res1.returncode == 0
    assert res1.stdout.strip() == "True"

    res2 = dot_source_eval(f"Test-HashMatch {quote(h2)} {quote(h1)}")
    assert res2.returncode == 0
    assert res2.stdout.strip() == "True"

    res3 = dot_source_eval(f"Test-HashMatch {quote(h1)} {quote(other_hash)}")
    assert res3.returncode == 0
    assert res3.stdout.strip() == "False"


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        ("host.example.com", "host.example.net", False),
        ("host.example.com", "host", False),
        ("host", "host.example.com", False),
        ("HOST.EXAMPLE.COM", "host.example.com", True),
        ("HOST", "host", True),
        ("host", "other", False),
        (" host ", "HOST", True),
        ("host", "", True),
        ("", "host", False),
    ],
)
def test_host_matching_preserves_domain_suffix(actual, expected, matches):
    res = dot_source_eval(f"Test-ComputerMatch {quote(actual)} {quote(expected)}")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(matches)


@pytest.mark.parametrize("empty_override", ["", "   "])
def test_empty_overrides_retain_configured_checks(tmp_path: Path, empty_override: str):
    config_file = tmp_path / "checks.json"
    configured = {"ExpectedSHA256": "ab" * 32, "ExpectedComputer": "host.example.com"}
    config_file.write_text(json.dumps(configured), encoding="utf-8")
    res = dot_source_eval(f"""
Resolve-Configuration -ConfigPath {quote(config_file)} -Overrides @{{
    ExpectedSHA256 = {quote(empty_override)}
    ExpectedComputer = {quote(empty_override)}
}} | ConvertTo-Json
""")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["ExpectedSHA256"] == configured["ExpectedSHA256"]
    assert data["ExpectedComputer"] == configured["ExpectedComputer"]


def test_missing_file(tmp_path: Path):
    non_existent = tmp_path / "missing-installer.exe"
    code = (
        f"& {quote(SCRIPT_PATH)} "
        f"-InstallerPath {quote(non_existent)} "
        f"-ExpectedPublisher 'Example Corp'"
    )
    res = run_ps(code)
    assert res.returncode == 1
    assert "Installer file not found" in (res.stdout + res.stderr)


def test_config_precedence(tmp_path: Path):
    config_file = tmp_path / "config.local.json"
    config_file.write_text(
        json.dumps(
            {
                "InstallerPath": "C:\\Staging\\setup.exe",
                "ExpectedPublisher": "Config Publisher",
                "ExpectedSHA256": "",
                "ExpectedComputer": "",
                "Format": "Table",
            }
        ),
        encoding="utf-8",
    )

    code = (
        f"$ErrorActionPreference = 'Stop'; . {quote(SCRIPT_PATH)}; "
        f"$cfg = Resolve-Configuration -ConfigPath {quote(config_file)} "
        f"-Overrides @{{ ExpectedPublisher = 'CLI Publisher'; Format = 'Json' }}; "
        f"$cfg | ConvertTo-Json -Compress"
    )
    res = run_ps(code)
    assert res.returncode == 0
    parsed = json.loads(res.stdout)
    assert parsed["InstallerPath"] == "C:\\Staging\\setup.exe"
    assert parsed["ExpectedPublisher"] == "CLI Publisher"
    assert parsed["Format"] == "Json"


def test_configurator_non_overwrite(tmp_path: Path):
    target = tmp_path / "custom.local.json"
    code_init = f"& {quote(CONFIGURATOR_PATH)} -OutputPath {quote(target)}"
    res1 = run_ps(code_init)
    assert res1.returncode == 0
    assert target.is_file()

    # Second invocation without -Force must fail
    res2 = run_ps(code_init)
    assert res2.returncode != 0
    assert "Refusing to replace existing configuration" in (res2.stdout + res2.stderr)

    # Invocation with -Force must succeed
    code_force = f"& {quote(CONFIGURATOR_PATH)} -OutputPath {quote(target)} -Force"
    res3 = run_ps(code_force)
    assert res3.returncode == 0


def test_json_output_shape_with_injected_signature(tmp_path: Path):
    installer = tmp_path / "test_installer.bin"
    payload = b"Example installer binary content for testing."
    installer.write_bytes(payload)
    sha256_hex = hashlib.sha256(payload).hexdigest()

    code = f"""
$ErrorActionPreference = 'Stop'
. {quote(SCRIPT_PATH)}
function Get-InstallerSignature {{
    [pscustomobject]@{{
    Status = 'Valid'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=Example Corp, O=Example Corp, C=US'
    }}
}}
}}
Invoke-InstallerSignatureCheck `
    -InstallerPath {quote(installer)} `
    -ExpectedPublisher 'Example Corp' `
    -ExpectedSHA256 {quote(sha256_hex)} `
    -Format Json | ConvertTo-Json -Depth 4
"""
    res = run_ps(code)
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)

    assert "Computer" in data
    assert "Path" in data
    assert "Bytes" in data
    assert "SHA256" in data
    assert "Signature" in data
    assert "Publisher" in data

    assert data["Bytes"] == len(payload)
    assert data["SHA256"].lower() == sha256_hex.lower()
    assert data["Signature"] == "Valid"
    assert data["Publisher"] == "CN=Example Corp, O=Example Corp, C=US"
    assert Path(data["Path"]).resolve() == installer.resolve()


def test_bad_input_or_config_exit_code_2(tmp_path: Path):
    # 1. Non-existent config
    bad_cfg = tmp_path / "non_existent.json"
    res1 = run_ps(f"& {quote(SCRIPT_PATH)} -ConfigPath {quote(bad_cfg)}")
    assert res1.returncode == 2

    # 2. Invalid JSON config
    malformed_cfg = tmp_path / "malformed.json"
    malformed_cfg.write_text("{ this is not json }", encoding="utf-8")
    res2 = run_ps(f"& {quote(SCRIPT_PATH)} -ConfigPath {quote(malformed_cfg)}")
    assert res2.returncode == 2

    # 3. Unknown property in config
    unknown_cfg = tmp_path / "unknown_prop.json"
    unknown_cfg.write_text(
        json.dumps(
            {
                "InstallerPath": "C:\\setup.exe",
                "ExpectedPublisher": "Example Corp",
                "UnrecognizedField": "fail",
            }
        ),
        encoding="utf-8",
    )
    res3 = run_ps(f"& {quote(SCRIPT_PATH)} -ConfigPath {quote(unknown_cfg)}")
    assert res3.returncode == 2

    # 4. Invalid ExpectedSHA256 format
    res4 = run_ps(
        f"& {quote(SCRIPT_PATH)} "
        f"-InstallerPath {quote(tmp_path / 'foo')} "
        f"-ExpectedPublisher 'Example Corp' "
        f"-ExpectedSHA256 'not-a-valid-sha256'"
    )
    assert res4.returncode == 2

    # 5. Missing required settings (empty publisher)
    res5 = run_ps(f"& {quote(SCRIPT_PATH)} -InstallerPath {quote(tmp_path / 'foo')}")
    assert res5.returncode == 2


def test_check_failures_exit_code_1(tmp_path: Path):
    installer = tmp_path / "installer.bin"
    installer.write_bytes(b"Sample payload")

    # Invalid signature status
    code_invalid_sig = f"""
$ErrorActionPreference = 'Stop'
. {quote(SCRIPT_PATH)}
function Get-InstallerSignature {{
    [pscustomobject]@{{
    Status = 'HashMismatch'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=Example Corp, C=US'
    }}
}}
}}
Invoke-InstallerSignatureCheck `
    -InstallerPath {quote(installer)} `
    -ExpectedPublisher 'Example Corp'
"""
    res1 = run_ps(code_invalid_sig)
    assert res1.returncode == 1
    assert "Installer signature is not valid: HashMismatch" in res1.stderr

    # Publisher mismatch
    code_pub_mismatch = f"""
$ErrorActionPreference = 'Stop'
. {quote(SCRIPT_PATH)}
function Get-InstallerSignature {{
    [pscustomobject]@{{
    Status = 'Valid'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=Different Corp, C=US'
    }}
}}
}}
Invoke-InstallerSignatureCheck `
    -InstallerPath {quote(installer)} `
    -ExpectedPublisher 'Example Corp'
"""
    res2 = run_ps(code_pub_mismatch)
    assert res2.returncode == 1
    assert "does not match expected publisher" in res2.stderr

    # Hash mismatch
    code_hash_mismatch = f"""
$ErrorActionPreference = 'Stop'
. {quote(SCRIPT_PATH)}
function Get-InstallerSignature {{
    [pscustomobject]@{{
    Status = 'Valid'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=Example Corp, C=US'
    }}
}}
}}
Invoke-InstallerSignatureCheck `
    -InstallerPath {quote(installer)} `
    -ExpectedPublisher 'Example Corp' `
    -ExpectedSHA256 '0000000000000000000000000000000000000000000000000000000000000000'
"""
    res3 = run_ps(code_hash_mismatch)
    assert res3.returncode == 1
    assert "does not match expected hash" in res3.stderr

    # Host mismatch
    code_host_mismatch = f"""
$ErrorActionPreference = 'Stop'
. {quote(SCRIPT_PATH)}
function Get-InstallerSignature {{
    [pscustomobject]@{{
    Status = 'Valid'
    SignerCertificate = [pscustomobject]@{{
        Subject = 'CN=Example Corp, C=US'
    }}
}}
}}
Invoke-InstallerSignatureCheck `
    -InstallerPath {quote(installer)} `
    -ExpectedPublisher 'Example Corp' `
    -ExpectedComputer 'DEFINITELY-NOT-THIS-COMPUTER-12345'
"""
    res4 = run_ps(code_host_mismatch)
    assert res4.returncode == 1
    assert "Host mismatch" in res4.stderr
