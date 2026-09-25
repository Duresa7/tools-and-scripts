"""Tests for TCP reachability check tool."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = TOOL_DIR / "Test-TcpReachability.ps1"
CONFIG_SCRIPT = TOOL_DIR / "configure.ps1"
EXAMPLE_CONFIG = TOOL_DIR / "config.example.json"


def get_pwsh() -> str:
    for name in ("pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    candidate = Path.home() / ".local/bin/pwsh"
    if candidate.is_file():
        return str(candidate)
    raise AssertionError(
        "Install PowerShell 7 and add pwsh to PATH to run these tests."
    )


def run_script(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    pwsh = get_pwsh()
    proc_env = None
    if env is not None:
        proc_env = dict(os.environ)
        proc_env.update(env)
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
        env=proc_env,
        timeout=30,
    )


def run_configure(args: list[str]) -> subprocess.CompletedProcess[str]:
    pwsh = get_pwsh()
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(CONFIG_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def get_current_computer_name() -> str:
    pwsh = get_pwsh()
    cmd = "[System.Net.Dns]::GetHostName()"
    res = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return res.stdout.strip()


@pytest.fixture
def tcp_server() -> Generator[int, None, None]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    stop_event = threading.Event()

    def handle_clients() -> None:
        server.settimeout(0.5)
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except TimeoutError:
                continue
            except OSError:
                break

    thread = threading.Thread(target=handle_clients, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop_event.set()
        server.close()
        thread.join(timeout=2)


def test_open_port_local_listener(tcp_server: int) -> None:
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-TimeoutSeconds",
            "1",
            "-Json",
        ]
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["Target"] == "127.0.0.1"
    assert data[0]["Port"] == tcp_server
    assert data[0]["Connected"] is True


def test_closed_port() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(closed_port),
            "-TimeoutSeconds",
            "1",
            "-Json",
        ]
    )
    assert res.returncode == 1
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["Target"] == "127.0.0.1"
    assert data[0]["Port"] == closed_port
    assert data[0]["Connected"] is False


def test_unresolvable_name() -> None:
    res = run_script(
        [
            "-Target",
            "unresolvable-test.example.invalid",
            "-Port",
            "80",
            "-TimeoutSeconds",
            "1",
            "-Json",
        ]
    )
    assert res.returncode == 1
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["Target"] == "unresolvable-test.example.invalid"
    assert data[0]["Port"] == 80
    assert data[0]["Connected"] is False


def test_source_name_guard_mismatch(tcp_server: int) -> None:
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-ExpectedSource",
            "definitely-nonexistent-computer-name",
        ]
    )
    assert res.returncode == 2
    assert "does not match expected source" in res.stderr


def test_source_name_guard_matches(tcp_server: int) -> None:
    current_host = get_current_computer_name()
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-ExpectedSource",
            current_host,
            "-Json",
        ]
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data[0]["Connected"] is True


def test_source_host_guard_cannot_be_bypassed_by_computername_env(
    tcp_server: int,
) -> None:
    spoofed = "spoofed-computer-name"
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-ExpectedSource",
            spoofed,
            "-TimeoutSeconds",
            "1",
        ],
        env={"COMPUTERNAME": spoofed},
    )
    assert res.returncode == 2
    assert "does not match expected source" in res.stderr


def test_json_output_shape(tcp_server: int) -> None:
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-Label",
            "primary-listener",
            "-Format",
            "Json",
        ]
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert "Source" in item and isinstance(item["Source"], str)
    assert item["Target"] == "127.0.0.1"
    assert item["Port"] == tcp_server
    assert item["Connected"] is True
    assert item["Label"] == "primary-listener"


def test_table_output_format(tcp_server: int) -> None:
    res = run_script(
        [
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-Format",
            "Table",
        ]
    )
    assert res.returncode == 0
    assert "Source" in res.stdout
    assert "Target" in res.stdout
    assert "Port" in res.stdout
    assert "Connected" in res.stdout


@pytest.mark.parametrize(
    "invalid_args",
    [
        ["-Port", "99999", "-Target", "127.0.0.1"],
        ["-Port", "0", "-Target", "127.0.0.1"],
        ["-TimeoutSeconds", "-1", "-Target", "127.0.0.1", "-Port", "80"],
        ["-Format", "Xml", "-Target", "127.0.0.1", "-Port", "80"],
        ["-ConfigPath", "nonexistent-file.json"],
        ["-Port", "80"],
    ],
)
def test_exit_codes_bad_input(invalid_args: list[str]) -> None:
    res = run_script(invalid_args)
    assert res.returncode == 2
    assert "Error:" in res.stderr


def test_parameter_binding_failure() -> None:
    res = run_script(["-Port", "nonsense", "-Target", "127.0.0.1"])
    assert res.returncode == 1
    assert "Cannot process argument transformation" in res.stderr


def test_config_precedence(tmp_path: Path, tcp_server: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    # Config has closed port and Table format
    cfg = tmp_path / "custom_config.json"
    cfg.write_text(
        json.dumps(
            {
                "Targets": [
                    {
                        "Target": "127.0.0.1",
                        "Port": closed_port,
                        "Label": "config-port",
                    }
                ],
                "TimeoutSeconds": 10.0,
                "Format": "Table",
            }
        ),
        encoding="utf-8",
    )

    # CLI overrides target with open port and overrides format with Json
    res = run_script(
        [
            "-ConfigPath",
            str(cfg),
            "-Target",
            "127.0.0.1",
            "-Port",
            str(tcp_server),
            "-TimeoutSeconds",
            "1",
            "-Json",
        ]
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["Target"] == "127.0.0.1"
    assert data[0]["Port"] == tcp_server
    assert data[0]["Connected"] is True


def test_configurator_non_overwrite(tmp_path: Path) -> None:
    out_file = tmp_path / "config.local.json"

    # Initial write succeeds
    res1 = run_configure(["-OutputPath", str(out_file)])
    assert res1.returncode == 0
    assert out_file.is_file()
    initial_content = out_file.read_text(encoding="utf-8")

    # Second write without -Overwrite fails
    res2 = run_configure(["-OutputPath", str(out_file)])
    assert res2.returncode == 1
    assert "Refusing to replace" in res2.stderr
    assert out_file.read_text(encoding="utf-8") == initial_content

    # Third write with -Overwrite succeeds
    res3 = run_configure(
        ["-OutputPath", str(out_file), "-Overwrite", "-TimeoutSeconds", "5"]
    )
    assert res3.returncode == 0
    updated_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert updated_data["TimeoutSeconds"] == 5.0


def test_dot_sourcing_does_not_execute() -> None:
    pwsh = get_pwsh()
    cmd = f". '{SCRIPT_PATH}'"
    res = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""
    assert res.stderr.strip() == ""
