import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIGS = ROOT / "ai-configs"

TOOLS = (
    ROOT / "linux" / "networkmanager-cutover",
    ROOT / "monitoring" / "prometheus-target-check",
    ROOT / "automation" / "semaphore-sqlite-guard",
    ROOT / "automation" / "teamspeak-channel-migration",
    ROOT / "automation" / "ssh-key-rotation",
    ROOT / "linux" / "proxmox-subscription-notice",
    ROOT / "linux" / "compose-service-update",
    ROOT / "monitoring" / "dnf-updates",
    ROOT / "monitoring" / "cloudflared-tunnel-health",
    ROOT / "monitoring" / "teamspeak-voice-probe",
    ROOT / "monitoring" / "minecraft-status-probe",
    ROOT / "monitoring" / "grafana-dashboard-check",
    ROOT / "utilities" / "tcp-reachability-check",
    ROOT / "automation" / "semaphore-project-reconciler",
    ROOT / "monitoring" / "unifi-flow-collector",
    ROOT / "windows" / "online-logon-policy",
    ROOT / "windows" / "workstation-bootstrap",
    ROOT / "windows" / "recovery-lockdown",
    ROOT / "windows" / "session-limits",
    ROOT / "utilities" / "git-preview-server",
    ROOT / "linux" / "wazuh-hash-list-refresh",
    ROOT / "windows" / "installer-signature-check",
    ROOT / "monitoring" / "unifi-flow-dashboards",
    ROOT / "monitoring" / "discord-alert-relay",
    ROOT / "automation" / "fleet-updates",
    ROOT / "automation" / "monitoring-exporters",
    ROOT / "automation" / "linux-access-baseline",
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
    "linux/networkmanager-cutover/config.local.conf",
    "monitoring/prometheus-target-check/config.local.json",
    "automation/semaphore-sqlite-guard/config.local.toml",
    "automation/teamspeak-channel-migration/config.local.toml",
    "automation/ssh-key-rotation/inventory/hosts.yml",
    "automation/ssh-key-rotation/identities/workstation-key.yml",
    "linux/proxmox-subscription-notice/config.local.conf",
    "linux/compose-service-update/config.local.conf",
    "monitoring/dnf-updates/config.local.conf",
    "monitoring/cloudflared-tunnel-health/config.local.toml",
    "monitoring/teamspeak-voice-probe/config.local.toml",
    "monitoring/minecraft-status-probe/config.local.toml",
    "monitoring/grafana-dashboard-check/config.local.json",
    "utilities/tcp-reachability-check/config.local.json",
    "automation/semaphore-project-reconciler/config.local.toml",
    "monitoring/unifi-flow-collector/config.local.toml",
    "windows/online-logon-policy/config.local.json",
    "windows/workstation-bootstrap/config.local.json",
    "windows/recovery-lockdown/config.local.json",
    "windows/session-limits/config.local.json",
    "utilities/git-preview-server/config.local.json",
    "automation/semaphore-project-reconciler/manifest.local.yml",
    "linux/wazuh-hash-list-refresh/config.local.conf",
    "windows/installer-signature-check/config.local.json",
    "monitoring/unifi-flow-dashboards/config.local.toml",
    "monitoring/discord-alert-relay/config.local.toml",
    "automation/fleet-updates/inventory/hosts.yml",
    "automation/fleet-updates/config.local.yml",
    "automation/monitoring-exporters/inventory/hosts.yml",
    "automation/linux-access-baseline/inventory/hosts.yml",
    "automation/linux-access-baseline/config.local.yml",
    "automation/linux-access-baseline/vars/automation-key.yml",
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
