#!/usr/bin/env python3
"""Collect Cloudflare Tunnel HA connection count and origin health for node_exporter."""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_METRICS_URL = "http://127.0.0.1:20241/metrics"
DEFAULT_SYSTEMD_UNIT = "cloudflared.service"
DEFAULT_TIMEOUT = 5.0
DEFAULT_OUTPUT_PATH = Path("/var/lib/prometheus/node-exporter/cloudflared-tunnel.prom")
DEFAULT_METRIC_PREFIX = "cloudflared_tunnel"


@dataclass(frozen=True)
class OriginCheck:
    name: str
    url: str


@dataclass(frozen=True)
class Settings:
    metrics_url: str = DEFAULT_METRICS_URL
    systemd_unit: str = DEFAULT_SYSTEMD_UNIT
    timeout_seconds: float = DEFAULT_TIMEOUT
    output_path: Path = DEFAULT_OUTPUT_PATH
    metric_prefix: str = DEFAULT_METRIC_PREFIX
    origins: tuple[OriginCheck, ...] = ()


def escape_label_value(value: str) -> str:
    """Escape label values according to Prometheus exposition format."""
    return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


def render_metrics(
    connection_count: int | None,
    origin_results: list[tuple[str, int]],
    timestamp: float | None = None,
    prefix: str = DEFAULT_METRIC_PREFIX,
) -> str:
    """Render metrics in Prometheus text exposition format."""
    ts = time.time() if timestamp is None else timestamp
    # A stopped connector reports 0; an unreachable metrics endpoint reports -1.
    conn_val = connection_count if connection_count is not None else -1

    conn_help = (
        f"# HELP {prefix}_ha_connections Active Cloudflare Tunnel HA connections "
        "(-1 for unknown, 0 for stopped)."
    )
    lines = [
        conn_help,
        f"# TYPE {prefix}_ha_connections gauge",
        f"{prefix}_ha_connections {conn_val}",
    ]

    if origin_results:
        origin_help = (
            f"# HELP {prefix}_origin_up Local origin HTTP probe status "
            "(1 for up, 0 for down)."
        )
        lines.append(origin_help)
        lines.append(f"# TYPE {prefix}_origin_up gauge")
        for name, status in origin_results:
            escaped_name = escape_label_value(name)
            lines.append(f'{prefix}_origin_up{{service="{escaped_name}"}} {status}')

    ts_help = (
        f"# HELP {prefix}_timestamp_seconds Unix timestamp of the last "
        "health check collection."
    )
    lines.extend(
        [
            ts_help,
            f"# TYPE {prefix}_timestamp_seconds gauge",
            f"{prefix}_timestamp_seconds {ts:.3f}",
        ]
    )
    return "\n".join(lines) + "\n"


