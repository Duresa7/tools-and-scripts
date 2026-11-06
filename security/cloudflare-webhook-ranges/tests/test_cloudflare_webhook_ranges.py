from __future__ import annotations

import argparse
import importlib.util
import json
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

SPEC = importlib.util.spec_from_file_location(
    "cloudflare_webhook_ranges", MODULE_DIR / "cloudflare_webhook_ranges.py"
)
assert SPEC and SPEC.loader
cloudflare_webhook_ranges = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cloudflare_webhook_ranges
SPEC.loader.exec_module(cloudflare_webhook_ranges)

SPEC_CFG = importlib.util.spec_from_file_location(
    "configure", MODULE_DIR / "configure.py"
)
assert SPEC_CFG and SPEC_CFG.loader
configure = importlib.util.module_from_spec(SPEC_CFG)
sys.modules[SPEC_CFG.name] = configure
SPEC_CFG.loader.exec_module(configure)

Settings = cloudflare_webhook_ranges.Settings
TOKEN_VARIABLE = cloudflare_webhook_ranges.TOKEN_VARIABLE

ACCOUNT = "0123456789abcdef0123456789abcdef"
LIST_ID = "11111111-2222-3333-4444-555555555555"
PUBLISHED = ["192.0.2.0/24", "198.51.100.0/24", "2001:db8::/32"]


