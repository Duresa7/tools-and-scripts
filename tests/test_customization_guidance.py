import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MARKER_COUNTS = {
    "ansible/ssh-key-rotation/identities/_identity-template.yml.example": 8,
    "ansible/ssh-key-rotation/inventory/hosts.yml.example": 7,
    "networkmanager/networkmanager-ifupdown-cutover.sh": 1,
}

HELP_CASES = (
    (
        "prometheus/check_targets.py",
        ("--help",),
        ("replace its jobs and URLs",),
    ),
    (
        "semaphore/semaphore_sqlite.py",
        ("backup", "--help"),
        ("your live Semaphore SQLite database", "must not already exist"),
    ),
    (
        "teamspeak/teamspeak_channels.py",
        ("export", "--help"),
        (
            "ClientQuery host",
            "environment variable containing your ClientQuery API key",
        ),
    ),
    (
        "teamspeak/teamspeak_channels.py",
        ("import", "--help"),
        ("target ServerQuery host", "environment variable containing your ServerQuery"),
    ),
)

TOOL_READMES = (
    "ansible/ssh-key-rotation/README.md",
    "networkmanager/README.md",
    "prometheus/README.md",
    "semaphore/README.md",
    "teamspeak/README.md",
)


def test_user_supplied_configuration_has_customize_markers() -> None:
    marker_counts = {
        path: (ROOT / path).read_text(encoding="utf-8").count("CUSTOMIZE:")
        for path in MARKER_COUNTS
    }
    assert marker_counts == MARKER_COUNTS


def test_prometheus_example_explains_its_placeholders() -> None:
    path = ROOT / "prometheus/expected-targets.example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["_comment"].startswith("CUSTOMIZE:")
    assert all(
        target["_comment"].startswith("CUSTOMIZE:")
        for target in payload["expected_targets"]
    )
    assert payload["_forbidden_substrings_comment"].startswith("CUSTOMIZE:")
    assert payload["_required_health_comment"].startswith("CUSTOMIZE:")


def test_command_help_identifies_environment_specific_inputs() -> None:
    for relative_path, arguments, expected_fragments in HELP_CASES:
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative_path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        normalized_help = " ".join(completed.stdout.split())
        for fragment in expected_fragments:
            assert fragment in normalized_help


def test_every_tool_readme_has_a_customization_section() -> None:
    missing = [
        path
        for path in TOOL_READMES
        if "## What you must customize" not in (ROOT / path).read_text(encoding="utf-8")
    ]
    assert missing == []


def test_documented_commands_use_copied_local_configuration() -> None:
    prometheus_readme = (ROOT / "prometheus/README.md").read_text(encoding="utf-8")
    ansible_readme = (ROOT / "ansible/ssh-key-rotation/README.md").read_text(
        encoding="utf-8"
    )

    assert prometheus_readme.count("--expect prometheus/expected-targets.json") == 2
    assert "cp inventory/hosts.yml.example inventory/hosts.yml" in ansible_readme
