#!/usr/bin/env python3
"""Assert the exact active-target set returned by the Prometheus HTTP API."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.request import Request, urlopen


@dataclass(frozen=True, order=True)
class Target:
    """Fields used to identify and assess one active Prometheus target."""

    job: str
    scrape_url: str
    health: str
    last_error: str


@dataclass(frozen=True)
class Expectations:
    """Expected target identities and health policy."""

    targets: Counter[tuple[str, str]]
    forbidden_substrings: tuple[str, ...]
    required_health: str


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def parse_targets(payload: Any) -> list[Target]:
    """Parse a Prometheus /api/v1/targets response."""

    if not isinstance(payload, dict):
        raise ValueError("Prometheus response must be a JSON object")
    if payload.get("status") != "success":
        raise ValueError("Prometheus response status is not 'success'")

    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("activeTargets"), list):
        raise ValueError("Prometheus response has no data.activeTargets list")

    targets: list[Target] = []
    for index, item in enumerate(data["activeTargets"]):
        if not isinstance(item, dict):
            raise ValueError(f"activeTargets[{index}] must be an object")
        labels = item.get("labels")
        if not isinstance(labels, dict):
            raise ValueError(f"activeTargets[{index}].labels must be an object")
        targets.append(
            Target(
                job=_required_string(labels.get("job"), f"activeTargets[{index}].job"),
                scrape_url=_required_string(
                    item.get("scrapeUrl"), f"activeTargets[{index}].scrapeUrl"
                ),
                health=_required_string(
                    item.get("health"), f"activeTargets[{index}].health"
                ),
                last_error=str(item.get("lastError") or ""),
            )
        )
    return targets


def parse_expectations(payload: Any) -> Expectations:
    """Parse the target expectation file."""

    if not isinstance(payload, dict):
        raise ValueError("expectation file must contain a JSON object")
    raw_targets = payload.get("expected_targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("expected_targets must be a non-empty list")

    identities: list[tuple[str, str]] = []
    for index, item in enumerate(raw_targets):
        if not isinstance(item, dict):
            raise ValueError(f"expected_targets[{index}] must be an object")
        identities.append(
            (
                _required_string(item.get("job"), f"expected_targets[{index}].job"),
                _required_string(
                    item.get("scrape_url"),
                    f"expected_targets[{index}].scrape_url",
                ),
            )
        )

    duplicates = [
        identity for identity, count in Counter(identities).items() if count > 1
    ]
    if duplicates:
        raise ValueError(f"expected_targets contains duplicates: {duplicates}")

    raw_forbidden = payload.get("forbidden_substrings", [])
    if not isinstance(raw_forbidden, list) or not all(
        isinstance(value, str) and value for value in raw_forbidden
    ):
        raise ValueError("forbidden_substrings must be a list of non-empty strings")

    required_health = _required_string(
        payload.get("required_health", "up"), "required_health"
    )
    return Expectations(
        targets=Counter(identities),
        forbidden_substrings=tuple(raw_forbidden),
        required_health=required_health,
    )


def evaluate(targets: list[Target], expected: Expectations) -> list[str]:
    """Return every failed assertion without stopping at the first mismatch."""

    errors: list[str] = []
    actual = Counter((target.job, target.scrape_url) for target in targets)
    missing = expected.targets - actual
    unexpected = actual - expected.targets
    if missing:
        errors.append(f"missing targets: {sorted(missing.elements())}")
    if unexpected:
        errors.append(f"unexpected targets: {sorted(unexpected.elements())}")

    forbidden_hits = sorted(
        {
            (value, target.scrape_url)
            for value in expected.forbidden_substrings
            for target in targets
            if value in target.scrape_url
        }
    )
    if forbidden_hits:
        errors.append(f"forbidden scrape URL values: {forbidden_hits}")

    unhealthy = sorted(
        (target.job, target.scrape_url, target.health, target.last_error or "none")
        for target in targets
        if target.health != expected.required_health
    )
    if unhealthy:
        errors.append(
            f"targets not in required health state {expected.required_health!r}: "
            f"{unhealthy}"
        )
    return errors


def read_json_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_prometheus_payload(
    input_path: str, url: str | None, timeout: float, stdin: TextIO
) -> Any:
    if url:
        request = Request(url, headers={"User-Agent": "tools-and-scripts/1"})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    if input_path == "-":
        return json.load(stdin)
    return read_json_file(Path(input_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--input",
        default="-",
        metavar="PATH",
        help="Prometheus target JSON file, or - for stdin (default: -)",
    )
    source.add_argument(
        "--url",
        help="Prometheus targets API URL, such as http://127.0.0.1:9090/api/v1/targets",
    )
    parser.add_argument(
        "--expect", required=True, type=Path, help="Expectation JSON file"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="HTTP timeout in seconds"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    try:
        payload = read_prometheus_payload(args.input, args.url, args.timeout, sys.stdin)
        expected = parse_expectations(read_json_file(args.expect))
        targets = parse_targets(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 1

    for target in sorted(targets):
        print(
            "|".join(
                (
                    target.job,
                    target.health,
                    target.scrape_url,
                    target.last_error or "none",
                )
            )
        )

    errors = evaluate(targets, expected)
    if errors:
        for error in errors:
            print(f"assertion-failed: {error}", file=sys.stderr)
        return 2

    print(f"assertion-passed: {len(targets)} expected targets are healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