class FakeApi(BaseHTTPRequestHandler):
    """GitHub's meta document and the Cloudflare list calls, in one server."""

    hooks: ClassVar[list[str]] = []
    items: ClassVar[set[str]] = set()
    accounts: ClassVar[list[str]] = [ACCOUNT]
    patches: ClassVar[list[dict[str, Any]]] = []
    tokens: ClassVar[list[str]] = []
    readback_lies: ClassVar[bool] = False

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.tokens.append(self.headers.get("Authorization", ""))
        if self.path == "/meta":
            self.send_json(200, {"hooks": self.hooks})
        elif self.path == "/accounts":
            self.send_json(
                200,
                {"success": True, "result": [{"id": value} for value in self.accounts]},
            )
        elif self.path == f"/accounts/{ACCOUNT}/gateway/lists?type=IP":
            self.send_json(
                200,
                {
                    "success": True,
                    "result": [
                        {"id": "other", "name": "Other"},
                        {"id": LIST_ID, "name": "GitHub Webhooks"},
                    ],
                },
            )
        elif self.path == f"/accounts/{ACCOUNT}/gateway/lists/{LIST_ID}/items":
            items = set(self.items)
            if self.readback_lies:
                items.discard(self.hooks[0])
            self.send_json(
                200,
                {"success": True, "result": [{"value": value} for value in items]},
            )
        else:
            self.send_json(404, {"success": False, "errors": [{"code": 404}]})

    def do_PATCH(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.patches.append(body)
        if self.path == f"/accounts/{ACCOUNT}/gateway/lists/{LIST_ID}":
            for entry in body["append"]:
                self.items.add(entry["value"])
            for value in body["remove"]:
                self.items.discard(value)
            self.send_json(200, {"success": True, "result": {"id": LIST_ID}})
        else:
            self.send_json(404, {"success": False, "errors": [{"code": 404}]})

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def api():
    FakeApi.hooks = list(PUBLISHED)
    FakeApi.items = set(PUBLISHED)
    FakeApi.accounts = [ACCOUNT]
    FakeApi.patches = []
    FakeApi.tokens = []
    FakeApi.readback_lies = False
    server = HTTPServer(("127.0.0.1", 0), FakeApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def settings_for(api_base: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "meta_url": f"{api_base}/meta",
        "api_url": api_base,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_no_change_sends_no_patch(api: str) -> None:
    result = cloudflare_webhook_ranges.sync(settings_for(api), "token", dry_run=False)
    assert result.count == 3
    assert not result.changed
    assert FakeApi.patches == []


def test_difference_is_one_patch_then_readback(api: str) -> None:
    FakeApi.items = {"192.0.2.0/24", "203.0.113.0/24"}
    result = cloudflare_webhook_ranges.sync(settings_for(api), "token", dry_run=False)
    assert result.appended == ("198.51.100.0/24", "2001:db8::/32")
    assert result.removed == ("203.0.113.0/24",)
    assert len(FakeApi.patches) == 1
    assert FakeApi.patches[0] == {
        "append": [{"value": "198.51.100.0/24"}, {"value": "2001:db8::/32"}],
        "remove": ["203.0.113.0/24"],
    }
    assert FakeApi.items == set(PUBLISHED)


def test_dry_run_reports_and_changes_nothing(api: str) -> None:
    FakeApi.items = {"192.0.2.0/24"}
    result = cloudflare_webhook_ranges.sync(settings_for(api), "token", dry_run=True)
    assert result.changed
    assert FakeApi.patches == []
    assert FakeApi.items == {"192.0.2.0/24"}


def test_short_answer_refuses_to_sync(api: str) -> None:
    FakeApi.hooks = ["192.0.2.0/24"]
    FakeApi.items = set()
    with pytest.raises(RuntimeError, match="fewer than the minimum"):
        cloudflare_webhook_ranges.sync(settings_for(api), "token", dry_run=False)
    assert FakeApi.patches == []


def test_readback_mismatch_fails(api: str) -> None:
    FakeApi.items = {"192.0.2.0/24"}
    FakeApi.readback_lies = True
    with pytest.raises(RuntimeError, match="after the update"):
        cloudflare_webhook_ranges.sync(settings_for(api), "token", dry_run=False)


def test_account_is_discovered_from_the_token(api: str) -> None:
    client = cloudflare_webhook_ranges.CloudflareLists(
        settings_for(api), "secret-token"
    )
    assert client.account_id == ACCOUNT
    assert "Bearer secret-token" in FakeApi.tokens
    FakeApi.accounts = [ACCOUNT, "another"]
    with pytest.raises(RuntimeError, match="sees 2 accounts"):
        cloudflare_webhook_ranges.CloudflareLists(settings_for(api), "secret-token")
    explicit = cloudflare_webhook_ranges.CloudflareLists(
        settings_for(api, account_id=ACCOUNT), "secret-token"
    )
    assert explicit.account_id == ACCOUNT


def test_ranges_are_normalised() -> None:
    assert cloudflare_webhook_ranges.ipaddress.ip_network("192.0.2.5/24", strict=False)
    FakeApi.hooks = ["192.0.2.5/24", "198.51.100.0/24", "2001:DB8::/32"]
    settings = Settings(minimum_ranges=1)
    normalised = {
        str(cloudflare_webhook_ranges.ipaddress.ip_network(v, strict=False))
        for v in FakeApi.hooks
    }
    assert normalised == {"192.0.2.0/24", "198.51.100.0/24", "2001:db8::/32"}
    assert settings.minimum_ranges == 1


def test_metrics_render_and_atomic_write(tmp_path: Path) -> None:
    body = cloudflare_webhook_ranges.render_metrics(
        ok=True, count=9, changed=True, last_success=1700000000.0, now=1700000001.0
    )
    assert "cloudflare_webhook_ranges_last_run_ok 1" in body
    assert "cloudflare_webhook_ranges_count 9" in body
    assert "cloudflare_webhook_ranges_changed 1" in body
    assert "cloudflare_webhook_ranges_last_success_timestamp_seconds 1700000000.000"
    target = tmp_path / "nested" / "ranges.prom"
    cloudflare_webhook_ranges.write_textfile(target, body)
    assert target.read_text(encoding="utf-8") == body
    assert target.stat().st_mode & 0o777 == 0o644
    assert list(target.parent.iterdir()) == [target]
    with pytest.raises(ValueError, match=r"\.prom"):
        cloudflare_webhook_ranges.write_textfile(tmp_path / "ranges.txt", body)


def test_failed_run_keeps_the_previous_success(
    api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ranges.prom"
    monkeypatch.setenv(TOKEN_VARIABLE, "token")
    assert (
        cloudflare_webhook_ranges.main(
            [
                "--meta-url",
                f"{api}/meta",
                "--api-url",
                api,
                "--output",
                str(target),
            ]
        )
        == 0
    )
    first = cloudflare_webhook_ranges.previous_success(
        target, "cloudflare_webhook_ranges"
    )
    assert first > 0
    FakeApi.hooks = []
    code = cloudflare_webhook_ranges.main(
        ["--meta-url", f"{api}/meta", "--api-url", api, "--output", str(target)]
    )
    assert code == 1
    text = target.read_text(encoding="utf-8")
    assert "cloudflare_webhook_ranges_last_run_ok 0" in text
    assert "cloudflare_webhook_ranges_count -1" in text
    assert cloudflare_webhook_ranges.previous_success(
        target, "cloudflare_webhook_ranges"
    ) == (first)


def test_missing_token_is_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    target = tmp_path / "never.prom"
    code = cloudflare_webhook_ranges.main(["--output", str(target)])
    assert code == 1
    assert TOKEN_VARIABLE in capsys.readouterr().err
    assert not target.exists()


def test_stdout_writes_nothing(
    api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, "token")
    target = tmp_path / "never.prom"
    code = cloudflare_webhook_ranges.main(
        [
            "--meta-url",
            f"{api}/meta",
            "--api-url",
            api,
            "--output",
            str(target),
            "--stdout",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert not target.exists()
    assert "cloudflare_webhook_ranges_count 3" in captured.out


def test_config_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[github]
meta_url = "http://127.0.0.1:9/meta"
minimum_ranges = 5

[cloudflare]
api_url = "http://127.0.0.1:9/"
account_id = "abc"
list_name = "From Config"
timeout_seconds = 7.5

[metrics]
output_path = "relative.prom"
metric_prefix = "custom"
""",
        encoding="utf-8",
    )
    settings = cloudflare_webhook_ranges.load_settings(config_file)
    assert settings.meta_url == "http://127.0.0.1:9/meta"
    assert settings.api_url == "http://127.0.0.1:9"
    assert settings.account_id == "abc"
    assert settings.list_name == "From Config"
    assert settings.minimum_ranges == 5
    assert settings.timeout_seconds == 7.5
    assert settings.output_path == tmp_path / "relative.prom"
    assert settings.metric_prefix == "custom"

    cli = argparse.Namespace(
        config=config_file,
        meta_url=None,
        api_url="http://127.0.0.1:10",
        account_id=None,
        list_name="From CLI",
        timeout=12.0,
        output=tmp_path / "cli.prom",
    )
    resolved = cloudflare_webhook_ranges.resolve_settings(cli)
    assert resolved.meta_url == "http://127.0.0.1:9/meta"
    assert resolved.api_url == "http://127.0.0.1:10"
    assert resolved.list_name == "From CLI"
    assert resolved.timeout_seconds == 12.0
    assert resolved.output_path == tmp_path / "cli.prom"
    assert resolved.minimum_ranges == 5


def test_configuration_refuses_a_token() -> None:
    with pytest.raises(ValueError, match=TOKEN_VARIABLE):
        cloudflare_webhook_ranges.parse_settings({"cloudflare": {"api_token": "x"}})
    with pytest.raises(ValueError, match="TOML table"):
        cloudflare_webhook_ranges.parse_settings([])
    with pytest.raises(ValueError, match="minimum_ranges"):
        cloudflare_webhook_ranges.parse_settings({"github": {"minimum_ranges": 0}})
    with pytest.raises(ValueError, match="timeout_seconds"):
        cloudflare_webhook_ranges.parse_settings(
            {"cloudflare": {"timeout_seconds": -1}}
        )
    with pytest.raises(ValueError, match=r"\.prom"):
        cloudflare_webhook_ranges.parse_settings(
            {"metrics": {"output_path": "/tmp/x.txt"}}
        )


def test_configurator_writes_and_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "config.local.toml"
    assert configure.main(["--output", str(target), "--list-name", "Hooks"]) == 0
    content = target.read_text(encoding="utf-8")
    assert 'list_name = "Hooks"' in content
    assert "# CUSTOMIZE:" in content
    assert TOKEN_VARIABLE in content
    assert "token =" not in content
    assert configure.main(["--output", str(target)]) == 1
    assert (
        configure.main(
            ["--output", str(target), "--overwrite", "--list-name", "Hooks Two"]
        )
        == 0
    )
    assert configure.main(["--output", str(tmp_path / "bad.toml"), "--timeout", "0"])
    settings = cloudflare_webhook_ranges.load_settings(target)
    assert settings.list_name == "Hooks Two"


def test_cli_help() -> None:
    for script, expected in (
        ("cloudflare_webhook_ranges.py", TOKEN_VARIABLE),
        ("configure.py", "config.local.toml"),
    ):
        completed = subprocess.run(
            [sys.executable, str(MODULE_DIR / script), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert expected in completed.stdout
