import importlib.util
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

TOOL = Path(__file__).resolve().parents[1]
FIXTURES = TOOL / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location(
    "grafana_dashboard_check", TOOL / "grafana_dashboards.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def cli(*args, script="grafana_dashboards.py"):
    return subprocess.run(
        [sys.executable, str(TOOL / script), *map(str, args)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def prometheus():
    state = SimpleNamespace(
        status=200, result=[{"metric": {}, "value": [1, "1"]}], error=None, requests=[]
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            if state.status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            payload = {
                "status": "success",
                "data": {"resultType": "vector", "result": state.result},
            }
            if state.error is not None:
                payload = {"status": "error", "error": state.error}
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def dashboard(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text(
        json.dumps(
            {
                "uid": "example",
                "title": "Example",
                "panels": [
                    {"title": "Targets", "targets": [{"refId": "A", "expr": "up"}]}
                ],
            }
        )
    )
    return path


def test_layout_valid_fixtures_are_read_only():
    before = {path: path.read_bytes() for path in FIXTURES.rglob("*.json")}
    result = cli("layout", FIXTURES / "valid")
    assert result.returncode == 0, result.stderr
    assert "2 dashboards, 2 unique uids" in result.stdout
    assert "assertion-passed" in result.stdout
    assert {path: path.read_bytes() for path in before} == before


def test_layout_invalid_fixture_reports_every_problem():
    result = cli("layout", FIXTURES / "invalid")
    assert result.returncode == 2
    for finding in (
        "'Memory' overlaps 'CPU' at column 6, row 2",
        "'Disk' spans columns 16..26, outside 0..24",
        "'Network' has size 0x4",
        "'Load' has no complete integer gridPos",
        "'Uptime' spans columns -1..3, outside 0..24",
        "panel id 1 is used by 'CPU' and 'Swap'",
        "more than one datasource uid in use",
        "7 layout problem(s)",
    ):
        assert finding in result.stderr


def test_duplicate_dashboard_uids_fail(tmp_path):
    source = FIXTURES / "valid" / "overview.json"
    (tmp_path / "first.json").write_bytes(source.read_bytes())
    (tmp_path / "second.json").write_bytes(source.read_bytes())
    result = cli("layout", tmp_path)
    assert result.returncode == 2
    assert "uid 'fleet-overview' is used by both" in result.stderr


def test_queries_return_data(prometheus, dashboard):
    before = dashboard.read_bytes()
    result = cli("queries", dashboard, "--url", prometheus.url)
    assert result.returncode == 0, result.stderr
    assert "1 returned data" in result.stdout
    assert prometheus.requests == [("/api/v1/query?query=up", None)]
    assert dashboard.read_bytes() == before


def test_empty_result_fails(prometheus, dashboard):
    prometheus.result = []
    result = cli("queries", dashboard, "--url", prometheus.url)
    assert result.returncode == 3
    assert "query-empty: Example / Targets [A]" in result.stderr


@pytest.mark.parametrize("source", ["cli", "config", "file"])
def test_allow_empty_titles(prometheus, dashboard, tmp_path, source):
    prometheus.result = []
    args = []
    if source == "cli":
        args = ["--allow-empty", "Targets"]
    elif source == "config":
        config = tmp_path / "config.local.json"
        config.write_text(json.dumps({"queries": {"allow_empty_titles": ["Targets"]}}))
        args = ["--config", config]
    else:
        allow = tmp_path / "allow-empty.json"
        allow.write_text('["Targets"]')
        args = ["--allow-empty-file", allow]
    result = cli("queries", dashboard, "--url", prometheus.url, *args)
    assert result.returncode == 0, result.stderr
    assert "query-allowed-empty: Example / Targets [A]" in result.stdout
    assert "1 allowed empty" in result.stdout


def test_allow_empty_does_not_hide_errors(prometheus, dashboard):
    prometheus.status = 422
    prometheus.error = "invalid expression"
    result = cli(
        "queries", dashboard, "--url", prometheus.url, "--allow-empty", "Targets"
    )
    assert result.returncode == 2
    assert "HTTP 422" in result.stderr
    assert "query-allowed-empty" not in result.stdout


def test_substitution_nested_queries_and_hidden_targets(prometheus):
    result = cli(
        "queries",
        FIXTURES / "valid",
        "--url",
        prometheus.url,
        "--var",
        "instance=server.example.net",
        "--var",
        "job=node",
        "--var",
        "window=10m",
    )
    assert result.returncode == 0, result.stderr
    expressions = [
        parse_qs(urlparse(path).query)["query"][0] for path, _ in prometheus.requests
    ]
    assert len(expressions) == 7
    assert all("$" not in expr and "hidden_series" not in expr for expr in expressions)
    assert 'sum(up{job=~"node", instance=~"server.example.net"})' in expressions
    assert any("[21600s]" in expr for expr in expressions)
    assert any("[5m]" in expr for expr in expressions)
    assert any("[10m]" in expr for expr in expressions)
    assert any("node_filesystem_avail_bytes" in expr for expr in expressions)


def test_variable_names_are_matched_whole():
    assert MODULE.interpolate(
        "$host $hostname ${host} $__range_s $__range",
        {"host": ".*", **MODULE.DEFAULT_VARIABLES},
    ) == (".* $hostname .* 21600 6h")


@pytest.mark.parametrize(
    "status, expected", [(400, 2), (500, 2), (401, 1), (403, 1), (404, 1), (302, 1)]
)
def test_http_errors_and_redirects(prometheus, status, expected):
    prometheus.status = status
    prometheus.error = "request rejected"
    result = cli("queries", FIXTURES / "valid", "--url", prometheus.url)
    assert result.returncode == expected
    assert f"HTTP {status}" in result.stderr
    assert len(prometheus.requests) == (1 if expected == 1 else 7)
    assert all(not path.startswith("/redirected") for path, _ in prometheus.requests)


@pytest.mark.parametrize("status", [200, 401, 422])
def test_named_token_is_sent_but_never_printed(
    prometheus, dashboard, monkeypatch, status
):
    token = "test-only-secret-credential"
    monkeypatch.setenv("DASHBOARD_TEST_TOKEN", token)
    prometheus.status = status
    if status != 200:
        prometheus.error = f"rejected Bearer {token}"
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--bearer-token-env",
        "DASHBOARD_TEST_TOKEN",
    )
    assert result.returncode == {200: 0, 401: 1, 422: 2}[status]
    assert prometheus.requests[0][1] == f"Bearer {token}"
    assert token not in result.stdout + result.stderr


def test_api_error_does_not_echo_token(prometheus, dashboard, monkeypatch):
    token = "test-only-api-secret"
    monkeypatch.setenv("DASHBOARD_TEST_TOKEN", token)
    prometheus.error = token
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--bearer-token-env",
        "DASHBOARD_TEST_TOKEN",
    )
    assert result.returncode == 2
    assert token not in result.stdout + result.stderr


def test_missing_token_fails_before_request(prometheus, dashboard, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TEST_TOKEN", raising=False)
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--bearer-token-env",
        "DASHBOARD_TEST_TOKEN",
    )
    assert result.returncode == 1
    assert "DASHBOARD_TEST_TOKEN" in result.stderr
    assert prometheus.requests == []


def test_dry_run_sends_nothing_and_needs_no_token(prometheus, dashboard, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TEST_TOKEN", raising=False)
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--dry-run",
        "--bearer-token-env",
        "DASHBOARD_TEST_TOKEN",
    )
    assert result.returncode == 0, result.stderr
    assert "planned-query: Example / Targets [A]: up" in result.stdout
    assert "no request was sent" in result.stdout
    assert prometheus.requests == []


def test_config_precedence_and_relative_paths(tmp_path):
    config_path = tmp_path / "config.local.json"
    config_path.write_text(
        json.dumps(
            {
                "dashboards": {
                    "paths": ["dashboards"],
                    "require_single_datasource": False,
                },
                "prometheus": {
                    "url": "https://prometheus.example.net",
                    "timeout_seconds": 12,
                    "ca_file": "ca.pem",
                    "bearer_token_env": "CONFIG_TOKEN",
                },
                "queries": {
                    "variables": {"job": "config-job", "__range": "12h"},
                    "allow_empty_titles": ["Configured"],
                    "allow_empty_file": "allow.json",
                },
            }
        )
    )
    defaults = MODULE.load_config(None)
    assert defaults.http.url == "http://127.0.0.1:9090"
    assert defaults.http.timeout == 30
    assert defaults.require_single_datasource is True
    assert defaults.allow_empty_titles == ()
    config = MODULE.load_config(config_path)
    parser = MODULE.build_parser()
    inherited = MODULE.resolve_query_settings(config, parser.parse_args(["queries"]))
    assert inherited == config
    assert config.dashboards == (tmp_path / "dashboards",)
    assert config.http.ca_file == tmp_path / "ca.pem"
    assert config.allow_empty_file == tmp_path / "allow.json"
    args = parser.parse_args(
        [
            "queries",
            "cli.json",
            "--url",
            "http://127.0.0.1:9091",
            "--timeout",
            "3",
            "--no-ca-file",
            "--no-auth",
            "--var",
            "job=cli-job",
            "--allow-empty",
            "Override",
            "--allow-empty-file",
            "other.json",
        ]
    )
    resolved = MODULE.resolve_query_settings(config, args)
    assert resolved.dashboards == (Path("cli.json"),)
    assert resolved.http.url == "http://127.0.0.1:9091"
    assert resolved.http.timeout == 3
    assert resolved.http.ca_file is None
    assert resolved.http.bearer_token_env == ""
    assert resolved.variables["job"] == "cli-job"
    assert resolved.variables["__range"] == "12h"
    assert resolved.variables["__interval"] == "1m"
    assert resolved.allow_empty_titles == ("Override",)
    assert resolved.allow_empty_file == Path("other.json")
    layout = MODULE.resolve_layout_settings(
        config, parser.parse_args(["layout", "cli.json", "--require-single-datasource"])
    )
    assert layout.require_single_datasource is True
    assert layout.dashboards == (Path("cli.json"),)


def test_config_used_by_cli_with_path_override(tmp_path):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps({"dashboards": {"paths": [str(FIXTURES / "invalid")]}})
    )
    assert cli("layout", "--config", config).returncode == 2
    assert cli("layout", FIXTURES / "valid", "--config", config).returncode == 0


def test_configurator_discovers_variables_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TEST_TOKEN", "test-only-config-secret")
    output = tmp_path / "config.local.json"
    args = [
        "--output",
        output,
        "--dashboard",
        FIXTURES / "valid",
        "--bearer-token-env",
        "DASHBOARD_TEST_TOKEN",
    ]
    result = cli(*args, script="configure.py")
    assert result.returncode == 0, result.stderr
    before = output.read_bytes()
    payload = json.loads(before)
    assert payload["queries"]["variables"] == {
        "instance": ".*",
        "job": ".*",
        "window": "10m",
    }
    assert payload["prometheus"]["bearer_token_env"] == "DASHBOARD_TEST_TOKEN"
    assert payload["_comment"].startswith("CUSTOMIZE:")
    assert MODULE.load_config(output).dashboards == (FIXTURES / "valid",)
    again = cli(*args, script="configure.py")
    assert again.returncode == 1
    assert "refusing to replace" in again.stderr
    assert output.read_bytes() == before
    assert (
        "test-only-config-secret" not in before.decode() + result.stdout + result.stderr
    )


def test_configurator_defaults_and_invalid_input(tmp_path):
    output = tmp_path / "config.local.json"
    result = cli("--output", output, script="configure.py")
    assert result.returncode == 0, result.stderr
    settings = MODULE.load_config(output)
    assert settings.http == MODULE.HttpSettings()
    assert settings.variables["instance"] == ".*"
    bad = tmp_path / "bad.json"
    result = cli(
        "--output", bad, "--dashboard", tmp_path / "missing", script="configure.py"
    )
    assert result.returncode == 1
    assert not bad.exists()


@pytest.mark.parametrize("subcommand", ["layout", "queries"])
def test_missing_or_malformed_input(subcommand, tmp_path):
    assert cli(subcommand, tmp_path / "missing").returncode == 1
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    result = cli(subcommand, invalid)
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_missing_allow_empty_file_fails_before_request(prometheus, dashboard, tmp_path):
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--allow-empty-file",
        tmp_path / "missing.json",
    )
    assert result.returncode == 1
    assert prometheus.requests == []


@pytest.mark.parametrize(
    "script,args,fragment",
    [
        ("grafana_dashboards.py", [], "layout"),
        ("grafana_dashboards.py", ["layout"], "--allow-mixed-datasources"),
        ("grafana_dashboards.py", ["queries"], "--bearer-token-env"),
        ("configure.py", [], "--dashboard"),
    ],
)
def test_help(script, args, fragment):
    result = cli(*args, "--help", script=script)
    assert result.returncode == 0, result.stderr
    assert fragment in result.stdout


def test_basic_auth_error_redacts_password_and_encoded_header(
    prometheus, dashboard, monkeypatch
):
    import base64

    password = "test-only-basic-password"
    monkeypatch.setenv("DASHBOARD_TEST_PASSWORD", password)
    encoded = base64.b64encode(f"metrics-reader:{password}".encode()).decode()
    prometheus.status = 400
    prometheus.error = f"rejected Basic {encoded} with password {password}"
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        "--basic-username",
        "metrics-reader",
        "--basic-password-env",
        "DASHBOARD_TEST_PASSWORD",
    )
    assert result.returncode == 2
    assert prometheus.requests[0][1] == f"Basic {encoded}"
    assert password not in result.stdout + result.stderr
    assert encoded not in result.stdout + result.stderr


@pytest.mark.parametrize("preview", [False, True])
def test_dashboard_without_queries_fails(prometheus, tmp_path, preview):
    dashboard = tmp_path / "empty.json"
    dashboard.write_text('{"title": "Empty", "panels": []}')
    result = cli(
        "queries",
        dashboard,
        "--url",
        prometheus.url,
        *(["--dry-run"] if preview else []),
    )
    assert result.returncode == 2
    assert "no PromQL queries found" in result.stderr
    assert prometheus.requests == []
