import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MARKER_COUNTS = {
    "identity-and-access/ssh-key-rotation/identities/_identity-template.yml.example": 8,
    "identity-and-access/ssh-key-rotation/inventory/hosts.yml.example": 7,
    "networking/networkmanager-cutover/config.example.conf": 10,
    "monitoring/prometheus-target-check/config.example.json": 10,
}

HELP_CASES = (
    (
        "monitoring/prometheus-target-check/check_targets.py",
        ("--help",),
        ("local JSON configuration", "bearer-token environment variable"),
    ),
    (
        "backup-and-recovery/semaphore-sqlite-guard/semaphore_sqlite.py",
        ("backup", "--help"),
        ("your live Semaphore SQLite database", "must not already exist"),
    ),
    (
        "migrations/teamspeak-channel-migration/teamspeak_channels.py",
        ("export", "--help"),
        (
            "ClientQuery host",
            "environment variable containing your ClientQuery API key",
        ),
    ),
    (
        "migrations/teamspeak-channel-migration/teamspeak_channels.py",
        ("import", "--help"),
        ("target ServerQuery host", "environment variable containing your ServerQuery"),
    ),
)

TOOL_READMES = (
    "identity-and-access/ssh-key-rotation/README.md",
    "networking/networkmanager-cutover/README.md",
    "monitoring/prometheus-target-check/README.md",
    "backup-and-recovery/semaphore-sqlite-guard/README.md",
    "migrations/teamspeak-channel-migration/README.md",
)


def test_user_supplied_configuration_has_customize_markers() -> None:
    marker_counts = {
        path: (ROOT / path).read_text(encoding="utf-8").count("CUSTOMIZE:")
        for path in MARKER_COUNTS
    }
    assert marker_counts == MARKER_COUNTS


def test_prometheus_example_explains_its_placeholders() -> None:
    path = ROOT / "monitoring/prometheus-target-check/config.example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["_comment"].startswith("CUSTOMIZE:")
    comment_values = [
        value
        for section in (payload["prometheus"], payload["expectations"])
        for key, value in section.items()
        if key.startswith("_comment")
    ]
    assert len(comment_values) == 9
    assert all(value.startswith("CUSTOMIZE:") for value in comment_values)


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
        if not any(
            marker in (ROOT / path).read_text(encoding="utf-8")
            for marker in ("CUSTOMIZE:", "## What you must customize")
        )
    ]
    assert missing == []


def test_documented_commands_use_copied_local_configuration() -> None:
    prometheus_readme = (
        ROOT / "monitoring/prometheus-target-check/README.md"
    ).read_text(encoding="utf-8")
    ansible_readme = (
        ROOT / "identity-and-access/ssh-key-rotation/README.md"
    ).read_text(encoding="utf-8")

    assert "config.example.json" in prometheus_readme
    assert "config.local.json" in prometheus_readme
    assert "cp inventory/hosts.yml.example inventory/hosts.yml" in ansible_readme
