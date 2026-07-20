import argparse
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "check_targets.py"
SPEC = importlib.util.spec_from_file_location("prometheus_target_check", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

Expectations = MODULE.Expectations
HttpSettings = MODULE.HttpSettings
Target = MODULE.Target
authorization_header = MODULE.authorization_header
evaluate = MODULE.evaluate
parse_config = MODULE.parse_config
parse_expectations = MODULE.parse_expectations
parse_targets = MODULE.parse_targets
resolve_http_settings = MODULE.resolve_http_settings


def test_matching_target_set_passes() -> None:
    targets = [
        Target("node", "http://192.0.2.10:9100/metrics", "up", ""),
        Target("node", "http://192.0.2.11:9100/metrics", "up", ""),
    ]
    expected = Expectations(
        targets=Counter(
            {
                ("node", "http://192.0.2.10:9100/metrics"): 1,
                ("node", "http://192.0.2.11:9100/metrics"): 1,
            }
        ),
        forbidden_substrings=("192.0.2.99",),
        required_health="up",
    )

    assert evaluate(targets, expected) == []


def test_duplicate_actual_target_is_reported() -> None:
    target = Target("node", "http://192.0.2.10:9100/metrics", "up", "")
    expected = Expectations(
        targets=Counter({("node", target.scrape_url): 1}),
        forbidden_substrings=(),
        required_health="up",
    )

    errors = evaluate([target, target], expected)

    assert errors == [f"unexpected targets: [('node', '{target.scrape_url}')]"]


def test_reports_forbidden_and_unhealthy_target() -> None:
    target = Target(
        "node",
        "http://192.0.2.99:9100/metrics",
        "down",
        "connection refused",
    )
    expected = Expectations(
        targets=Counter({("node", target.scrape_url): 1}),
        forbidden_substrings=("192.0.2.99",),
        required_health="up",
    )

    errors = evaluate([target], expected)

    assert "forbidden scrape URL values" in errors[0]
    assert "connection refused" in errors[1]


def test_parse_prometheus_response() -> None:
    payload = {
        "status": "success",
        "data": {
            "activeTargets": [
                {
                    "labels": {"job": "node"},
                    "scrapeUrl": "http://192.0.2.10:9100/metrics",
                    "health": "up",
                    "lastError": "",
                }
            ]
        },
    }

    assert parse_targets(payload) == [
        Target("node", "http://192.0.2.10:9100/metrics", "up", "")
    ]


def test_expectation_file_rejects_duplicates() -> None:
    payload = {
        "expected_targets": [
            {"job": "node", "scrape_url": "http://192.0.2.10:9100/metrics"},
            {"job": "node", "scrape_url": "http://192.0.2.10:9100/metrics"},
        ]
    }

    with pytest.raises(ValueError, match="duplicates"):
        parse_expectations(payload)


def valid_config() -> dict[str, object]:
    return {
        "prometheus": {
            "url": "https://prometheus.example.net/api/v1/targets",
            "timeout_seconds": 15,
            "ca_file": "",
            "bearer_token_env": "PROMETHEUS_TOKEN",
            "basic_username": "",
            "basic_password_env": "",
        },
        "expectations": {
            "targets": [
                {
                    "job": "node",
                    "scrape_url": "http://192.0.2.10:9100/metrics",
                }
            ],
            "forbidden_substrings": ["192.0.2.99"],
            "required_health": "up",
        },
    }


def test_cli_http_values_override_local_config() -> None:
    config = parse_config(valid_config())
    args = argparse.Namespace(
        url="http://127.0.0.1:9090/api/v1/targets",
        timeout=3.0,
        ca_file=None,
        no_ca_file=False,
        bearer_token_env="LOCAL_TOKEN",
        basic_username=None,
        basic_password_env=None,
        no_auth=False,
    )

    resolved = resolve_http_settings(config.http, args)

    assert resolved.url == "http://127.0.0.1:9090/api/v1/targets"
    assert resolved.timeout == 3.0
    assert resolved.bearer_token_env == "LOCAL_TOKEN"


def test_bearer_token_is_read_from_the_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMETHEUS_TEST_TOKEN", "secret-value")
    settings = HttpSettings(bearer_token_env="PROMETHEUS_TEST_TOKEN")

    header = authorization_header(settings)

    assert header == "Bearer secret-value"


def test_authentication_modes_cannot_be_combined() -> None:
    payload = valid_config()
    prometheus = payload["prometheus"]
    assert isinstance(prometheus, dict)
    prometheus["basic_username"] = "metrics-reader"
    prometheus["basic_password_env"] = "PROMETHEUS_PASSWORD"

    with pytest.raises(ValueError, match="mutually exclusive"):
        parse_config(payload)


def test_configurator_refuses_to_replace_local_configuration(tmp_path: Path) -> None:
    output = tmp_path / "config.local.json"
    command = [
        sys.executable,
        str(MODULE_PATH.with_name("configure.py")),
        "--output",
        str(output),
    ]

    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert parse_config(payload).http.url == MODULE.DEFAULT_URL
    serialized = output.read_text(encoding="utf-8")
    assert "secret-value" not in serialized
