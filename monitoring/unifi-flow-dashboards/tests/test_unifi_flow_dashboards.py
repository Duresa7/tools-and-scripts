from __future__ import annotations

import http.server
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

# Load module under test
SPEC = importlib.util.spec_from_file_location(
    "unifi_flow_dashboards_mod", TOOL_DIR / "unifi_flow_dashboards.py"
)
assert SPEC and SPEC.loader
DASH_MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DASH_MOD
SPEC.loader.exec_module(DASH_MOD)

# Load configure module
CONF_SPEC = importlib.util.spec_from_file_location(
    "unifi_flow_dashboards_conf_mod", TOOL_DIR / "configure.py"
)
assert CONF_SPEC and CONF_SPEC.loader
CONF_MOD = importlib.util.module_from_spec(CONF_SPEC)
sys.modules[CONF_SPEC.name] = CONF_MOD
CONF_SPEC.loader.exec_module(CONF_MOD)


@pytest.fixture
def fake_splunk_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        mode = "results"
        received_auth: ClassVar[list[str | None]] = []

        def do_POST(self) -> None:
            Handler.received_auth.append(self.headers.get("Authorization"))
            content_len = int(self.headers.get("Content-Length", 0))
            _ = self.rfile.read(content_len)

            if Handler.mode == "results":
                body = json.dumps({"results": [{"count": "100"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif Handler.mode == "empty":
                body = json.dumps({"results": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif Handler.mode == "error":
                body = json.dumps(
                    {"messages": [{"type": "ERROR", "text": "Syntax error in search"}]}
                ).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif Handler.mode == "http_500":
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Internal Server Error")
            elif Handler.mode == "echo_token_error":
                auth = self.headers.get("Authorization", "")
                body = json.dumps(
                    {
                        "messages": [
                            {
                                "type": "ERROR",
                                "text": f"Server rejected auth header: {auth}",
                            }
                        ]
                    }
                ).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif Handler.mode == "missing_results":
                body = json.dumps({"sid": "12345.67"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        Handler.received_auth.clear()
        yield f"http://127.0.0.1:{port}", Handler
    finally:
        server.shutdown()
        server.server_close()


def test_build_valid_json_and_balanced_layout(tmp_path: Path) -> None:
    out_dir = tmp_path / "app_out"
    config = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(output_dir=out_dir)
    )
    rc = DASH_MOD.run_build(config)
    assert rc == 0

    dashboards = [
        "unifi_flow_insights.json",
        "unifi_threat_center.json",
        "unifi_client_activity.json",
    ]

    for dash_file in dashboards:
        path = out_dir / dash_file
        assert path.is_file(), f"missing dashboard output {dash_file}"
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)

        # Validate layout structure
        assert "layout" in data
        layout = data["layout"]
        assert layout["type"] == "absolute"
        width = layout["options"]["width"]
        height = layout["options"]["height"]
        assert width == 1440
        assert height > 0

        structure = layout["structure"]
        viz = data["visualizations"]
        data_sources = data["dataSources"]

        structure_ids = set()
        for block in structure:
            assert block["type"] == "block"
            item_id = block["item"]
            structure_ids.add(item_id)
            assert item_id in viz, f"block {item_id} not in visualizations"

            pos = block["position"]
            x, y, w, h = pos["x"], pos["y"], pos["w"], pos["h"]
            assert x >= 0, f"negative x: {x}"
            assert y >= 0, f"negative y: {y}"
            assert w > 0, f"invalid w: {w}"
            assert h > 0, f"invalid h: {h}"
            assert x + w <= width, f"item {item_id} exceeds width: {x} + {w} > {width}"
            assert y + h <= height, (
                f"item {item_id} exceeds height: {y} + {h} > {height}"
            )

        # Balanced: 1:1 match between layout items and visualizations
        assert structure_ids == set(viz.keys()), (
            "layout structure and visualizations mismatch"
        )

        # Every data source is referenced
        referenced_sources: set[str] = set()
        for v in viz.values():
            if "dataSources" in v:
                for ds_id in v["dataSources"].values():
                    assert ds_id in data_sources, (
                        f"visualization references unknown dataSource {ds_id}"
                    )
                    referenced_sources.add(ds_id)

        assert referenced_sources == set(data_sources.keys()), (
            "some data sources were created but never referenced by any visualization"
        )


def test_build_refuses_to_overwrite(tmp_path: Path) -> None:
    out_dir = tmp_path / "protected_out"
    config = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(output_dir=out_dir)
    )

    # Initial build succeeds
    rc1 = DASH_MOD.run_build(config)
    assert rc1 == 0

    # Build again without force must refuse
    rc2 = DASH_MOD.run_build(config, force=False)
    assert rc2 == 1

    # Build again with force succeeds
    rc3 = DASH_MOD.run_build(config, force=True)
    assert rc3 == 0


def test_verify_against_fake_splunk_results(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "results"
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="TEST_SPLUNK_TOKEN",
            verify_tls=False,
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc == 0


def test_verify_against_fake_splunk_empty_results(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "empty"
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="TEST_SPLUNK_TOKEN",
            verify_tls=False,
        )
    )
    # Default verify treats empty as non-fatal
    rc = DASH_MOD.run_verify(config, fail_on_empty=False)
    assert rc == 0

    # With fail_on_empty, empty queries cause exit code 1
    rc_empty_fail = DASH_MOD.run_verify(config, fail_on_empty=True)
    assert rc_empty_fail == 1


def test_verify_against_fake_splunk_http_errors(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "error"
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="TEST_SPLUNK_TOKEN",
            verify_tls=False,
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc == 1


def test_verify_auth_header_from_env_var(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "results"
    expected_token = "secret-splunk-token-abc"
    monkeypatch.setenv("MY_CUSTOM_TOKEN_ENV", expected_token)

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="MY_CUSTOM_TOKEN_ENV",
            verify_tls=False,
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc == 0
    assert len(handler.received_auth) > 0
    assert handler.received_auth[0] == f"Bearer {expected_token}"


def test_verify_token_never_printed(
    fake_splunk_server: tuple[str, type],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "http_500"
    secret_token = "super-secret-unguessable-token-xyz987"
    monkeypatch.setenv("SECRET_TOKEN_VAR", secret_token)

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="SECRET_TOKEN_VAR",
            verify_tls=False,
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc == 1

    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err


def test_config_precedence(tmp_path: Path) -> None:
    # 1. Default config
    default_cfg = DASH_MOD.load_config(None)
    assert default_cfg.splunk.url == "https://127.0.0.1:8089"
    assert default_cfg.splunk.timeout_seconds == 60.0

    # 2. Config file precedence over default
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text(
        """
[splunk]
url = "https://198.51.100.25:8089"
timeout_seconds = 45.0

[dashboards]
flow_index = "custom_fw"
""",
        encoding="utf-8",
    )
    loaded_cfg = DASH_MOD.load_config(cfg_file)
    assert loaded_cfg.splunk.url == "https://198.51.100.25:8089"
    assert loaded_cfg.splunk.timeout_seconds == 45.0
    assert loaded_cfg.dashboards.flow_index == "custom_fw"

    # 3. CLI arguments precedence over config file
    parser = DASH_MOD.build_parser()
    args = parser.parse_args(
        [
            "build",
            "--config",
            str(cfg_file),
            "--flow-index",
            "cli_override_index",
        ]
    )
    assert args.flow_index == "cli_override_index"


def test_configurator_non_overwrite(tmp_path: Path) -> None:
    cfg_dest = tmp_path / "config.local.toml"

    # Initial write succeeds
    rc1 = CONF_MOD.main(["--output", str(cfg_dest)])
    assert rc1 == 0
    assert cfg_dest.is_file()

    # Second write without overwrite fails
    rc2 = CONF_MOD.main(["--output", str(cfg_dest)])
    assert rc2 == 1

    # Overwrite with --overwrite succeeds
    rc3 = CONF_MOD.main(["--output", str(cfg_dest), "--overwrite"])
    assert rc3 == 0

    # Overwrite with --force succeeds
    rc4 = CONF_MOD.main(["--output", str(cfg_dest), "--force"])
    assert rc4 == 0


def test_help_subcommands() -> None:
    for cmd in (
        [sys.executable, str(TOOL_DIR / "unifi_flow_dashboards.py"), "--help"],
        [
            sys.executable,
            str(TOOL_DIR / "unifi_flow_dashboards.py"),
            "build",
            "--help",
        ],
        [
            sys.executable,
            str(TOOL_DIR / "unifi_flow_dashboards.py"),
            "verify",
            "--help",
        ],
        [sys.executable, str(TOOL_DIR / "configure.py"), "--help"],
    ):
        res = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=10
        )
        assert res.returncode == 0
        assert "help" in res.stdout.lower() or "usage:" in res.stdout.lower()


def test_default_flow_index_is_empty() -> None:
    assert DASH_MOD.DEFAULT_FLOW_INDEX == ""
    cfg = DASH_MOD.ToolConfig()
    assert cfg.dashboards.flow_index == ""
    files = DASH_MOD.generate_app_files(cfg.dashboards)
    macros = files["default/macros.conf"]
    assert "definition = sourcetype=unifi:flow\n" in macros


def test_build_exclusive_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "race_out"
    config = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(output_dir=out_dir)
    )

    # Pre-create a target file with sentinel content
    out_dir.mkdir(parents=True, exist_ok=True)
    target_file = out_dir / "unifi_flow_insights.json"
    target_file.write_text("SENTINEL_CONTENT", encoding="utf-8")

    # Bypass pre-check using monkeypatch to simulate race condition
    # between check and write
    monkeypatch.setattr(Path, "exists", lambda self: False)

    # Without force, run_build must fail using exclusive creation mode "x"
    rc = DASH_MOD.run_build(config, force=False)
    assert rc == 1
    # File must not be overwritten or truncated
    assert target_file.read_text(encoding="utf-8") == "SENTINEL_CONTENT"


def test_verify_fails_nonexistent_app_dir(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = fake_splunk_server
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")
    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url, auth_token_env="TEST_SPLUNK_TOKEN", verify_tls=False
        )
    )
    rc = DASH_MOD.run_verify(config, app_dir=Path("/nonexistent/app/dir/xyz123"))
    assert rc != 0


def test_verify_fails_malformed_dashboard_definition(
    tmp_path: Path,
    fake_splunk_server: tuple[str, type],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, _ = fake_splunk_server
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")
    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url, auth_token_env="TEST_SPLUNK_TOKEN", verify_tls=False
        )
    )
    broken_app = tmp_path / "broken_app"
    views_dir = broken_app / "default" / "data" / "ui" / "views"
    views_dir.mkdir(parents=True, exist_ok=True)
    broken_view = views_dir / "broken.xml"
    broken_view.write_text(
        '<dashboard version="2"><definition><![CDATA[\n'
        "INVALID JSON{\n"
        "]]></definition></dashboard>",
        encoding="utf-8",
    )
    rc = DASH_MOD.run_verify(config, app_dir=broken_app)
    assert rc != 0


def test_verify_rejects_state_changing_spl_commands(
    tmp_path: Path,
    fake_splunk_server: tuple[str, type],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, _ = fake_splunk_server
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")
    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url, auth_token_env="TEST_SPLUNK_TOKEN", verify_tls=False
        )
    )

    state_changing_cmds = [
        "outputlookup",
        "outputcsv",
        "collect",
        "tscollect",
        "sendemail",
        "delete",
        "script",
        "run",
        "sendalert",
        "meventcollect",
        "mcollect",
        "dump",
    ]

    for cmd in state_changing_cmds:
        app_dir = tmp_path / f"app_{cmd}"
        views_dir = app_dir / "default" / "data" / "ui" / "views"
        views_dir.mkdir(parents=True, exist_ok=True)
        view_file = views_dir / "test.xml"
        definition = {
            "visualizations": {},
            "dataSources": {
                "ds1": {"options": {"query": f"index=netfw | {cmd} target_table"}}
            },
        }
        view_file.write_text(
            '<dashboard version="2"><definition><![CDATA[\n'
            f"{json.dumps(definition)}\n"
            "]]></definition></dashboard>",
            encoding="utf-8",
        )
        rc = DASH_MOD.run_verify(config, app_dir=app_dir)
        assert rc != 0, f"command '{cmd}' was not rejected as state-changing"


def test_verify_response_missing_results_fails(
    fake_splunk_server: tuple[str, type], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "missing_results"
    monkeypatch.setenv("TEST_SPLUNK_TOKEN", "valid-test-token")
    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url, auth_token_env="TEST_SPLUNK_TOKEN", verify_tls=False
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc != 0


def test_verify_redacts_credentials_in_server_error(
    fake_splunk_server: tuple[str, type],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, handler = fake_splunk_server
    handler.mode = "echo_token_error"
    secret_token = "secret-token-must-be-redacted-999"
    monkeypatch.setenv("SECRET_TOKEN_VAR", secret_token)

    config = DASH_MOD.ToolConfig(
        splunk=DASH_MOD.SplunkSettings(
            url=url,
            auth_token_env="SECRET_TOKEN_VAR",
            verify_tls=False,
        )
    )
    rc = DASH_MOD.run_verify(config)
    assert rc != 0

    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err
    assert "[REDACTED]" in (captured.out + captured.err)


def test_cli_argument_parsing_errors_exit_2() -> None:
    res = subprocess.run(
        [sys.executable, str(TOOL_DIR / "unifi_flow_dashboards.py"), "--invalid-opt"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 2

    res_missing_subcmd = subprocess.run(
        [sys.executable, str(TOOL_DIR / "unifi_flow_dashboards.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_missing_subcmd.returncode == 2


def test_intrusion_detection_not_blocked_exemption_spl(tmp_path: Path) -> None:
    # Finding 1: Default empty settings must omit NOT (...) clause entirely
    out_dir_empty = tmp_path / "out_empty"
    config_empty = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(output_dir=out_dir_empty)
    )
    rc = DASH_MOD.run_build(config_empty)
    assert rc == 0
    saved_empty = (out_dir_empty / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert "[UniFi - Intrusion detection not blocked]" in saved_empty
    assert "proxy-host" not in saved_empty
    assert "198.51.100.2" not in saved_empty
    assert 'NOT (rule="Web Infrastructure Servers"' not in saved_empty

    # Configured exemptions must produce the NOT (...) clause
    out_dir_custom = tmp_path / "out_custom"
    config_custom = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(
            output_dir=out_dir_custom,
            intrusion_exempt_sources=("proxy-host", "198.51.100.2"),
            intrusion_exempt_rule="Web Infrastructure Servers",
        )
    )
    rc_custom = DASH_MOD.run_build(config_custom)
    assert rc_custom == 0
    saved_custom = (out_dir_custom / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert (
        'NOT (rule="Web Infrastructure Servers" src IN ("proxy-host", "198.51.100.2"))'
        in saved_custom
    )


def test_internal_host_blocked_repeatedly_search(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Finding 2: When internal_networks is empty, skip generating and print a note
    out_dir_empty = tmp_path / "out_empty"
    config_empty = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(
            output_dir=out_dir_empty, internal_networks=()
        )
    )
    rc = DASH_MOD.run_build(config_empty)
    assert rc == 0
    saved_empty = (out_dir_empty / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert "[UniFi - Internal host blocked repeatedly]" not in saved_empty
    captured = capsys.readouterr()
    assert (
        "note: skipping 'UniFi - Internal host blocked repeatedly' "
        "(internal_networks is empty)" in captured.out
    )

    # When internal_networks has CIDRs, generate search with where cidrmatch(...) OR ...
    out_dir_custom = tmp_path / "out_custom"
    cidrs = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
    config_custom = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(
            output_dir=out_dir_custom, internal_networks=cidrs
        )
    )
    rc_custom = DASH_MOD.run_build(config_custom)
    assert rc_custom == 0
    saved_custom = (out_dir_custom / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert "[UniFi - Internal host blocked repeatedly]" in saved_custom
    expected_clause = (
        'where cidrmatch("192.0.2.0/24", src_ip) OR '
        'cidrmatch("198.51.100.0/24", src_ip) OR '
        'cidrmatch("203.0.113.0/24", src_ip)'
    )
    assert expected_clause in saved_custom


def test_all_generated_searches_have_balanced_parentheses_and_quotes(
    tmp_path: Path,
) -> None:
    # Finding 2 requirement: every generated search has balanced parentheses and quotes
    out_dir = tmp_path / "app_balanced"
    config = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(
            output_dir=out_dir,
            internal_networks=("192.0.2.0/24", "198.51.100.0/24"),
            admin_networks=("192.0.2.0/24", "203.0.113.0/24"),
            scan_exempt_sources=("scanner.example.net",),
            intrusion_exempt_sources=("proxy-host", "198.51.100.2"),
            intrusion_exempt_rule="Web Infrastructure Servers",
            intrusion_exempt_zones=("DMZ",),
            intrusion_exempt_zone_rule="Web Infrastructure Servers",
        )
    )
    rc = DASH_MOD.run_build(config)
    assert rc == 0

    panels, searches = DASH_MOD.collect_queries(app_dir=out_dir)
    assert panels
    assert searches

    for dash_name, key, query in panels:
        assert query.count("(") == query.count(")"), (
            f"unbalanced parentheses in panel query {dash_name} [{key}]: {query}"
        )
        cleaned = query.replace(r"\"", "")
        assert cleaned.count('"') % 2 == 0, (
            f"unbalanced double quotes in panel query {dash_name} [{key}]: {query}"
        )

    for name, query, _earliest, _latest in searches:
        assert query.count("(") == query.count(")"), (
            f"unbalanced parentheses in search '{name}': {query}"
        )
        cleaned = query.replace(r"\"", "")
        assert cleaned.count('"') % 2 == 0, (
            f"unbalanced double quotes in search '{name}': {query}"
        )


def test_other_environment_specific_searches_configurable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Finding 3: Default empty values must not contain environment-specific
    # hostnames or zones
    out_dir_empty = tmp_path / "out_empty"
    config_empty = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(output_dir=out_dir_empty)
    )
    rc = DASH_MOD.run_build(config_empty)
    assert rc == 0
    saved_empty = (out_dir_empty / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert "scanner.example.net" not in saved_empty
    assert "DMZ" not in saved_empty
    assert (
        "[UniFi - Admin console access from an unexpected network]" not in saved_empty
    )
    captured = capsys.readouterr()
    assert (
        "note: skipping 'UniFi - Admin console access from an unexpected "
        "network' (admin_networks is empty)" in captured.out
    )

    # Configured values generate proper clauses
    out_dir_custom = tmp_path / "out_custom"
    config_custom = DASH_MOD.ToolConfig(
        dashboards=DASH_MOD.DashboardSettings(
            output_dir=out_dir_custom,
            admin_networks=("192.0.2.0/24", "198.51.100.0/24"),
            scan_exempt_sources=("scanner.example.net",),
            intrusion_exempt_zones=("DMZ",),
            intrusion_exempt_zone_rule="Web Infrastructure Servers",
        )
    )
    rc_custom = DASH_MOD.run_build(config_custom)
    assert rc_custom == 0
    saved_custom = (out_dir_custom / "default" / "savedsearches.conf").read_text(
        encoding="utf-8"
    )
    assert "[UniFi - Admin console access from an unexpected network]" in saved_custom
    assert (
        'where NOT (cidrmatch("192.0.2.0/24", src_ip) OR '
        'cidrmatch("198.51.100.0/24", src_ip))' in saved_custom
    )
    assert 'src!="scanner.example.net"' in saved_custom
    assert 'NOT (rule="Scanning Activity" src="scanner.example.net")' in saved_custom
    assert 'NOT (rule="Web Infrastructure Servers" src_zone="DMZ")' in saved_custom
