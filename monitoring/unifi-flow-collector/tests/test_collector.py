import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "unifi_flow_test_module", TOOL / "unifi_flow_collector.py"
)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)

FLOW = {
    "id": "flow-one",
    "time": 1700000000000,
    "action": "blocked",
    "protocol": "TCP",
    "service": "HTTPS",
    "source": {"ip": "192.0.2.10", "port": 4567, "client_name": "client"},
    "destination": {
        "ip": "198.51.100.10",
        "port": 443,
        "zone_name": "External",
        "domains": ["example.net"],
    },
    "traffic_data": {"bytes_total": 100, "packets_total": 2, "bytes_rx": 40},
    "duration_milliseconds": 2500,
    "policies": [{"name": "test-rule", "ips_category": "test-category"}],
}


@pytest.fixture
def servers():
    running = []

    def start(respond):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append((self.path, dict(self.headers), body))
                status, payload, headers = respond(calls)
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode()
                )
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}", calls

    yield start
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def setup(tmp_path, servers):
    unifi, api_calls = servers(
        lambda _: (200, {"data": [FLOW, FLOW], "has_next": False}, {})
    )
    hec, hec_calls = servers(lambda _: (200, {"code": 0}, {}))
    config = tmp_path / "config.local.toml"
    config.write_text(
        f'[unifi]\ncontroller_url = "{unifi}"\n'
        f'[hec]\nurl = "{hec}/services/collector/event"\n'
    )
    return config, api_calls, hec_calls


