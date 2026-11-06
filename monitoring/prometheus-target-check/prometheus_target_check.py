#!/usr/bin/env python3
"""Assert the exact active-target set returned by the Prometheus HTTP API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

DEFAULT_URL = "http://127.0.0.1:9090/api/v1/targets"
DEFAULT_TIMEOUT = 10.0


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


@dataclass(frozen=True)
class HttpSettings:
    """Connection settings that never contain credential values."""

    url: str = DEFAULT_URL
    timeout: float = DEFAULT_TIMEOUT
    ca_file: Path | None = None
    bearer_token_env: str = ""
    basic_username: str = ""
    basic_password_env: str = ""


@dataclass(frozen=True)
class ToolConfig:
    http: HttpSettings
    expectations: Expectations


class RejectRedirects(HTTPRedirectHandler):
    """Keep authorization headers from being forwarded to another endpoint."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
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
    """Parse the expectation section from a local configuration."""

    if not isinstance(payload, dict):
        raise ValueError("expectations must contain a JSON object")
    raw_targets = payload.get("targets")
    if raw_targets is None:
        raw_targets = payload.get("expected_targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("expectations.targets must be a non-empty list")

    identities: list[tuple[str, str]] = []
    for index, item in enumerate(raw_targets):
        if not isinstance(item, dict):
            raise ValueError(f"expectations.targets[{index}] must be an object")
        identities.append(
            (
                _required_string(item.get("job"), f"targets[{index}].job"),
                _required_string(
                    item.get("scrape_url"),
                    f"targets[{index}].scrape_url",
                ),
            )
        )

    duplicates = [
        identity for identity, count in Counter(identities).items() if count > 1
    ]
    if duplicates:
        raise ValueError(f"expectations.targets contains duplicates: {duplicates}")

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


def parse_http_settings(payload: Any) -> HttpSettings:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("prometheus must contain a JSON object")

    url = _optional_string(payload.get("url", DEFAULT_URL), "prometheus.url")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("prometheus.url must be an HTTP or HTTPS URL")

    timeout = payload.get("timeout_seconds", DEFAULT_TIMEOUT)
    if (
        not isinstance(timeout, int | float)
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        raise ValueError("prometheus.timeout_seconds must be greater than zero")

    ca_value = _optional_string(payload.get("ca_file"), "prometheus.ca_file")
    settings = HttpSettings(
        url=url,
        timeout=float(timeout),
        ca_file=Path(ca_value).expanduser() if ca_value else None,
        bearer_token_env=_optional_string(
            payload.get("bearer_token_env"), "prometheus.bearer_token_env"
        ),
        basic_username=_optional_string(
            payload.get("basic_username"), "prometheus.basic_username"
        ),
        basic_password_env=_optional_string(
            payload.get("basic_password_env"), "prometheus.basic_password_env"
        ),
    )
    validate_http_settings(settings)
    return settings


def validate_http_settings(settings: HttpSettings) -> None:
    parsed_url = urlparse(settings.url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Prometheus URL must use HTTP or HTTPS")
    if settings.timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if settings.ca_file and parsed_url.scheme != "https":
        raise ValueError("a custom CA file requires an HTTPS URL")
    if settings.bearer_token_env and (
        settings.basic_username or settings.basic_password_env
    ):
        raise ValueError("bearer and basic authentication are mutually exclusive")
    if bool(settings.basic_username) != bool(settings.basic_password_env):
        raise ValueError(
            "basic authentication requires a username and password env name"
        )


def parse_config(payload: Any) -> ToolConfig:
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a JSON object")
    return ToolConfig(
        http=parse_http_settings(payload.get("prometheus")),
        expectations=parse_expectations(payload.get("expectations")),
    )


def resolve_http_settings(
    config: HttpSettings, args: argparse.Namespace
) -> HttpSettings:
    if args.no_auth and any(
        value is not None
        for value in (
            args.bearer_token_env,
            args.basic_username,
            args.basic_password_env,
        )
    ):
        raise ValueError("--no-auth cannot be combined with authentication overrides")

    bearer_token_env = config.bearer_token_env
    basic_username = config.basic_username
    basic_password_env = config.basic_password_env
    if args.no_auth:
        bearer_token_env = ""
        basic_username = ""
        basic_password_env = ""
    elif args.bearer_token_env is not None:
        bearer_token_env = args.bearer_token_env
        basic_username = ""
        basic_password_env = ""
    elif args.basic_username is not None or args.basic_password_env is not None:
        bearer_token_env = ""
        basic_username = (
            args.basic_username
            if args.basic_username is not None
            else config.basic_username
        )
        basic_password_env = (
            args.basic_password_env
            if args.basic_password_env is not None
            else config.basic_password_env
        )

    settings = replace(
        config,
        url=args.url if args.url is not None else config.url,
        timeout=args.timeout if args.timeout is not None else config.timeout,
        ca_file=(
            None
            if args.no_ca_file
            else (
                Path(args.ca_file).expanduser()
                if args.ca_file is not None
                else config.ca_file
            )
        ),
        bearer_token_env=bearer_token_env,
        basic_username=basic_username,
        basic_password_env=basic_password_env,
    )
    validate_http_settings(settings)
    return settings


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


def authorization_header(settings: HttpSettings) -> str | None:
    if settings.bearer_token_env:
        token = os.environ.get(settings.bearer_token_env)
        if not token:
            raise ValueError(
                f"environment variable {settings.bearer_token_env!r} is empty or unset"
            )
        return f"Bearer {token}"
    if settings.basic_password_env:
        password = os.environ.get(settings.basic_password_env)
        if not password:
            variable_name = settings.basic_password_env
            raise ValueError(
                f"environment variable {variable_name!r} is empty or unset"
            )
        raw = f"{settings.basic_username}:{password}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"
    return None


def fetch_prometheus_payload(settings: HttpSettings) -> Any:
    headers = {"User-Agent": "tools-and-scripts/1"}
    auth = authorization_header(settings)
    if auth:
        headers["Authorization"] = auth
    request = Request(settings.url, headers=headers)

    ssl_context = None
    if urlparse(settings.url).scheme == "https":
        if settings.ca_file and not settings.ca_file.is_file():
            raise ValueError(f"custom CA file not found: {settings.ca_file}")
        ssl_context = ssl.create_default_context(
            cafile=str(settings.ca_file) if settings.ca_file else None
        )
    opener = build_opener(RejectRedirects(), HTTPSHandler(context=ssl_context))
    try:
        with opener.open(request, timeout=settings.timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(f"Prometheus returned HTTP {exc.code}") from None
    except URLError as exc:
        raise ValueError(f"Prometheus request failed: {exc.reason}") from None


def read_prometheus_payload(
    input_path: str | None, settings: HttpSettings, stdin: TextIO
) -> Any:
    if input_path is None:
        return fetch_prometheus_payload(settings)
    if input_path == "-":
        return json.load(stdin)
    return read_json_file(Path(input_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="local JSON configuration copied from config.example.json",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--input",
        metavar="PATH",
        help="read a saved Prometheus target response, or - for stdin",
    )
    source.add_argument(
        "--url",
        help="override the configured HTTP or HTTPS targets API URL",
    )
    parser.add_argument("--timeout", type=float, help="override the HTTP timeout")
    ca_group = parser.add_mutually_exclusive_group()
    ca_group.add_argument("--ca-file", help="override the custom CA path")
    ca_group.add_argument(
        "--no-ca-file", action="store_true", help="use the system trust store"
    )
    parser.add_argument(
        "--bearer-token-env",
        help="override the name of the bearer-token environment variable",
    )
    parser.add_argument(
        "--basic-username", help="override the configured basic-auth username"
    )
    parser.add_argument(
        "--basic-password-env",
        help="override the name of the basic-auth password environment variable",
    )
    parser.add_argument(
        "--no-auth", action="store_true", help="disable configured authentication"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = parse_config(read_json_file(args.config))
        settings = resolve_http_settings(config.http, args)
        payload = read_prometheus_payload(args.input, settings, sys.stdin)
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

    errors = evaluate(targets, config.expectations)
    if errors:
        for error in errors:
            print(f"assertion-failed: {error}", file=sys.stderr)
        return 2

    print(f"assertion-passed: {len(targets)} expected targets are healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
