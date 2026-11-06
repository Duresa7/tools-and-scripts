import subprocess
from pathlib import Path

import tool_catalog

ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIGS = ROOT / "ai-configs"

TOOLS = tool_catalog.tools(ROOT)

README_SECTIONS = (
    "Use case",
    "Prerequisites",
    "Guided setup",
    "Manual setup",
    "Inputs",
    "Permissions",
    "Dry run",
    "Changes made",
    "Safeguard reasoning",
    "Rollback",
    "Troubleshooting",
    "Exit behavior",
)

LOCAL_CONFIGS = (
    *(f"{tool.relative}/{name}" for tool in TOOLS for name in tool.local_files),
    "ai-configs/skills/orchestrate/config.local.toml",
)


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / item.decode()
        for item in completed.stdout.split(b"\0")
        if item and (ROOT / item.decode()).is_file()
    ]


def test_every_tool_folder_is_self_contained() -> None:
    missing: list[str] = []
    for tool in TOOLS:
        if not (tool.path / "README.md").is_file():
            missing.append(f"{tool.relative}: README.md")
        if not (tool.path / "tests").is_dir():
            missing.append(f"{tool.relative}: tests/")
        has_configurator = any(
            path.name.startswith("configure.") for path in tool.path.iterdir()
        )
        if has_configurator != tool.has_configurator:
            missing.append(f"{tool.relative}: configurator")
    assert missing == []


def test_every_tool_folder_has_a_manifest() -> None:
    folders = sorted(
        f"{category}/{folder.name}"
        for category in tool_catalog.CATEGORIES
        for folder in (ROOT / category).iterdir()
        if folder.is_dir() and not folder.name.startswith((".", "_"))
    )
    assert folders == sorted(tool.relative for tool in TOOLS)


def test_only_category_folders_hold_tools() -> None:
    stray = sorted(
        path.parent.relative_to(ROOT).as_posix()
        for path in ROOT.glob("*/*/tool.toml")
        if path.parent.parent.name not in tool_catalog.CATEGORIES
    )
    assert stray == []


def test_readme_catalog_links_every_tool() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    unlisted = [
        tool.relative for tool in TOOLS if f"]({tool.relative}/README.md)" not in readme
    ]
    assert unlisted == []


def test_every_tool_readme_uses_the_same_navigation() -> None:
    missing: list[str] = []
    for tool in TOOLS:
        text = (tool.path / "README.md").read_text(encoding="utf-8")
        for section in README_SECTIONS:
            if f"## {section}" not in text:
                missing.append(f"{tool.relative}: {section}")
            link = section.lower().replace(" ", "-")
            if f"](#{link})" not in text:
                missing.append(f"{tool.relative}: link {link}")
    assert missing == []


def test_local_configuration_paths_are_ignored() -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "--no-index", *LOCAL_CONFIGS],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    ignored = set(completed.stdout.splitlines())
    assert ignored == set(LOCAL_CONFIGS)


def test_repository_text_omits_process_and_environment_labels() -> None:
    forbidden = (
        "home" + "lab",
        "co-authored" + "-by",
        "co" + "dex",
        "open" + "ai",
        "clau" + "de",
        chr(0x2014),
    )
    findings: list[str] = []
    for path in tracked_files():
        # Agent configs name the agents they configure.
        if path.is_relative_to(AGENT_CONFIGS):
            continue
        if path.suffix.lower() not in {
            ".conf",
            ".example",
            ".json",
            ".md",
            ".ps1",
            ".py",
            ".sh",
            ".toml",
            ".yml",
        }:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for value in forbidden:
            if value in text:
                findings.append(f"{path.relative_to(ROOT)}: {value}")
    assert findings == []


def test_examples_do_not_assume_privileged_login_accounts() -> None:
    findings: list[str] = []
    patterns = (
        "ansible_user: " + "root",
        "ansible_user: " + "administrator",
        "/" + "root" + "/",
    )
    for path in tracked_files():
        if path.suffix.lower() not in {".md", ".yml", ".example"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for pattern in patterns:
            if pattern in text:
                findings.append(f"{path.relative_to(ROOT)}: {pattern}")
    assert findings == []
