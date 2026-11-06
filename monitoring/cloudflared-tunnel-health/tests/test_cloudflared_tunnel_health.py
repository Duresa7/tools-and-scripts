from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

SPEC_TH = importlib.util.spec_from_file_location(
    "tunnel_health", MODULE_DIR / "tunnel_health.py"
)
assert SPEC_TH and SPEC_TH.loader
tunnel_health = importlib.util.module_from_spec(SPEC_TH)
sys.modules[SPEC_TH.name] = tunnel_health
SPEC_TH.loader.exec_module(tunnel_health)

SPEC_CFG = importlib.util.spec_from_file_location(
    "configure", MODULE_DIR / "configure.py"
)
assert SPEC_CFG and SPEC_CFG.loader
configure = importlib.util.module_from_spec(SPEC_CFG)
sys.modules[SPEC_CFG.name] = configure
SPEC_CFG.loader.exec_module(configure)

connections = tunnel_health.connections
escape_label_value = tunnel_health.escape_label_value
load_settings = tunnel_health.load_settings
main = tunnel_health.main
parse_settings = tunnel_health.parse_settings
probe_origin = tunnel_health.probe_origin
render_metrics = tunnel_health.render_metrics
resolve_settings = tunnel_health.resolve_settings
write_textfile = tunnel_health.write_textfile


class MockServerHandler(BaseHTTPRequestHandler):
    routes: ClassVar[dict[str, tuple[int, bytes, dict[str, str]]]] = {}

    def do_GET(self) -> None:
        if self.path in self.routes:
            status, body, headers = self.routes[self.path]
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), MockServerHandler)
    MockServerHandler.routes = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, MockServerHandler.routes
    finally:
        server.shutdown()
        server.server_close()


def test_stopped_connector_reports_zero(mock_server) -> None:
    base_url, routes = mock_server
    # Stopped connector reports 0 without contacting metrics endpoint.
    routes["/metrics"] = (
        200,
        b"cloudflared_tunnel_ha_connections 4\n",
        {"Content-Type": "text/plain"},
    )
    result = connections(
        metrics_url=f"{base_url}/metrics",
        systemd_unit="cloudflared.service",
        systemctl_fn=lambda _: "inactive",
    )
    assert result == 0

    failed_result = connections(
        metrics_url=f"{base_url}/metrics",
        systemd_unit="cloudflared.service",
        systemctl_fn=lambda _: "failed",
    )
    assert failed_result == 0


def test_unreachable_metrics_endpoint_reports_unknown() -> None:
    # Service is active, but port is unreachable -> reports None (unknown).
    result = connections(
        metrics_url="http://127.0.0.1:1/metrics",
        systemd_unit="cloudflared.service",
        systemctl_fn=lambda _: "active",
        timeout=1.0,
    )
    assert result is None


def test_active_connector_reports_count(mock_server) -> None:
    base_url, routes = mock_server
    routes["/metrics"] = (
        200,
        b"# HELP cloudflared_tunnel_ha_connections ...\n"
        b"cloudflared_tunnel_ha_connections 4\n",
        {"Content-Type": "text/plain"},
    )
    result = connections(
        metrics_url=f"{base_url}/metrics",
        systemd_unit="cloudflared.service",
        systemctl_fn=lambda _: "active",
    )
    assert result == 4


def test_nan_or_negative_values_report_unknown(mock_server) -> None:
    base_url, routes = mock_server

    for payload in (
        b"cloudflared_tunnel_ha_connections NaN\n",
        b"cloudflared_tunnel_ha_connections -1\n",
        b"cloudflared_tunnel_ha_connections -5.0\n",
        b"cloudflared_tunnel_ha_connections +Inf\n",
        b"other_metric 123\n",
    ):
        routes["/metrics"] = (200, payload, {"Content-Type": "text/plain"})
        result = connections(
            metrics_url=f"{base_url}/metrics",
            systemd_unit="cloudflared.service",
            systemctl_fn=lambda _: "active",
        )
        assert result is None


def test_origin_up_down_and_redirect(mock_server) -> None:
    base_url, routes = mock_server
    routes["/up"] = (200, b"OK", {})
    routes["/redirect"] = (302, b"Found", {"Location": "/up"})
    routes["/down"] = (500, b"Error", {})

    assert probe_origin(f"{base_url}/up") == 1
    assert probe_origin(f"{base_url}/redirect") == 1
    assert probe_origin(f"{base_url}/down") == 0
    assert probe_origin("http://127.0.0.1:1/nonexistent", timeout=0.5) == 0


def test_label_escaping() -> None:
    raw = 'service"name\\with\nnewline'
    escaped = escape_label_value(raw)
    assert escaped == r"service\"name\\with\nnewline"

    rendered = render_metrics(
        connection_count=2,
        origin_results=[(raw, 1)],
        timestamp=1700000000.0,
    )
    assert r'service="service\"name\\with\nnewline"' in rendered
    assert "cloudflared_tunnel_ha_connections 2" in rendered
    assert "cloudflared_tunnel_timestamp_seconds 1700000000.000" in rendered


