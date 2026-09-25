#!/usr/bin/env python3
"""Create local configuration for Discord alert relay service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alert_relay import (
    DEFAULT_API_BASE_URL,
    DEFAULT_BOT_TOKEN_ENV,
    DEFAULT_CONDENSE_THRESHOLD,
    DEFAULT_DEFAULT_CLASS,
    DEFAULT_LISTEN_ADDRESS,
    DEFAULT_LISTEN_PORT,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MODE,
    DEFAULT_READINESS_INTERVAL_SECONDS,
    DEFAULT_RETRY_AFTER_CAP_SECONDS,
    DEFAULT_SECRET_ENV,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_WEBHOOK_URL_ENV,
)

DEFAULT_EXAMPLE_SPLUNK_SOURCES = [
    "127.0.0.1",
    "192.0.2.10",
    "198.51.100.0/24",
]


def format_toml_string(value: str) -> str:
    """Format string safely for TOML representation."""
    escaped = value.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for configuration generator."""
    parser = argparse.ArgumentParser(
        description="Generate config.local.toml for Discord alert relay service."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output config path; existing file is not replaced without --overwrite",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow overwriting an existing output file",
    )
    parser.add_argument(
        "--listen-address",
        default=DEFAULT_LISTEN_ADDRESS,
        help="server listen address",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help="server listen port",
    )
    parser.add_argument(
        "--secret-env",
        default=DEFAULT_SECRET_ENV,
        help="environment variable holding Grafana bearer secret",
    )
    parser.add_argument(
        "--splunk-source",
        action="append",
        dest="allowed_splunk_sources",
        help="allowed IP or CIDR for Splunk webhooks; repeatable",
    )
    parser.add_argument(
        "--mode",
        choices=["bot", "webhook"],
        default=DEFAULT_MODE,
        help="Discord delivery mode",
    )
    parser.add_argument(
        "--bot-token-env",
        default=DEFAULT_BOT_TOKEN_ENV,
        help="environment variable holding Discord bot token",
    )
    parser.add_argument(
        "--channel-id",
        default="123456789012345678",
        help="Discord channel ID for bot messages",
    )
    parser.add_argument(
        "--webhook-url-env",
        default=DEFAULT_WEBHOOK_URL_ENV,
        help="environment variable holding Discord webhook URL",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="Discord API base URL",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="request timeout in seconds",
    )
    parser.add_argument(
        "--retry-after-cap",
        type=float,
        default=DEFAULT_RETRY_AFTER_CAP_SECONDS,
        help="maximum wait time in seconds for HTTP 429 rate limit retry",
    )
    parser.add_argument(
        "--readiness-interval",
        type=float,
        default=DEFAULT_READINESS_INTERVAL_SECONDS,
        help="interval in seconds between periodic readiness checks",
    )
    parser.add_argument(
        "--default-class",
        help="default alert class name",
        default=DEFAULT_DEFAULT_CLASS,
    )
    parser.add_argument(
        "--condense-threshold",
        type=int,
        default=DEFAULT_CONDENSE_THRESHOLD,
        help="condensed embed threshold",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=DEFAULT_MAX_REQUEST_BYTES,
        help="maximum request size in bytes",
    )
    return parser


