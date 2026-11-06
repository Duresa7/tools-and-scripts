import json
import subprocess
import sys
from pathlib import Path

import tool_catalog

ROOT = Path(__file__).resolve().parents[1]


def tool_path(relative: str) -> str:
    """Resolve "tool-name/file" to its current "category/tool-name/file" path."""
    name, _, rest = relative.partition("/")
    return f"{tool_catalog.tool(name, ROOT).relative}/{rest}"


MARKER_COUNTS = {
    tool_path("ssh-key-rotation/identities/_identity-template.yml.example"): 7,
    tool_path("ssh-key-rotation/inventory/hosts.yml.example"): 37,
    tool_path("networkmanager-cutover/config.example.conf"): 11,
    tool_path("prometheus-target-check/config.example.json"): 10,
    tool_path("semaphore-sqlite-guard/config.example.toml"): 4,
    tool_path("teamspeak-channel-migration/config.example.toml"): 13,
}

HELP_CASES = (
    (
        tool_path("prometheus-target-check/prometheus_target_check.py"),
        ("--help",),
        ("local JSON configuration", "bearer-token environment variable"),
    ),
    (
        tool_path("semaphore-sqlite-guard/semaphore_sqlite_guard.py"),
        ("backup", "--help"),
        ("overrides database_path", "must not exist"),
    ),
    (
        tool_path("teamspeak-channel-migration/teamspeak_channel_migration.py"),
        ("export", "--help"),
        (
            "ClientQuery host",
            "environment variable containing your ClientQuery API key",
        ),
    ),
    (
        tool_path("teamspeak-channel-migration/teamspeak_channel_migration.py"),
        ("import", "--help"),
        ("target ServerQuery host", "environment variable containing your ServerQuery"),
    ),
)

TOOL_READMES = (
    tool_path("ssh-key-rotation/README.md"),
    tool_path("networkmanager-cutover/README.md"),
    tool_path("prometheus-target-check/README.md"),
    tool_path("semaphore-sqlite-guard/README.md"),
    tool_path("teamspeak-channel-migration/README.md"),
)


def test_user_supplied_configuration_has_customize_markers() -> None:
    marker_counts = {
        path: (ROOT / path).read_text(encoding="utf-8").count("CUSTOMIZE:")
        for path in MARKER_COUNTS
    }
    assert marker_counts == MARKER_COUNTS


def test_ssh_inventory_marks_each_environment_owned_example_value() -> None:
    path = ROOT / tool_path("ssh-key-rotation/inventory/hosts.yml.example")
    lines = path.read_text(encoding="utf-8").splitlines()
    owned_fragments = (
        "-example:",
        "192.0.2.",
        "replace-",
        "/srv/shared-service/",
        "ssh_key_write_enabled:",
        "ssh_key_manage_directory:",
        "ssh_key_become:",
        "ssh_key_shared_writer:",
    )

    missing = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not any(
            fragment in stripped for fragment in owned_fragments
        ):
            continue
        previous = lines[index - 1].strip() if index else ""
        if not previous.startswith("# CUSTOMIZE:"):
            missing.append(stripped)

    assert missing == []


def test_prometheus_example_explains_its_placeholders() -> None:
    path = ROOT / tool_path("prometheus-target-check/config.example.json")
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
        ROOT / tool_path("prometheus-target-check/README.md")
    ).read_text(encoding="utf-8")
    ansible_readme = (ROOT / tool_path("ssh-key-rotation/README.md")).read_text(
        encoding="utf-8"
    )

    assert "config.example.json" in prometheus_readme
    assert "config.local.json" in prometheus_readme
    assert "cp inventory/hosts.yml.example inventory/hosts.yml" in ansible_readme
