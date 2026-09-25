#!/usr/bin/env python3
"""Create a local dashboard-check configuration without contacting any server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from grafana_dashboards import (
    DEFAULT_TIMEOUT,
    DEFAULT_URL,
    VARIABLE_NAME,
    Dashboard,
    HttpSettings,
    load_dashboards,
    validate_http_settings,
)

PLACEHOLDER_DASHBOARDS = ["/home/example/grafana/dashboards"]
PLACEHOLDER_VARIABLES = {"instance": ".*", "job": ".*"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.json"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument(
        "--dashboard",
        action="append",
        type=Path,
        metavar="PATH",
        help=(
            "dashboard file or directory to record; its template variables seed "
            "queries.variables. Repeat for more paths. Only local files are read."
        ),
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help="Prometheus base URL, without /api/v1"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-query timeout in seconds",
    )
    parser.add_argument("--ca-file", default="", help="private CA PEM path for HTTPS")
    parser.add_argument(
        "--bearer-token-env",
        default="",
        help="name of the environment variable that holds a bearer token",
    )
    parser.add_argument("--basic-username", default="", help="basic-auth username")
    parser.add_argument(
        "--basic-password-env",
        default="",
        help="name of the environment variable that holds the basic-auth password",
    )
    parser.add_argument(
        "--allow-empty-file",
        default="",
        help="JSON list of panel titles that may return no data",
    )
    return parser


def _absolute(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve()) if str(value) else ""


def _seed_value(variable: dict[str, Any]) -> str:
    # A regex matcher given .* covers every series the variable could select.
    # Interval and constant variables need their literal value instead.
    if variable.get("type") in {"interval", "constant"}:
        current = variable.get("current")
        value = current.get("value") if isinstance(current, dict) else None
        if isinstance(value, str) and value:
            return value
    return ".*"


def discovered_variables(dashboards: list[Dashboard]) -> dict[str, str]:
    found: dict[str, str] = {}
    for dashboard in dashboards:
        templating = dashboard.data.get("templating")
        entries = templating.get("list") if isinstance(templating, dict) else None
        for variable in entries if isinstance(entries, list) else []:
            if not isinstance(variable, dict):
                continue
            name = variable.get("name")
            if (
                not isinstance(name, str)
                or not VARIABLE_NAME.fullmatch(name)
                or variable.get("type") in {"datasource", "adhoc"}
            ):
                continue
            found.setdefault(name, _seed_value(variable))
    return dict(sorted(found.items()))


def configuration_payload(
    settings: HttpSettings,
    dashboards: list[str],
    variables: dict[str, str],
    allow_empty_file: str,
) -> dict[str, object]:
    return {
        "_comment": "CUSTOMIZE: Review every generated value before using it.",
        "dashboards": {
            "_comment_paths": (
                "CUSTOMIZE: Confirm every dashboard file or directory to check."
            ),
            "paths": dashboards,
            "_comment_datasource": (
                "CUSTOMIZE: Set false only when dashboards mix datasources on purpose."
            ),
            "require_single_datasource": True,
        },
        "prometheus": {
            "_comment_url": "CUSTOMIZE: Confirm the Prometheus base URL.",
            "url": settings.url,
            "_comment_timeout": "CUSTOMIZE: Confirm the per-query timeout in seconds.",
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
        "queries": {
            "_comment_variables": (
                "CUSTOMIZE: Confirm the value substituted for each template variable."
            ),
            "variables": variables,
            "_comment_allow_empty": (
                "CUSTOMIZE: List panel titles that are correct when they are empty."
            ),
            "allow_empty_titles": [],
            "_comment_allow_empty_file": (
                "CUSTOMIZE: Confirm the allow-empty title file or leave it empty."
            ),
            "allow_empty_file": allow_empty_file,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1

    ca_file = _absolute(args.ca_file)
    settings = HttpSettings(
        url=args.url,
        timeout=args.timeout,
        ca_file=Path(ca_file) if ca_file else None,
        bearer_token_env=args.bearer_token_env,
        basic_username=args.basic_username,
        basic_password_env=args.basic_password_env,
    )
    try:
        validate_http_settings(settings)
        if args.dashboard:
            dashboards, errors = load_dashboards(args.dashboard)
            if errors:
                raise ValueError("; ".join(errors))
            paths = [_absolute(path) for path in args.dashboard]
            variables = discovered_variables(dashboards)
        else:
            paths = PLACEHOLDER_DASHBOARDS
            variables = dict(PLACEHOLDER_VARIABLES)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = configuration_payload(
        settings, paths, variables, _absolute(args.allow_empty_file)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation closes the race between the early existence check and
        # this write. A concurrently created local config is never replaced.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    print(f"configuration-written: {args.output}")
    if args.dashboard:
        print(f"variables-seeded: {len(variables)}")
    else:
        print("next-step: replace the CUSTOMIZE dashboard path and variables")
    print("next-step: review every variable value and add allow-empty titles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
