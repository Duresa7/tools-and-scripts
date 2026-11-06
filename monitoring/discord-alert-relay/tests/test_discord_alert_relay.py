"""Tests for Discord alert relay service."""

from __future__ import annotations

import http.server
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import configure
import pytest
from alert_relay import (
    DEFAULT_RESOLVED_COLOR,
    DiscordClient,
    DiscordRelayServer,
    RelayConfig,
    apply_cli_overrides,
    build_embed,
    build_parser,
    create_server,
    embed_length,
    fit_embed,
    is_ip_allowed,
    load_config,
    safe_url,
    title_and_color,
)

RELAY_SCRIPT = Path(__file__).resolve().parents[1] / "alert_relay.py"
CONFIGURE_SCRIPT = Path(__file__).resolve().parents[1] / "configure.py"


class FakeDiscordHandler(http.server.BaseHTTPRequestHandler):
    """Fake Discord REST API server handler for tests."""

    def log_message(self, format_str: str, *args: Any) -> None:
        pass  # Quiet during tests

    @property
    def received_requests(self) -> list[dict[str, Any]]:
        return self.server.received_requests  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        auth = self.headers.get("Authorization", "")
        self.received_requests.append(
            {
                "method": "GET",
                "path": self.path,
                "auth": auth,
            }
        )
        if "/fail" in self.path:
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"id": "channel-1", "name": "alerts"}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        auth = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        parsed_body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        self.received_requests.append(
            {
                "method": "POST",
                "path": self.path,
                "auth": auth,
                "body": parsed_body,
            }
        )

        if getattr(self.server, "post_status_codes", None):
            resp = self.server.post_status_codes.pop(0)
            if isinstance(resp, tuple):
                code, headers, body_bytes = resp
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
            elif isinstance(resp, int) and resp != 200:
                self.send_response(resp)
                self.end_headers()
                return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        resp_data = json.dumps({"id": "msg-123"}).encode("utf-8")
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)


class FakeDiscordServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int]):
        super().__init__(server_address, FakeDiscordHandler)
        self.received_requests: list[dict[str, Any]] = []
        self.post_status_codes: list[Any] = []
        self.get_status_codes: list[int] = []


@pytest.fixture
def fake_discord() -> Any:
    server = FakeDiscordServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    server.base_url = f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def relay_env(fake_discord: FakeDiscordServer) -> Any:
    secret = "test-secret-value-xyz"
    bot_token = "test-bot-token-abc"
    config = RelayConfig(
        listen_address="127.0.0.1",
        listen_port=0,
        secret_env="TEST_SECRET_ENV",
        bot_token_env="TEST_BOT_TOKEN_ENV",
        channel_id="1234567890",
        api_base_url=fake_discord.base_url,  # type: ignore[attr-defined]
        allowed_splunk_sources=["127.0.0.1"],
    )
    server = create_server(config, secret=secret, bot_token=bot_token)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_address[1]
    server.base_url = f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    server.secret = secret  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Helper to perform HTTP requests using standard library."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_health_before_and_after_startup_check(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify /health answers 503 before Discord check and 200 after."""
    url = f"{relay_env.base_url}/health"  # type: ignore[attr-defined]

    # Before startup check
    assert not relay_env.is_discord_ready
    status, _, body = http_request(url)
    assert status == 503
    assert b"discord not ready" in body

    # Run startup check
    ready = relay_env.check_discord_api()
    assert ready is True
    assert relay_env.is_discord_ready

    # After startup check
    status, _, body = http_request(url)
    assert status == 200
    assert body.strip() == b"ok"


