#!/usr/bin/env python3
"""Create a local Semaphore reconciler configuration without contacting Semaphore."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from reconcile_semaphore import (
    DEFAULT_CREDENTIAL_LOGIN,
    DEFAULT_MANIFEST,
    DEFAULT_PRIVATE_KEY_ENV,
    DEFAULT_TIMEOUT,
    DEFAULT_TOKEN_ENV,
    DEFAULT_URL,
    Settings,
    validate_settings,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Semaphore base URL")
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help="name of the environment variable that will hold the API token",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--ca-file", default="", help="private CA PEM path for HTTPS")
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="allow plain HTTP to a host that isn't loopback",
    )
    parser.add_argument(
        "--credential-login",
        default=DEFAULT_CREDENTIAL_LOGIN,
        help="SSH login stored with a managed credential",
    )
    parser.add_argument(
        "--private-key-env",
        default=DEFAULT_PRIVATE_KEY_ENV,
        help="name of the environment variable that will hold the SSH private key",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        metavar="PATH",
        help=(
            f"manifest path, relative to the config file; repeat for several "
            f"projects (default: {DEFAULT_MANIFEST})"
        ),
    )
    return parser


def toml_string(value: str) -> str:
    return json.dumps(value)


def configuration_text(settings: Settings, manifests: list[str]) -> str:
    manifest_list = ", ".join(toml_string(path) for path in manifests)
    return "\n".join(
        (
            "[semaphore]",
            "# CUSTOMIZE: Confirm the Semaphore base URL, without /api.",
            f"url = {toml_string(settings.url)}",
            "# CUSTOMIZE: Store only the API-token environment-variable name.",
            f"token_env = {toml_string(settings.token_env)}",
            "# CUSTOMIZE: Confirm the request timeout in seconds.",
            f"timeout_seconds = {settings.timeout}",
            "# CUSTOMIZE: Confirm the private CA path or leave it empty.",
            f"ca_file = {toml_string(str(settings.ca_file or ''))}",
            "# CUSTOMIZE: Keep false unless plain HTTP crosses a trusted network.",
            f"allow_insecure_http = {str(settings.allow_insecure_http).lower()}",
            "",
            "[credential]",
            "# CUSTOMIZE: Confirm the SSH login Semaphore uses with this credential.",
            f"login = {toml_string(settings.credential_login)}",
            "# CUSTOMIZE: Store only the private-key environment-variable name.",
            f"private_key_env = {toml_string(settings.private_key_env)}",
            "",
            "[manifests]",
            "# CUSTOMIZE: List every manifest; relative paths start at this file.",
            f"paths = [{manifest_list}]",
            "",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1

    manifests = args.manifest or [DEFAULT_MANIFEST]
    settings = Settings(
        url=args.url,
        token_env=args.token_env,
        timeout=args.timeout,
        ca_file=Path(args.ca_file).expanduser() if args.ca_file else None,
        allow_insecure_http=args.allow_insecure_http,
        credential_login=args.credential_login,
        private_key_env=args.private_key_env,
        manifests=tuple(Path(path) for path in manifests),
    )
    try:
        validate_settings(settings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation closes the race between the early existence check and
        # this write. A concurrently created local config is never replaced.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(configuration_text(settings, manifests))
    except FileExistsError:
        print(
            f"error: refusing to replace existing file: {args.output}",
            file=sys.stderr,
        )
        return 1
    print(f"configuration-written: {args.output}")
    token_state = "set" if os.environ.get(settings.token_env) else "unset"
    print(f"token-env: {settings.token_env} is {token_state}")
    print("next-step: copy manifest.yml.example to each manifest path and edit it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