def query_systemd_status(unit: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Query systemctl is-active for a unit name. Returns output string or empty."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def connections(
    metrics_url: str = DEFAULT_METRICS_URL,
    systemd_unit: str = DEFAULT_SYSTEMD_UNIT,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    systemctl_fn: Callable[[str], str] | None = None,
) -> int | None:
    """Distinguish a stopped connector from an unreachable endpoint."""
    status = (
        systemctl_fn(systemd_unit)
        if systemctl_fn is not None
        else query_systemd_status(systemd_unit, timeout=timeout)
    )
    if status in ("inactive", "failed"):
        return 0

    try:
        req = urllib.request.Request(
            metrics_url,
            headers={"User-Agent": "tunnel-health/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(2_000_000).decode("utf-8", errors="replace")
        match = re.search(
            r"^cloudflared_tunnel_ha_connections ([0-9.eE+\-]+)$", data, re.M
        )
        if not match:
            return None
        value = float(match[1])
        return int(value) if math.isfinite(value) and value >= 0 else None
    except (OSError, ValueError):
        return None


def probe_origin(url: str, timeout: float = DEFAULT_TIMEOUT) -> int:
    """Probe an origin HTTP/HTTPS endpoint (status 200..399 is up)."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "tunnel-health/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(200 <= response.status < 400)
    except (OSError, ValueError):
        return 0


def validate_output_path(path: Path) -> None:
    """Ensure the metrics file path ends with the .prom extension."""
    if path.suffix != ".prom":
        raise ValueError(f"output path must end in .prom: {path}")


def write_textfile(path: Path, content: str) -> None:
    """Atomically write content to path using a temporary file in the same directory."""
    validate_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with open(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def parse_settings(
    payload: dict[str, Any], base_directory: Path | None = None
) -> Settings:
    """Parse settings from a TOML document dictionary."""
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")

    tunnel = payload.get("tunnel", {})
    if not isinstance(tunnel, dict):
        raise ValueError("[tunnel] must be a TOML table")

    metrics = payload.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("[metrics] must be a TOML table")

    raw_origins = payload.get("origins", [])
    if not isinstance(raw_origins, list):
        raise ValueError("[[origins]] must be an array of tables")

    metrics_url = tunnel.get("metrics_url", DEFAULT_METRICS_URL)
    if not isinstance(metrics_url, str) or not metrics_url:
        raise ValueError("tunnel.metrics_url must be a non-empty string")

    systemd_unit = tunnel.get("systemd_unit", DEFAULT_SYSTEMD_UNIT)
    if not isinstance(systemd_unit, str) or not systemd_unit:
        raise ValueError("tunnel.systemd_unit must be a non-empty string")

    timeout_val = tunnel.get("timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout_val, (int, float)) or timeout_val <= 0:
        raise ValueError("tunnel.timeout_seconds must be a positive number")
    timeout_seconds = float(timeout_val)

    raw_output = metrics.get("output_path", str(DEFAULT_OUTPUT_PATH))
    if not isinstance(raw_output, str) or not raw_output:
        raise ValueError("metrics.output_path must be a non-empty string")
    output_p = Path(raw_output).expanduser()
    if not output_p.is_absolute() and base_directory is not None:
        output_p = base_directory / output_p
    validate_output_path(output_p)

    metric_prefix = metrics.get("metric_prefix", DEFAULT_METRIC_PREFIX)
    if not isinstance(metric_prefix, str) or not metric_prefix:
        raise ValueError("metrics.metric_prefix must be a non-empty string")

    origins: list[OriginCheck] = []
    for idx, origin in enumerate(raw_origins):
        if not isinstance(origin, dict):
            raise ValueError(f"origins[{idx}] must be a table")
        name = origin.get("name")
        url = origin.get("url")
        if not isinstance(name, str) or not name:
            raise ValueError(f"origins[{idx}].name must be a non-empty string")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f"origins[{idx}].url must start with http:// or https://")
        origins.append(OriginCheck(name=name, url=url))

    return Settings(
        metrics_url=metrics_url,
        systemd_unit=systemd_unit,
        timeout_seconds=timeout_seconds,
        output_path=output_p,
        metric_prefix=metric_prefix,
        origins=tuple(origins),
    )


def load_settings(path: Path | None) -> Settings:
    """Load settings from an explicit config path or discover local config."""
    if path is None:
        default_local = Path(__file__).resolve().with_name("config.local.toml")
        if default_local.is_file():
            with default_local.open("rb") as handle:
                return parse_settings(tomllib.load(handle), default_local.parent)
        return Settings()
    with path.open("rb") as handle:
        return parse_settings(tomllib.load(handle), path.resolve().parent)


def parse_origin_cli(origin_str: str) -> OriginCheck:
    """Parse name=url string from CLI argument."""
    if "=" not in origin_str:
        raise ValueError(
            f"invalid origin format '{origin_str}'; expected name=http://url"
        )
    name, url = origin_str.split("=", 1)
    name = name.strip()
    url = url.strip()
    if not name:
        raise ValueError(f"invalid origin name in '{origin_str}'")
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"invalid origin URL in '{origin_str}'; must start with http:// or https://"
        )
    return OriginCheck(name=name, url=url)


def resolve_settings(args: argparse.Namespace) -> Settings:
    """Resolve settings following CLI > config file > default precedence."""
    base_settings = load_settings(args.config)

    metrics_url = (
        args.metrics_url if args.metrics_url is not None else base_settings.metrics_url
    )
    systemd_unit = (
        args.systemd_unit
        if args.systemd_unit is not None
        else base_settings.systemd_unit
    )
    timeout = (
        args.timeout if args.timeout is not None else base_settings.timeout_seconds
    )
    if timeout <= 0:
        raise ValueError("--timeout must be a positive number")

    if args.output is not None:
        output_path = args.output.expanduser()
        validate_output_path(output_path)
    else:
        output_path = base_settings.output_path

    if args.origins is not None:
        origins = tuple(parse_origin_cli(o) for o in args.origins)
    else:
        origins = base_settings.origins

    return Settings(
        metrics_url=metrics_url,
        systemd_unit=systemd_unit,
        timeout_seconds=timeout,
        output_path=output_path,
        metric_prefix=base_settings.metric_prefix,
        origins=origins,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Collect Cloudflare Tunnel HA connection count and origin health "
            "for node_exporter."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to local TOML configuration file",
    )
    parser.add_argument(
        "--metrics-url",
        help="override cloudflared metrics endpoint URL",
    )
    parser.add_argument(
        "--systemd-unit",
        help="override cloudflared systemd unit name",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="override request and check timeout in seconds",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="override output .prom file path",
    )
    parser.add_argument(
        "--origin",
        action="append",
        dest="origins",
        metavar="NAME=URL",
        help="add or override origin health check (format: name=http://host:port/path)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="preview metrics output on stdout without writing to file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run health collection and write textfile or output to stdout."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = resolve_settings(args)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"configuration-error: {exc}", file=sys.stderr)
        return 1

    try:
        conn_count = connections(
            metrics_url=settings.metrics_url,
            systemd_unit=settings.systemd_unit,
            timeout=settings.timeout_seconds,
        )
        origin_results = [
            (
                origin.name,
                probe_origin(origin.url, timeout=settings.timeout_seconds),
            )
            for origin in settings.origins
        ]
        body = render_metrics(
            connection_count=conn_count,
            origin_results=origin_results,
            prefix=settings.metric_prefix,
        )

        if args.stdout:
            sys.stdout.write(body)
            return 0

        write_textfile(settings.output_path, body)
        return 0
    except (OSError, ValueError) as exc:
        print(f"execution-error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