def test_grafana_payload_with_several_alerts(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify Grafana alerts are mapped to embeds and condensed when threshold met."""
    relay_env.check_discord_api()

    # Payload with 4 identical DiskFull alerts (meeting threshold) and 1 HighCPU alert
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "DiskFull",
                    "severity": "critical",
                    "host": "srv-01",
                },
                "annotations": {"summary": "Disk full on srv-01"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "DiskFull",
                    "severity": "critical",
                    "host": "srv-02",
                },
                "annotations": {"summary": "Disk full on srv-02"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "DiskFull",
                    "severity": "critical",
                    "host": "srv-03",
                },
                "annotations": {"summary": "Disk full on srv-03"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "DiskFull",
                    "severity": "critical",
                    "host": "srv-04",
                },
                "annotations": {"summary": "Disk full on srv-04"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "severity": "warning",
                    "host": "srv-05",
                },
                "annotations": {"summary": "High CPU on srv-05"},
            },
        ]
    }

    url = f"{relay_env.base_url}/grafana"  # type: ignore[attr-defined]
    headers = {
        "Authorization": f"Bearer {relay_env.secret}",  # type: ignore[attr-defined]
        "Content-Type": "application/json",
    }
    status, _, body = http_request(
        url, method="POST", headers=headers, data=json.dumps(payload).encode()
    )
    assert status == 200
    res = json.loads(body.decode())
    assert res["posted"] == 2
    assert res["alerts"] == 5

    # Check fake discord received 2 posts: 1 condensed embed and 1 single embed
    posts = [r for r in fake_discord.received_requests if r["method"] == "POST"]
    assert len(posts) == 2
    embed0 = posts[0]["body"]["embeds"][0]
    assert "DiskFull (4)" in embed0["title"]
    assert "• Disk full on srv-01" in embed0["description"]

    embed1 = posts[1]["body"]["embeds"][0]
    assert "WARNING: HighCPU" in embed1["title"]
    assert embed1["fields"][0]["value"] == "srv-05"


def test_delivery_failure_returns_502(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify 502 is returned with posted and alerts count when any embed fails."""
    relay_env.check_discord_api()

    # 1. Grafana with 2 embeds, where the second post fails
    fake_discord.post_status_codes = [200, 500]
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "FirstAlert", "severity": "warning"},
                "annotations": {"summary": "First alert"},
            },
            {
                "status": "firing",
                "labels": {"alertname": "SecondAlert", "severity": "critical"},
                "annotations": {"summary": "Second alert"},
            },
        ]
    }
    url = f"{relay_env.base_url}/grafana"
    headers = {
        "Authorization": f"Bearer {relay_env.secret}",
        "Content-Type": "application/json",
    }
    status, _, body = http_request(
        url, method="POST", headers=headers, data=json.dumps(payload).encode()
    )
    assert status == 502
    res = json.loads(body.decode())
    assert res == {"posted": 1, "alerts": 2}

    # 2. Splunk where delivery fails
    fake_discord.post_status_codes = [500]
    splunk_payload = {
        "search_name": "Test Alert",
        "result": {"src_ip": "192.0.2.1"},
    }
    splunk_url = f"{relay_env.base_url}/splunk"
    status, _, body = http_request(
        splunk_url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(splunk_payload).encode(),
    )
    assert status == 502
    res = json.loads(body.decode())
    assert res == {"posted": 0, "alerts": 1}