def cli(config, *args, secret="test-api-credential"):
    return subprocess.run(
        [
            sys.executable,
            str(TOOL / "unifi_flow_collector.py"),
            "--config",
            str(config),
            "--once",
            *args,
        ],
        env={
            **os.environ,
            "UNIFI_API_KEY": secret,
            "SPLUNK_HEC_TOKEN": "test-hec-credential",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


@pytest.mark.parametrize("mode", [[], ["--dry-run"], ["--stdout"]])
def test_preview_maps_cim_without_hec_or_state(setup, mode):
    config, api, hec = setup
    result = cli(config, *mode)
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    event = envelope["event"]
    assert event["src_ip"] == "192.0.2.10"
    assert event["dest_ip"] == "198.51.100.10"
    assert event["src"] == "client"
    assert event["dest_port"] == 443
    assert event["transport"] == "tcp"
    assert event["bytes"] == 100
    assert event["packets"] == 2
    assert event["bytes_in"] == 40
    assert event["duration"] == 2.5
    assert event["action"] == "blocked"
    assert event["app"] == "HTTPS"
    assert event["rule"] == "test-rule"
    assert event["ips_category"] == "test-category"
    assert event["is_blocked"] == event["is_external"] == 1
    assert envelope["time"] == FLOW["time"] / 1000
    assert envelope["sourcetype"] == "unifi:flow"
    assert len(api) == 1 and not hec
    assert api[0][1]["X-Api-Key"] == "test-api-credential"
    assert set(config.parent.iterdir()) == {config}
    assert "test-api-credential" not in result.stdout + result.stderr
    assert "test-hec-credential" not in result.stdout + result.stderr


def test_send_deduplicates_and_resumes_checkpoint(setup):
    config, api, hec = setup
    first = cli(config, "--send", "--index", "test-index")
    assert first.returncode == 0, first.stderr
    state = config.with_name("checkpoint.json")
    saved = json.loads(state.read_text())
    assert saved == {"last_time_ms": FLOW["time"], "seen": [FLOW["id"]]}
    second = cli(config, "--send")
    assert second.returncode == 0, second.stderr
    assert len(hec) == 1
    assert json.loads(hec[0][2])["index"] == "test-index"
    assert hec[0][1]["Authorization"] == "Splunk test-hec-credential"
    query = json.loads(api[1][2])
    assert query["timestampFrom"] == FLOW["time"] - 300000
    assert all(query[key] == [] for key in M.QUERY_ARRAYS)
    assert not list(config.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (500, {"code": 9}),
        (200, {"code": 6}),
        (200, b"invalid JSON"),
        (200, {"code": False}),
        (200, {"code": "test-hec-credential"}),
    ],
)
def test_hec_failures_do_not_checkpoint_or_print_credentials(
    setup, servers, status, payload
):
    config, _, _ = setup
    if isinstance(payload, dict):
        payload["text"] = "test-api-credential test-hec-credential"
    url, calls = servers(lambda _: (status, payload, {}))
    result = cli(config, "--send", "--hec-url", url)
    assert result.returncode == 3
    assert len(calls) == 1
    assert not config.with_name("checkpoint.json").exists()
    assert "test-api-credential" not in result.stdout + result.stderr
    assert "test-hec-credential" not in result.stdout + result.stderr


def test_rejected_api_key_is_not_printed(setup, servers):
    config, _, hec = setup
    url, _ = servers(lambda _: (401, {"text": "test-api-credential"}, {}))
    result = cli(config, "--send", "--controller-url", url)
    assert result.returncode == 3
    assert "test-api-credential" not in result.stdout + result.stderr
    assert not hec


def test_invalid_header_credential_is_not_printed(setup):
    config, api, hec = setup
    result = cli(config, "--send", secret="private-marker\ninjected")
    assert result.returncode == 1
    assert "private-marker" not in result.stdout + result.stderr
    assert not api and not hec


def test_redirect_does_not_forward_credentials(setup, servers):
    config, _, _ = setup
    target, calls = servers(lambda _: (200, {"code": 0}, {}))
    redirect, _ = servers(lambda _: (307, {}, {"Location": target}))
    result = cli(config, "--send", "--hec-url", redirect)
    assert result.returncode == 3
    assert not calls


def test_partial_batch_failure_preserves_only_accepted_ids(setup, servers, monkeypatch):
    config_path, _, _ = setup
    monkeypatch.setattr(M, "HEC_BATCH", 1)
    url, _ = servers(lambda calls: (200, {"code": 0 if len(calls) == 1 else 6}, {}))
    settings = M.HecSettings(url=url)
    checkpoint = M.Checkpoint(config_path.with_name("checkpoint.json"), last_time_ms=12)
    pending = [("one", 100, {}), ("two", 200, {})]
    with pytest.raises(M.RemoteError):
        M.deliver(
            pending,
            M.HecSink(settings, "test-token"),
            checkpoint,
            settings,
            "example.net",
            persist=True,
        )
    saved = M.Checkpoint.load(checkpoint.path, quarantine_invalid=False)
    assert list(saved.seen) == ["one"]
    assert saved.last_time_ms == 12


def test_preview_preserves_invalid_checkpoint(setup):
    config, _, hec = setup
    state = config.with_name("checkpoint.json")
    state.write_text("unrelated content")
    result = cli(config)
    assert result.returncode == 0, result.stderr
    assert state.read_text() == "unrelated content"
    assert not list(config.parent.glob("*.invalid-*"))
    assert not hec


def test_send_quarantines_invalid_checkpoint(setup):
    config, _, _ = setup
    state = config.with_name("checkpoint.json")
    state.write_text("unrelated content")
    result = cli(config, "--send")
    assert result.returncode == 0, result.stderr
    assert next(config.parent.glob("*.invalid-*")).read_text() == "unrelated content"
    assert json.loads(state.read_text())["seen"] == [FLOW["id"]]


def test_config_precedence_and_relative_paths(tmp_path):
    config = M.parse_config(
        {
            "unifi": {"controller_url": "https://unifi.example.net"},
            "hec": {"index": "configured"},
            "collector": {"poll_interval_seconds": 99},
        },
        tmp_path,
    )
    args = M.build_parser().parse_args(
        [
            "--config",
            "unused",
            "--index",
            "override",
            "--site",
            "second",
            "--poll-interval",
            "42",
        ]
    )
    resolved = M.resolve_config(config, args)
    assert resolved.hec.index == "override"
    assert resolved.unifi.site == "second"
    assert resolved.collector.poll_interval == 42
    assert resolved.collector.lookback == 900
    assert resolved.collector.state_path == tmp_path / "checkpoint.json"
    assert config.collector.poll_interval == 99
    assert resolved.unifi.verify_tls and resolved.hec.verify_tls


def test_required_controller_can_come_from_cli(setup):
    config, _, _ = setup
    url = M.load_config(config).unifi.controller_url
    config.write_text("")
    result = cli(config, "--controller-url", url)
    assert result.returncode == 0, result.stderr


def test_configurator_no_overwrite_and_explicit_overwrite(tmp_path):
    output = tmp_path / "config.local.toml"
    command = [
        sys.executable,
        str(TOOL / "configure.py"),
        "--output",
        str(output),
        "--controller-url",
        "https://controller.example.net",
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    assert (
        M.load_config(output).unifi.controller_url == "https://controller.example.net"
    )
    original = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 1
    assert "refusing to replace" in second.stderr
    assert output.read_bytes() == original
    third = subprocess.run(
        [*command, "--overwrite", "--site", "second"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert third.returncode == 0, third.stderr
    assert M.load_config(output).unifi.site == "second"
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
    assert set(tmp_path.iterdir()) == {output}


@pytest.mark.parametrize("filename", ["configure.py", "unifi_flow_collector.py"])
def test_help(filename):
    result = subprocess.run(
        [sys.executable, str(TOOL / filename), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--help" in result.stdout


def test_page_cap_does_not_advance_or_send(setup, servers, monkeypatch):
    config_path, _, hec = setup
    monkeypatch.setattr(M, "MAX_PAGES", 2)
    url, calls = servers(lambda _: (200, {"data": [FLOW], "has_next": True}, {}))
    config = M.load_config(config_path)
    checkpoint = M.Checkpoint(config.collector.state_path)
    with pytest.raises(M.RemoteError, match="page cap"):
        M.fetch_new_flows(
            M.UniFiClient(M.UniFiSettings(controller_url=url), "key"),
            checkpoint,
            config.collector,
            "default",
            0,
            FLOW["time"],
        )
    assert len(calls) == 2 and not hec
    assert checkpoint.last_time_ms == 0 and not checkpoint.seen


def test_session_refresh_retains_csrf_and_cookies(servers):
    def respond(calls):
        path, headers, body = calls[-1]
        if path == "/api/auth/login":
            assert json.loads(body)["password"] == "session-test-password"
            return (
                200,
                {},
                {"X-CSRF-Token": "csrf-test", "Set-Cookie": "TOKEN=test; Path=/"},
            )
        assert headers["X-Csrf-Token"] == "csrf-test"
        assert headers["Cookie"] == "TOKEN=test"
        return (401 if len(calls) == 2 else 200), {"data": []}, {}

    url, calls = servers(respond)
    settings = M.UniFiSettings(
        controller_url=url,
        api_key_env="",
        username="reader",
        password_env="UNIFI_PASSWORD",
    )
    assert M.UniFiClient(settings, "session-test-password").post("/flows", {}) == {
        "data": []
    }
    assert len(calls) == 4


def test_checkpoint_lock_refuses_second_writer(tmp_path):
    if M.fcntl is None:
        pytest.skip("POSIX lock only")
    with (
        M.state_lock(tmp_path / "state.json"),
        pytest.raises(ValueError, match="another collector"),
        M.state_lock(tmp_path / "state.json"),
    ):
        pytest.fail("second writer entered")


def test_checkpoint_replace_failure_keeps_original_and_removes_temporary(
    tmp_path, monkeypatch
):
    path = tmp_path / "checkpoint.json"
    original = '{"last_time_ms": 12, "seen": ["old"]}'
    path.write_text(original)
    checkpoint = M.Checkpoint(path, last_time_ms=20, seen=["new"])

    def fail_replace(*_args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(M.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        checkpoint.save()
    assert path.read_text() == original
    assert set(tmp_path.iterdir()) == {path}


def test_preview_needs_no_hec_token(setup):
    config, _, hec = setup
    environment = {**os.environ, "UNIFI_API_KEY": "test-api-credential"}
    environment.pop("SPLUNK_HEC_TOKEN", None)
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL / "unifi_flow_collector.py"),
            "--config",
            str(config),
            "--once",
            "--dry-run",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["event"]["flow_id"] == FLOW["id"]
    assert not hec
