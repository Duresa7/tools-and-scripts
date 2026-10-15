#!/usr/bin/env python3
"""Create local configuration for the Cloudflare webhook range sync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from webhook_ranges import (
    DEFAULT_API_URL,
    DEFAULT_LIST_NAME,
    DEFAULT_META_URL,
    DEFAULT_METRIC_PREFIX,
    DEFAULT_MINIMUM_RANGES,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_TIMEOUT,
    TOKEN_VARIABLE,
    validate_output_path,
)


def format_toml_string(value: str) -> str:
    escaped = value.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate config.local.toml for the Cloudflare webhook range sync. "
            f"The token is never written here; it stays in {TOKEN_VARIABLE}."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output config path; existing file is not replaced without --overwrite",
    )
    parser.add_argument(
        "--meta-url", default=DEFAULT_META_URL, help="GitHub meta document URL"
    )
    parser.add_argument(
        "--minimum-ranges",
        type=int,
        default=DEFAULT_MINIMUM_RANGES,
        help="refuse to sync when GitHub publishes fewer ranges than this",
    )
    parser.add_argument(
        "--api-url", default=DEFAULT_API_URL, help="Cloudflare API base URL"
    )
    parser.add_argument(
        "--account-id",
        default="",
        help="Cloudflare account identifier; leave empty for a single-account token",
    )
    parser.add_argument(
        "--list-name", default=DEFAULT_LIST_NAME, help="Zero Trust IP list name"
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
        help="destination .prom file for the node_exporter textfile collector",
    )
    parser.add_argument(
        "--metric-prefix",
        default=DEFAULT_METRIC_PREFIX,
        help="prefix for the exported metrics",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow overwriting an existing output file",
    )
    return parser


def generate_toml(args: argparse.Namespace) -> str:
    lines = [
        "[github]",
        "# CUSTOMIZE: The document that publishes the hooks ranges.",
        f"meta_url = {format_toml_string(args.meta_url)}",
        "# CUSTOMIZE: Refuse to sync when GitHub publishes fewer ranges than this.",
        f"minimum_ranges = {args.minimum_ranges}",
        "",
        "[cloudflare]",
        "# CUSTOMIZE: Cloudflare API base URL.",
        f"api_url = {format_toml_string(args.api_url)}",
        "# CUSTOMIZE: Account identifier. Leave empty when the token sees one account.",
        f"account_id = {format_toml_string(args.account_id)}",
        "# CUSTOMIZE: Name of the Zero Trust IP list to keep in sync.",
        f"list_name = {format_toml_string(args.list_name)}",
        "# CUSTOMIZE: Request timeout in seconds.",
        f"timeout_seconds = {args.timeout}",
        f"# The API token is read from the {TOKEN_VARIABLE} environment variable.",
        "",
        "[metrics]",
        "# CUSTOMIZE: Destination .prom file for the node_exporter textfile collector.",
        f"output_path = {format_toml_string(args.output_path)}",
        "# CUSTOMIZE: Prefix for the exported metrics.",
        f"metric_prefix = {format_toml_string(args.metric_prefix)}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists() and not args.overwrite:
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1
    try:
        validate_output_path(Path(args.output_path))
        if args.timeout <= 0:
            raise ValueError("--timeout must be a positive number")
        if args.minimum_ranges < 1:
            raise ValueError("--minimum-ranges must be at least 1")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation closes the race against a concurrent run unless
        # the overwrite was asked for.
        mode = "w" if args.overwrite else "x"
        with args.output.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(generate_toml(args))
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"configuration-written: {args.output}")
    print(f"next-step: export {TOKEN_VARIABLE} and run webhook_ranges.py --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