def test_splunk_payload(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify Splunk webhook is received from allowed IP and mapped to embed."""
    relay_env.check_discord_api()

    payload = {
        "search_name": "Firewall - Port scan detected",
        "sid": "1727263000.123",
        "app": "search",
        "results_link": "https://splunk.example.com/alerts/1727263000.123",
        "result": {
            "src_ip": "192.0.2.100",
            "dest_port": "22",
            "action": "blocked",
            "count": "45",
        },
    }

    url = f"{relay_env.base_url}/splunk"  # type: ignore[attr-defined]
    headers = {"Content-Type": "application/json"}
    status, _, body = http_request(
        url, method="POST", headers=headers, data=json.dumps(payload).encode()
    )
    assert status == 200
    res = json.loads(body.decode())
    assert res["posted"] == 1

    posts = [r for r in fake_discord.received_requests if r["method"] == "POST"]
    assert len(posts) == 1
    embed = posts[0]["body"]["embeds"][0]
    assert embed["title"] == "SECURITY Port scan detected"
    assert embed["color"] == 0x7048E8
    assert embed["footer"]["text"] == "Splunk · Firewall · search"
    field_names = [f["name"] for f in embed["fields"]]
    assert "src_ip" in field_names
    assert "dest_port" in field_names


def test_wrong_secret_401(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify unauthorized Grafana requests answer 401."""
    url = f"{relay_env.base_url}/grafana"  # type: ignore[attr-defined]

    # Wrong secret
    headers = {
        "Authorization": "Bearer invalid-token",
        "Content-Type": "application/json",
    }
    status, _, body = http_request(url, method="POST", headers=headers, data=b"{}")
    assert status == 401
    assert b"unauthorized" in body

    # Missing authorization header
    status, _, body = http_request(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    assert status == 401
    assert b"unauthorized" in body

    # No messages should have been posted
    posts = [r for r in fake_discord.received_requests if r["method"] == "POST"]
    assert len(posts) == 0


def test_disallowed_source_403(fake_discord: FakeDiscordServer) -> None:
    """Verify Splunk requests from disallowed source IP answer 403."""
    config = RelayConfig(
        listen_address="127.0.0.1",
        listen_port=0,
        allowed_splunk_sources=["192.0.2.50"],  # Does not include 127.0.0.1
        api_base_url=fake_discord.base_url,  # type: ignore[attr-defined]
        channel_id="1234567890",
    )
    server = create_server(config, secret="s", bot_token="t")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        server.set_discord_ready(True)
        url = f"http://127.0.0.1:{server.server_address[1]}/splunk"
        status, _, body = http_request(
            url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b"{}",
        )
        assert status == 403
        assert b"forbidden" in body
    finally:
        server.shutdown()
        server.server_close()


def test_oversized_body_rejected(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify request bodies exceeding max_request_bytes are rejected with 413."""
    relay_env.check_discord_api()
    relay_env.config.max_request_bytes = 100

    url = f"{relay_env.base_url}/grafana"  # type: ignore[attr-defined]
    headers = {
        "Authorization": f"Bearer {relay_env.secret}",  # type: ignore[attr-defined]
        "Content-Type": "application/json",
    }
    oversized_data = json.dumps({"alerts": ["x" * 200]}).encode()
    status, _, body = http_request(
        url, method="POST", headers=headers, data=oversized_data
    )
    assert status == 413
    assert b"payload too large" in body


def test_class_mapping_and_default() -> None:
    """Verify alert class prefixes, colors, and fallback defaults."""
    config = RelayConfig()

    # Updates class
    title, color = title_and_color(
        {"class": "updates", "alertname": "PkgUpgrade", "severity": "info"},
        resolved=False,
        config=config,
    )
    assert title == "UPDATES INFO: PkgUpgrade"
    assert color == 0x1C7ED6

    # Security class
    title, color = title_and_color(
        {"class": "security", "alertname": "RootLogin", "severity": "warning"},
        resolved=False,
        config=config,
    )
    assert title == "SECURITY WARNING: RootLogin"
    assert color == 0x7048E8

    # Default class (infrastructure) with critical severity
    title, color = title_and_color(
        {"alertname": "NodeDown", "severity": "critical"},
        resolved=False,
        config=config,
    )
    assert title == "CRITICAL: NodeDown"
    assert color == 0xE03131

    # Resolved alert
    title, color = title_and_color(
        {"class": "security", "alertname": "RootLogin"},
        resolved=True,
        config=config,
    )
    assert title == "Resolved: SECURITY RootLogin"
    assert color == DEFAULT_RESOLVED_COLOR


def test_secrets_never_logged(
    relay_env: DiscordRelayServer,
    fake_discord: FakeDiscordServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that credentials and tokens are never logged."""
    relay_env.check_discord_api()
    secret_value = relay_env.secret  # type: ignore[attr-defined]
    bot_token_value = relay_env.discord_client.bot_token

    with caplog.at_level(logging.DEBUG):
        # 1. Send invalid auth request
        url = f"{relay_env.base_url}/grafana"  # type: ignore[attr-defined]
        http_request(
            url,
            method="POST",
            headers={
                "Authorization": "Bearer bad-token",
                "Content-Type": "application/json",
            },
            data=b"{}",
        )

        # 2. Send valid auth request
        http_request(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {secret_value}",
                "Content-Type": "application/json",
            },
            data=json.dumps({"alerts": []}).encode(),
        )

    for record in caplog.records:
        assert secret_value not in record.message
        assert bot_token_value not in record.message
        assert "bad-token" not in record.message


def test_config_precedence(tmp_path: Path) -> None:
    """Verify precedence: CLI overrides config file, which overrides defaults."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[server]
listen_port = 8888
max_request_bytes = 500000

[discord]
mode = "bot"
channel_id = "channel-file-123"
retry_after_cap_seconds = 7.5
readiness_interval_seconds = 45.0
""",
        encoding="utf-8",
    )

    # File overrides default
    cfg = load_config(config_file)
    assert cfg.listen_port == 8888
    assert cfg.max_request_bytes == 500000
    assert cfg.channel_id == "channel-file-123"
    assert cfg.retry_after_cap_seconds == 7.5
    assert cfg.readiness_interval_seconds == 45.0

    # CLI overrides file
    parser = build_parser()
    args = parser.parse_args(
        [
            "--listen-port",
            "9999",
            "--channel-id",
            "cli-999",
            "--retry-after-cap",
            "3.0",
            "--readiness-interval",
            "15.0",
        ]
    )
    cfg = apply_cli_overrides(cfg, args)
    assert cfg.listen_port == 9999
    assert cfg.channel_id == "cli-999"
    assert cfg.max_request_bytes == 500000
    assert cfg.retry_after_cap_seconds == 3.0
    assert cfg.readiness_interval_seconds == 15.0


def test_configurator_non_overwrite(tmp_path: Path) -> None:
    """Verify configure.py refuses to overwrite existing config without --overwrite."""
    output_path = tmp_path / "config.local.toml"

    # First generation succeeds
    rc = configure.main(["--output", str(output_path)])
    assert rc == 0
    assert output_path.is_file()

    # Second run without --overwrite fails
    rc = configure.main(["--output", str(output_path)])
    assert rc == 1

    # Third run with --overwrite succeeds
    rc = configure.main(["--output", str(output_path), "--overwrite"])
    assert rc == 0


def test_cli_help() -> None:
    """Verify entry points answer --help with status 0."""
    result = subprocess.run(
        [sys.executable, str(RELAY_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Discord alert relay" in result.stdout

    result = subprocess.run(
        [sys.executable, str(CONFIGURE_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Generate config.local.toml" in result.stdout


def test_webhook_delivery_mode(fake_discord: FakeDiscordServer) -> None:
    """Verify delivery using incoming webhook mode."""
    webhook_url = f"{fake_discord.base_url}/webhooks/123/token-abc"  # type: ignore[attr-defined]
    config = RelayConfig(
        mode="webhook",
        timeout_seconds=5.0,
    )
    client = DiscordClient(config=config, webhook_url=webhook_url)

    # Check ready
    ready, detail = client.check_ready()
    assert ready is True
    assert "HTTP 200" in detail

    # Post embed
    embed = {"title": "Webhook Test Alert", "color": 0x123456}
    success, detail = client.post_embed(embed)
    assert success is True
    assert "HTTP 200" in detail

    posts = [r for r in fake_discord.received_requests if r["method"] == "POST"]
    assert any("Webhook Test Alert" in json.dumps(r["body"]) for r in posts)


def test_embed_limits_and_safety() -> None:
    """Verify embed trimming, silence link sizing, and safe URL rules."""
    config = RelayConfig()

    # 1. Silence link exceeding 1024 chars is excluded
    long_silence = "https://grafana.example.com/silence/new?matcher=" + ("a" * 1100)
    alert = {
        "status": "firing",
        "labels": {"alertname": "DiskIssue"},
        "silenceURL": long_silence,
    }
    embed = build_embed(alert, config)
    fields = embed.get("fields", [])
    assert not any(f.get("name") == "Silence" for f in fields)

    # 2. URLs must be well formed with a dot
    assert safe_url("https://example.com/path") == "https://example.com/path"
    assert safe_url("https://barehost/path") is None
    assert safe_url("ftp://example.com") is None

    # 3. Fit embed cuts description when exceeding limit
    big_embed = {
        "title": "Big Alert",
        "description": "x" * 7000,
    }
    fitted = fit_embed(big_embed)
    assert embed_length(fitted) <= 6000
    assert fitted["description"].endswith("…")


def test_ip_allowlist_matching() -> None:
    """Verify IP allowlist handles exact matches, CIDRs, and invalid inputs."""
    allowed = ["127.0.0.1", "192.0.2.0/24", "2001:db8::/32"]

    assert is_ip_allowed("127.0.0.1", allowed) is True
    assert is_ip_allowed("192.0.2.45", allowed) is True
    assert is_ip_allowed("198.51.100.1", allowed) is False
    assert is_ip_allowed("not-an-ip", allowed) is False
    assert is_ip_allowed("::ffff:192.0.2.55", allowed) is True


def test_rate_limit_429_retry_and_cap(fake_discord: FakeDiscordServer) -> None:
    """Verify 429 Retry-After handling, retry once within cap, and failure on cap."""
    config = RelayConfig(
        mode="bot",
        channel_id="12345",
        api_base_url=fake_discord.base_url,
        retry_after_cap_seconds=5.0,
    )
    slept: list[float] = []

    def mock_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = DiscordClient(
        config=config,
        bot_token="test-token",
        sleep_fn=mock_sleep,
    )

    # 1. 429 with Retry-After header <= cap (1.5s), retry succeeds (200)
    fake_discord.post_status_codes = [
        (429, {"Retry-After": "1.5"}, b'{"message": "rate limited"}'),
        200,
    ]
    embed = {"title": "Alert 1", "description": "testing 429 retry"}
    success, detail = client.post_embed(embed)
    assert success is True
    assert "HTTP 200" in detail
    assert slept == [1.5]

    # 2. 429 with retry_after in JSON body <= cap (2.5s), retry succeeds (200)
    fake_discord.post_status_codes = [
        (429, {}, b'{"message": "rate limited", "retry_after": 2.5}'),
        200,
    ]
    success, detail = client.post_embed(embed)
    assert success is True
    assert "HTTP 200" in detail
    assert slept == [1.5, 2.5]

    # 3. 429 with Retry-After > cap (6.0s > 5.0s): do not sleep or retry
    fake_discord.post_status_codes = [
        (429, {"Retry-After": "6.0"}, b'{"message": "rate limited"}'),
    ]
    success, detail = client.post_embed(embed)
    assert success is False
    assert "HTTP 429" in detail
    assert slept == [1.5, 2.5]

    # 4. 429 with Retry-After <= cap (2.0s), but retry also fails (HTTP 500)
    fake_discord.post_status_codes = [
        (429, {"Retry-After": "2.0"}, b'{"message": "rate limited"}'),
        500,
    ]
    success, detail = client.post_embed(embed)
    assert success is False
    assert "HTTP 500" in detail
    assert slept == [1.5, 2.5, 2.0]


def test_health_readiness_lifecycle_ready_not_ready_ready(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify /health transitions: 200 -> 503 on 401/403/network error -> 200."""
    health_url = f"{relay_env.base_url}/health"

    # 1. Initially make ready
    relay_env.check_discord_api()
    assert relay_env.is_discord_ready is True
    status, _, _ = http_request(health_url)
    assert status == 200

    # 2. Discord call returns 401: relay must be marked not ready
    fake_discord.post_status_codes = [401]
    embed = {"title": "Test alert", "description": "triggers 401"}
    success, _detail = relay_env.discord_client.post_embed(embed)
    assert success is False
    assert relay_env.is_discord_ready is False
    status, _, body = http_request(health_url)
    assert status == 503
    assert b"discord not ready" in body

    # 3. Discord recovers and periodic check restores readiness
    relay_env.config.readiness_interval_seconds = 0.05
    relay_env.start_poller(interval=0.05)
    try:
        deadline = time.time() + 1.0
        recovered = False
        while time.time() < deadline:
            status, _, _ = http_request(health_url)
            if status == 200 and relay_env.is_discord_ready:
                recovered = True
                break
            time.sleep(0.02)
        assert recovered is True
    finally:
        relay_env.stop_poller()


def test_health_marked_not_ready_on_network_failure(
    relay_env: DiscordRelayServer, fake_discord: FakeDiscordServer
) -> None:
    """Verify relay is marked not ready when a Discord call fails at network level."""
    health_url = f"{relay_env.base_url}/health"
    relay_env.check_discord_api()
    assert relay_env.is_discord_ready is True

    # Temporarily set invalid API base URL where connection fails immediately
    orig_url = relay_env.discord_client.config.api_base_url
    try:
        relay_env.discord_client.config.api_base_url = "http://127.0.0.1:1"
        embed = {"title": "Test alert"}
        success, detail = relay_env.discord_client.post_embed(embed)
        assert success is False
        assert "request failed" in detail
        assert relay_env.is_discord_ready is False
        status, _, body = http_request(health_url)
        assert status == 503
        assert b"discord not ready" in body
    finally:
        relay_env.discord_client.config.api_base_url = orig_url
