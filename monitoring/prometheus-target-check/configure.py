#!/usr/bin/env python3
"""Create a local Prometheus target-check configuration without changing Prometheus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from check_targets import (
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    HttpSettings,
    fetch_prometheus_payload,
    parse_targets,
    validate_http_settings,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.json"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="targets API URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--ca-file", default="")
    parser.add_argument("--bearer-token-env", default="")
    parser.add_argument("--basic-username", default="")
    parser.add_argument("--basic-password-env", default="")
    parser.add_argument(
        "--discover-targets",
        action="store_true",
        help="consent to one remote request and seed expectations from its response",
    )
    return parser


def configuration_payload(
    settings: HttpSettings, targets: list[dict[str, str]]
) -> dict[str, object]:
    return {
        "_comment": "CUSTOMIZE: Review every generated value before using it.",
        "prometheus": {
            "_comment_url": "CUSTOMIZE: Confirm the complete targets API URL.",
            "url": settings.url,
            "_comment_timeout": "CUSTOMIZE: Confirm the request timeout in seconds.",
            "timeout_seconds": settings.timeout,
            "_comment_ca": "CUSTOMIZE: Confirm the private CA path or leave it empty.",
            "ca_file": str(settings.ca_file or ""),
            "_comment_bearer": (
                "CUSTOMIZE: Store only the bearer-token environment-variable name."
            ),
            "bearer_token_env": settings.bearer_token_env,
            "_comment_basic_user": (
                "CUSTOMIZE: Confirm the basic-auth username or leave it empty."
            ),
            "basic_username": settings.basic_username,
            "_comment_basic_password": (
                "CUSTOMIZE: Store only the password environment-variable name."
            ),
            "basic_password_env": settings.basic_password_env,
        },
        "expectations": {
            "_comment_targets": "CUSTOMIZE: Confirm the complete expected target set.",
            "targets": targets,
            "_comment_forbidden": "CUSTOMIZE: Add stale values that must not appear.",
            "forbidden_substrings": [],
            "_comment_health": "CUSTOMIZE: Confirm the accepted health value.",
            "required_health": "up",
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1

    settings = HttpSettings(
        url=args.url,
        timeout=args.timeout,
        ca_file=Path(args.ca_file).expanduser() if args.ca_file else None,
        bearer_token_env=args.bearer_token_env,
        basic_username=args.basic_username,
        basic_password_env=args.basic_password_env,
    )
    try:
        validate_http_settings(settings)
        if args.discover_targets:
            discovered = parse_targets(fetch_prometheus_payload(settings))
            targets = [
                {"job": target.job, "scrape_url": target.scrape_url}
                for target in sorted(discovered)
            ]
            if not targets:
                raise ValueError("remote discovery returned no active targets")
        else:
            targets = [
                {
                    "job": "CUSTOMIZE-job-name",
                    "scrape_url": "http://192.0.2.10:9100/metrics",
                }
            ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation closes the race between the early existence check and
        # this write. A concurrently created local config is never replaced.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(configuration_payload(settings, targets), indent=2) + "\n"
            )
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    print(f"configuration-written: {args.output}")
    if not args.discover_targets:
        print("next-step: replace the CUSTOMIZE target before running the check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
