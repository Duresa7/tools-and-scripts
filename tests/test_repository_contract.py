import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOOLS = (
    ROOT / "networking" / "networkmanager-cutover",
    ROOT / "monitoring" / "prometheus-target-check",
    ROOT / "backup-and-recovery" / "semaphore-sqlite-guard",
    ROOT / "migrations" / "teamspeak-channel-migration",
    ROOT / "identity-and-access" / "ssh-key-rotation",
)

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
    "networking/networkmanager-cutover/config.local.conf",
    "monitoring/prometheus-target-check/config.local.json",
    "backup-and-recovery/semaphore-sqlite-guard/config.local.toml",
    "migrations/teamspeak-channel-migration/config.local.toml",
    "identity-and-access/ssh-key-rotation/inventory/hosts.yml",
    "identity-and-access/ssh-key-rotation/identities/workstation-key.yml",
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
        if not (tool / "README.md").is_file():
            missing.append(f"{tool.relative_to(ROOT)}: README.md")
        if not (tool / "tests").is_dir():
            missing.append(f"{tool.relative_to(ROOT)}: tests/")
        if not any(path.name.startswith("configure.") for path in tool.iterdir()):
            missing.append(f"{tool.relative_to(ROOT)}: configurator")
    assert missing == []


def test_every_tool_readme_uses_the_same_navigation() -> None:
    missing: list[str] = []
    for tool in TOOLS:
        text = (tool / "README.md").read_text(encoding="utf-8")
        for section in README_SECTIONS:
            if f"## {section}" not in text:
                missing.append(f"{tool.relative_to(ROOT)}: {section}")
            link = section.lower().replace(" ", "-")
            if f"](#{link})" not in text:
                missing.append(f"{tool.relative_to(ROOT)}: link {link}")
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
