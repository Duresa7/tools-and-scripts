import subprocess
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]


def test_networkmanager_tool_is_self_contained() -> None:
    required = {
        "README.md",
        "config.example.conf",
        "configure.sh",
        "networkmanager-cutover.sh",
        "lib/ifupdown.sh",
        "lib/netplan.sh",
        "lib/networkmanager.sh",
    }

    files = {
        str(path.relative_to(TOOL)).replace("\\", "/")
        for path in TOOL.rglob("*")
        if path.is_file()
    }
    assert files >= required


def test_networkmanager_help_names_all_execution_modes() -> None:
    completed = subprocess.run(
        ["bash", "networkmanager-cutover.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--dry-run" in completed.stdout
    assert "--validate-only" in completed.stdout
    assert "CUTOVER_NETWORK_CONNECTION" in completed.stdout


def test_network_config_marks_every_owned_value() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")

    assert example.count("CUSTOMIZE:") == 10
    assert "eval" not in (TOOL / "networkmanager-cutover.sh").read_text(
        encoding="utf-8"
    )
