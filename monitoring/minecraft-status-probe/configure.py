#!/usr/bin/env python3
"""Create a local Minecraft status probe configuration without contacting any server."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from minecraft_status_probe import (
    DEFAULT_FORMAT,
    DEFAULT_PORT,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_TIMEOUT,
    parse_settings,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct the command line argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument(
        "--host",
        default="mc.example.net",
        help="server hostname or IP address (example: mc.example.net or 192.0.2.10)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="server TCP port (default: 25565)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="connection timeout in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        default=DEFAULT_PROTOCOL_VERSION,
        help="protocol version number (default: 767)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default=DEFAULT_FORMAT,
        help="output format: text or json (default: text)",
    )
    parser.add_argument(
        "--srv",
        action="store_true",
        help="resolve _minecraft._tcp SRV record instead of direct connection",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="measure round-trip ping/pong latency after handshake",
    )
    return parser


def render_configuration(
    host: str,
    port: int,
    timeout: float,
    protocol_version: int,
    out_format: str,
    srv: bool,
    ping: bool,
) -> str:
    """Render the TOML configuration text with CUSTOMIZE markers."""
    lines = (
        "# Minecraft Java status probe configuration",
        "",
        "[probe]",
        "# CUSTOMIZE: Set the Minecraft server hostname or IP address.",
        f"host = {json.dumps(host)}",
        "# CUSTOMIZE: Set the Minecraft server TCP port.",
        f"port = {port}",
        "# CUSTOMIZE: Set connection and status response timeout in seconds.",
        f"timeout_seconds = {timeout}",
        "# CUSTOMIZE: Set handshake protocol version (767 is 1.21/1.21.1).",
        f"protocol_version = {protocol_version}",
        '# CUSTOMIZE: Set the output format: "text" or "json".',
        f"format = {json.dumps(out_format)}",
        "# CUSTOMIZE: Set whether to resolve SRV records (_minecraft._tcp).",
        f"srv = {'true' if srv else 'false'}",
        "# CUSTOMIZE: Set whether to measure round-trip ping/pong latency.",
        f"ping = {'true' if ping else 'false'}",
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run the configurator entry point."""
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1

    try:
        contents = render_configuration(
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            protocol_version=args.protocol_version,
            out_format=args.format,
            srv=args.srv,
            ping=args.ping,
        )
        parse_settings(tomllib.loads(contents))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
