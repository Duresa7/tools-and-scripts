#!/usr/bin/env python3
"""Create a local TeamSpeak probe configuration without contacting any server."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from teamspeak_probe import (
    DEFAULT_LOCAL_HOST,
    DEFAULT_TIMEOUT,
    parse_settings,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument(
        "--local-host",
        default=DEFAULT_LOCAL_HOST,
        help="host or IPv4 address where the voice servers listen",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds to wait for each handshake and SRV lookup",
    )
    parser.add_argument(
        "--dns-server",
        default="",
        help="DNS server for SRV lookups; empty uses the system resolver",
    )
    parser.add_argument(
        "--metrics-output",
        default="/var/lib/node_exporter/textfile_collector/teamspeak.prom",
        help="node_exporter textfile path written by the metrics command",
    )
    parser.add_argument(
        "--server",
        action="append",
        nargs=3,
        metavar=("NAME", "ADDRESS", "LOCAL_PORT"),
        help="voice server label, public address, and local UDP port; repeatable",
    )
    return parser


def toml_string(value: str) -> str:
    return json.dumps(value)


def render_configuration(args: argparse.Namespace) -> str:
    servers = args.server or [["voice1", "voice1.example.com", "9987"]]
    lines = [
        "[probe]",
        "# CUSTOMIZE: Confirm the host or IPv4 address where the voice servers listen.",
        f"local_host = {toml_string(args.local_host)}",
        "# CUSTOMIZE: Confirm the handshake and SRV lookup timeout in seconds.",
        f"timeout_seconds = {args.timeout:g}",
        "# CUSTOMIZE: Set a DNS server for SRV lookups, or leave empty.",
        f"dns_server = {toml_string(args.dns_server)}",
        "",
        "[metrics]",
        "# CUSTOMIZE: Confirm the .prom file in node_exporter's textfile directory.",
        f"output_path = {toml_string(args.metrics_output)}",
    ]
    for name, address, local_port in servers:
        if not local_port.isdigit():
            raise ValueError(f"local port for {name!r} must be a number")
        lines.extend(
            (
                "",
                "[[servers]]",
                "# CUSTOMIZE: Confirm the server label.",
                f"name = {toml_string(name)}",
                "# CUSTOMIZE: Confirm the public address users type.",
                f"address = {toml_string(address)}",
                "# CUSTOMIZE: Confirm the local UDP voice port.",
                f"local_port = {int(local_port)}",
                "# CUSTOMIZE: Set the ServerQuery TCP port, or 0 to skip ServerQuery.",
                "query_port = 0",
                "# CUSTOMIZE: Set a ServerQuery login when query_port is set.",
                'query_username = ""',
                "# CUSTOMIZE: Store only the password environment-variable name.",
                'query_password_env = ""',
                "# CUSTOMIZE: Confirm the virtual server ID.",
                "virtual_server_id = 1",
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1
    try:
        contents = render_configuration(args)
        parse_settings(tomllib.loads(contents), args.output.parent)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation closes the race between the early existence check and
        # this write. A concurrently created local config is never replaced.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    print(f"configuration-written: {args.output}")
    if not args.server:
        print("next-step: replace the CUSTOMIZE server before running a check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
