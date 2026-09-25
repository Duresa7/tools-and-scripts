#!/usr/bin/env python3
"""Create local configuration for Cloudflare Tunnel health checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tunnel_health import (
    DEFAULT_METRIC_PREFIX,
    DEFAULT_METRICS_URL,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_SYSTEMD_UNIT,
    DEFAULT_TIMEOUT,
    validate_output_path,
)

DEFAULT_EXAMPLE_ORIGINS = [
    ("web", "http://127.0.0.1:8080/health"),
    ("api", "http://192.0.2.10:8000/health"),
]


def format_toml_string(value: str) -> str:
    """Format a string safely for TOML representation."""
    escaped = value.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


def build_parser() -> argparse.ArgumentParser:
    """Build parser for configuration generator."""
    parser = argparse.ArgumentParser(
        description="Generate config.local.toml for Cloudflare Tunnel health checks."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output config path; existing file is not replaced without --overwrite",
    )
    parser.add_argument(
        "--metrics-url",
        default=DEFAULT_METRICS_URL,
        help="cloudflared metrics endpoint URL",
    )
    parser.add_argument(
        "--systemd-unit",
        default=DEFAULT_SYSTEMD_UNIT,
        help="cloudflared systemd service unit name",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="request timeout in seconds",
    )
    parser.add_argument(
        "--output-path",
        default=str(DEFAULT_OUTPUT_PATH),
        help="destination .prom file path for Prometheus node_exporter",
    )
    parser.add_argument(
        "--metric-prefix",
        default=DEFAULT_METRIC_PREFIX,
        help="prefix for exported Prometheus metrics",
    )
    parser.add_argument(
        "--origin",
        action="append",
        dest="origins",
        metavar="NAME=URL",
        help="origin check in NAME=URL format; repeatable",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow overwriting an existing output file",
    )
    return parser


def parse_origin_arg(arg: str) -> tuple[str, str]:
    """Parse name=url origin CLI argument."""
    if "=" not in arg:
        raise ValueError(f"invalid origin '{arg}'; expected name=http://url")
    name, url = arg.split("=", 1)
    name = name.strip()
    url = url.strip()
    if not name:
        raise ValueError(f"invalid origin name in '{arg}'")
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"invalid origin URL in '{arg}'; must start with http:// or https://"
        )
    return name, url


def generate_toml(
    metrics_url: str,
    systemd_unit: str,
    timeout_seconds: float,
    output_path: str,
    metric_prefix: str,
    origins: list[tuple[str, str]],
) -> str:
    """Generate TOML content with CUSTOMIZE comments."""
    lines = [
        "[tunnel]",
        "# CUSTOMIZE: Set the cloudflared metrics endpoint URL.",
        f"metrics_url = {format_toml_string(metrics_url)}",
        "# CUSTOMIZE: Set the systemd unit name for the cloudflared connector service.",
        f"systemd_unit = {format_toml_string(systemd_unit)}",
        "# CUSTOMIZE: Set the timeout in seconds for probes and systemctl.",
        f"timeout_seconds = {timeout_seconds}",
        "",
        "[metrics]",
        "# CUSTOMIZE: Set destination .prom file path for node_exporter.",
        f"output_path = {format_toml_string(output_path)}",
        "# CUSTOMIZE: Set the Prometheus metric family prefix.",
        f"metric_prefix = {format_toml_string(metric_prefix)}",
        "",
        "# Origin HTTP health checks. Repeat [[origins]] for each backend service.",
    ]
    for name, url in origins:
        lines.extend(
            [
                "[[origins]]",
                "# CUSTOMIZE: Short service label for the origin metric.",
                f"name = {format_toml_string(name)}",
                "# CUSTOMIZE: Origin probe URL (status 200 to 399 is up).",
                f"url = {format_toml_string(url)}",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Generate local configuration file."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.output.exists() and not args.overwrite:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1

    try:
        validate_output_path(Path(args.output_path))
        if args.timeout <= 0:
            raise ValueError("--timeout must be a positive number")

        if args.origins:
            origins = [parse_origin_arg(item) for item in args.origins]
        else:
            origins = DEFAULT_EXAMPLE_ORIGINS

        content = generate_toml(
            metrics_url=args.metrics_url,
            systemd_unit=args.systemd_unit,
            timeout_seconds=args.timeout,
            output_path=args.output_path,
            metric_prefix=args.metric_prefix,
            origins=origins,
        )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation mode closes the race against concurrent processes
        # unless explicit overwrite is granted.
        mode = "w" if args.overwrite else "x"
        with args.output.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"configuration-written: {args.output}")
    print("next-step: review CUSTOMIZE markers in config.local.toml before running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
