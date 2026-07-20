from collections import Counter

import pytest

from prometheus.check_targets import (
    Expectations,
    Target,
    evaluate,
    parse_expectations,
    parse_targets,
)


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
