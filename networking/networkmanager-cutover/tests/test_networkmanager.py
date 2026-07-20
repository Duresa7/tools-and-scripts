import os
import shutil
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
    assert "--expect-routes" in completed.stdout
    assert "CUTOVER_NETWORK_CONNECTION" in completed.stdout


def test_network_config_marks_every_owned_value() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")

    assert example.count("CUSTOMIZE:") == 11
    assert "EXPECT_ROUTES=default via" in example
    assert "table all default" in (TOOL / "networkmanager-cutover.sh").read_text(
        encoding="utf-8"
    )
    assert "eval" not in (TOOL / "networkmanager-cutover.sh").read_text(
        encoding="utf-8"
    )


def test_configurator_writes_discovered_routes_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ip = fake_bin / "ip"
    ip.write_text(
        """#!/usr/bin/env bash
case "$*" in
  "-4 route show default"|"-4 route show default dev eth0"|\
"-4 route show table all default dev eth0")
    echo 'default via 192.0.2.1 dev eth0 proto static metric 100'
    ;;
  "-o -4 address show dev eth0 scope global")
    echo '2: eth0 inet 192.0.2.10/24 scope global eth0'
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    nmcli = fake_bin / "nmcli"
    nmcli.write_text(
        """#!/usr/bin/env bash
case "$*" in
  "-g GENERAL.CONNECTION device show eth0") echo 'server-static' ;;
  "-g IP4.DNS device show eth0") echo '192.0.2.53' ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    ip.chmod(0o755)
    nmcli.chmod(0o755)
    output = tmp_path / "config.local.conf"
    environment = os.environ.copy()
    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        assert wsl is not None

        def wsl_path(path: Path) -> str:
            translated = subprocess.run(
                [wsl, "-e", "wslpath", "-a", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return translated.stdout.strip()

        command = [
            wsl,
            "-e",
            "bash",
            "-c",
            'PATH="$1:$PATH"; cd "$2"; exec ./configure.sh --output "$3"',
            "configurator-test",
            wsl_path(fake_bin),
            wsl_path(TOOL),
            wsl_path(output),
        ]
    else:
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        command = ["bash", "configure.sh", "--output", str(output)]

    first = subprocess.run(
        command,
        cwd=TOOL,
        env=environment,
        input="\n" * 10,
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        command,
        cwd=TOOL,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "refusing to replace" in second.stderr
    contents = output.read_text(encoding="utf-8")
    assert (
        "EXPECT_ROUTES=default via 192.0.2.1 dev eth0 proto static metric 100"
        in contents
    )