def test_render_metrics_unknown_connection() -> None:
    rendered = render_metrics(
        connection_count=None,
        origin_results=[],
        timestamp=1700000000.0,
    )
    assert "cloudflared_tunnel_ha_connections -1" in rendered
    assert "cloudflared_tunnel_origin_up" not in rendered


def test_atomic_write(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "tunnel.prom"
    body = "cloudflared_tunnel_ha_connections 4\n"
    write_textfile(target, body)

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == body
    assert target.stat().st_mode & 0o777 == 0o644

    # Ensure invalid extensions are rejected
    with pytest.raises(ValueError, match=r"\.prom"):
        write_textfile(tmp_path / "test.txt", body)


def test_stdout_writes_nothing_to_file(
    mock_server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_url, routes = mock_server
    routes["/metrics"] = (
        200,
        b"cloudflared_tunnel_ha_connections 3\n",
        {"Content-Type": "text/plain"},
    )
    non_existent = tmp_path / "never_created.prom"

    code = main(
        [
            "--metrics-url",
            f"{base_url}/metrics",
            "--output",
            str(non_existent),
            "--stdout",
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert not non_existent.exists()
    assert "cloudflared_tunnel_ha_connections" in captured.out


def test_config_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[tunnel]
metrics_url = "http://127.0.0.1:9999/metrics"
systemd_unit = "my-tunnel.service"
timeout_seconds = 7.5

[metrics]
output_path = "/var/lib/prometheus/node-exporter/from-config.prom"
metric_prefix = "custom_prefix"

[[origins]]
name = "config-origin"
url = "http://127.0.0.1:8080/health"
""",
        encoding="utf-8",
    )

    # 1. Config file values override defaults
    settings = load_settings(config_file)
    assert settings.metrics_url == "http://127.0.0.1:9999/metrics"
    assert settings.systemd_unit == "my-tunnel.service"
    assert settings.timeout_seconds == 7.5
    assert (
        str(settings.output_path)
        == "/var/lib/prometheus/node-exporter/from-config.prom"
    )
    assert settings.metric_prefix == "custom_prefix"
    assert len(settings.origins) == 1
    assert settings.origins[0].name == "config-origin"

    # 2. CLI flags override config file
    cli_args = argparse.Namespace(
        config=config_file,
        metrics_url="http://127.0.0.1:5555/metrics",
        systemd_unit=None,
        timeout=12.0,
        output=tmp_path / "cli.prom",
        origins=["cli-origin=http://127.0.0.1:9090/status"],
    )
    resolved = resolve_settings(cli_args)
    assert resolved.metrics_url == "http://127.0.0.1:5555/metrics"
    assert resolved.systemd_unit == "my-tunnel.service"
    assert resolved.timeout_seconds == 12.0
    assert resolved.output_path == tmp_path / "cli.prom"
    assert len(resolved.origins) == 1
    assert resolved.origins[0].name == "cli-origin"


def test_parse_settings_validation() -> None:
    with pytest.raises(ValueError, match="TOML table"):
        parse_settings([])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="timeout_seconds"):
        parse_settings(
            {"tunnel": {"timeout_seconds": -1}, "metrics": {}, "origins": []}
        )

    with pytest.raises(ValueError, match=r"\.prom"):
        parse_settings(
            {
                "tunnel": {},
                "metrics": {"output_path": "/var/log/metrics.txt"},
                "origins": [],
            }
        )

    with pytest.raises(ValueError, match="origins"):
        parse_settings(
            {
                "tunnel": {},
                "metrics": {},
                "origins": [{"name": "bad", "url": "ftp://bad"}],
            }
        )


def test_configurator_writes_and_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "config.local.toml"

    # First run writes successfully
    code1 = configure.main(
        [
            "--output",
            str(target),
            "--metrics-url",
            "http://127.0.0.1:20241/metrics",
            "--origin",
            "web=http://127.0.0.1:8080/health",
        ]
    )
    assert code1 == 0
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert 'name = "web"' in content
    assert "# CUSTOMIZE:" in content

    # Second run without --overwrite fails
    code2 = configure.main(["--output", str(target)])
    assert code2 == 1

    # Third run with --overwrite succeeds
    code3 = configure.main(["--output", str(target), "--overwrite"])
    assert code3 == 0


def test_cli_help() -> None:
    runner1 = subprocess.run(
        [sys.executable, str(MODULE_DIR / "tunnel_health.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert runner1.returncode == 0
    assert "Cloudflare Tunnel" in runner1.stdout

    runner2 = subprocess.run(
        [sys.executable, str(MODULE_DIR / "configure.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert runner2.returncode == 0
    assert "config.local.toml" in runner2.stdout


def test_fake_systemctl_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text("#!/bin/sh\necho inactive\n", encoding="utf-8")
    fake_systemctl.chmod(0o755)

    monkeypatch.setenv("PATH", f"{fake_bin}:{subprocess.os.environ.get('PATH', '')}")
    result = connections(
        metrics_url="http://127.0.0.1:20241/metrics",
        systemd_unit="cloudflared.service",
    )
    assert result == 0