def generate_toml(
    listen_address: str,
    listen_port: int,
    max_request_bytes: int,
    secret_env: str,
    allowed_splunk_sources: list[str],
    mode: str,
    bot_token_env: str,
    channel_id: str,
    webhook_url_env: str,
    api_base_url: str,
    timeout_seconds: float,
    retry_after_cap_seconds: float,
    readiness_interval_seconds: float,
    default_class: str,
    condense_threshold: int,
) -> str:
    """Generate TOML content with CUSTOMIZE comments."""
    sources_formatted = "\n".join(
        f"    {format_toml_string(s)}," for s in allowed_splunk_sources
    )
    lines = [
        "# Configuration for Discord alert relay service",
        "",
        "[server]",
        "# CUSTOMIZE: Listen IP address for the webhook server.",
        f"listen_address = {format_toml_string(listen_address)}",
        "# CUSTOMIZE: Listen port for the webhook server.",
        f"listen_port = {listen_port}",
        "# CUSTOMIZE: Maximum allowed request body size in bytes.",
        f"max_request_bytes = {max_request_bytes}",
        "# CUSTOMIZE: Environment variable for Grafana bearer secret.",
        f"secret_env = {format_toml_string(secret_env)}",
        "# CUSTOMIZE: Allowed source IP or CIDR list for POST /splunk.",
        "allowed_splunk_sources = [",
        sources_formatted,
        "]",
        "",
        "[discord]",
        '# CUSTOMIZE: Delivery mode: "bot" or "webhook".',
        f"mode = {format_toml_string(mode)}",
        "# CUSTOMIZE: Env var for Discord bot token (mode = 'bot').",
        f"bot_token_env = {format_toml_string(bot_token_env)}",
        "# CUSTOMIZE: Target Discord channel ID (mode = 'bot').",
        f"channel_id = {format_toml_string(channel_id)}",
        "# CUSTOMIZE: Env var for Discord webhook URL (mode = 'webhook').",
        f"webhook_url_env = {format_toml_string(webhook_url_env)}",
        "# CUSTOMIZE: Base URL for Discord REST API.",
        f"api_base_url = {format_toml_string(api_base_url)}",
        "# CUSTOMIZE: Request timeout in seconds for Discord API calls.",
        f"timeout_seconds = {timeout_seconds}",
        "# CUSTOMIZE: Maximum wait time in seconds for HTTP 429 rate limit retry.",
        f"retry_after_cap_seconds = {retry_after_cap_seconds}",
        "# CUSTOMIZE: Interval in seconds between periodic Discord readiness checks.",
        f"readiness_interval_seconds = {readiness_interval_seconds}",
        "",
        "[alerts]",
        "# CUSTOMIZE: Default alert class if not specified.",
        f"default_class = {format_toml_string(default_class)}",
        "# CUSTOMIZE: Alert count to collapse into condensed embed.",
        f"condense_threshold = {condense_threshold}",
        "# CUSTOMIZE: Color for resolved alerts as hex integer.",
        "resolved_color = 0x2F9E44",
        "",
        "[classes.infrastructure]",
        "# CUSTOMIZE: Prefix prepended to the title of infrastructure alerts.",
        'prefix = ""',
        "",
        "[classes.security]",
        "# CUSTOMIZE: Prefix prepended to the title of security alerts.",
        'prefix = "SECURITY "',
        "# CUSTOMIZE: Color for security alerts as hex integer.",
        "color = 0x7048E8",
        "",
        "[classes.updates]",
        "# CUSTOMIZE: Prefix prepended to the title of update alerts.",
        'prefix = "UPDATES "',
        "# CUSTOMIZE: Color for update alerts as hex integer.",
        "color = 0x1C7ED6",
        "",
        "[severity_colors]",
        "# CUSTOMIZE: Hex color for critical severity alerts.",
        "critical = 0xE03131",
        "# CUSTOMIZE: Hex color for warning severity alerts.",
        "warning = 0xF08C00",
        "# CUSTOMIZE: Hex color for info severity alerts.",
        "info = 0x1971C2",
    ]
    return "\n".join(lines) + "\n"


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
        if not (1 <= args.listen_port <= 65535):
            raise ValueError(f"invalid listen_port: {args.listen_port}")
        if args.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be positive")
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        if args.retry_after_cap < 0:
            raise ValueError("--retry-after-cap must be non-negative")
        if args.readiness_interval <= 0:
            raise ValueError("--readiness-interval must be positive")
        if args.condense_threshold < 1:
            raise ValueError("--condense-threshold must be at least 1")

        splunk_sources = (
            args.allowed_splunk_sources
            if args.allowed_splunk_sources
            else DEFAULT_EXAMPLE_SPLUNK_SOURCES
        )

        content = generate_toml(
            listen_address=args.listen_address,
            listen_port=args.listen_port,
            max_request_bytes=args.max_request_bytes,
            secret_env=args.secret_env,
            allowed_splunk_sources=splunk_sources,
            mode=args.mode,
            bot_token_env=args.bot_token_env,
            channel_id=args.channel_id,
            webhook_url_env=args.webhook_url_env,
            api_base_url=args.api_base_url,
            timeout_seconds=args.timeout,
            retry_after_cap_seconds=args.retry_after_cap,
            readiness_interval_seconds=args.readiness_interval,
            default_class=args.default_class,
            condense_threshold=args.condense_threshold,
        )

        args.output.parent.mkdir(parents=True, exist_ok=True)
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
    sys.exit(main())
