#!/usr/bin/env python3
"""Create local TeamSpeak migration settings without changing either server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from teamspeak_channels import (
    QueryError,
    SourceSettings,
    TargetSettings,
    ToolSettings,
    TS3Connection,
    credential,
    ts3_escape,
    validate_source,
    validate_target,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("config.local.toml"),
        help="output path; an existing file is never replaced",
    )
    parser.add_argument("--source-host", default="127.0.0.1")
    parser.add_argument("--source-port", type=int, default=25639)
    parser.add_argument("--handler-id", type=int, default=0)
    parser.add_argument("--source-timeout", type=float, default=10.0)
    parser.add_argument("--api-key-env", default="TS3_CLIENTQUERY_API_KEY")
    parser.add_argument("--export-path", type=Path, default=Path("channels.json"))
    parser.add_argument("--target-host", default="192.0.2.40")
    parser.add_argument("--target-port", type=int, default=10011)
    parser.add_argument("--virtual-server-id", type=int, default=1)
    parser.add_argument("--query-username", default="")
    parser.add_argument("--target-timeout", type=float, default=10.0)
    parser.add_argument("--password-env", default="TS3_SERVERQUERY_PASSWORD")
    parser.add_argument("--input-path", type=Path, default=Path("channels.json"))
    parser.add_argument(
        "--discover-source",
        action="store_true",
        help="consent to one ClientQuery session that lists open server tabs",
    )
    return parser


def toml_string(value: str) -> str:
    return json.dumps(value)


def discover_handler(settings: SourceSettings) -> tuple[int, str]:
    api_key = credential(settings.api_key_env, "ClientQuery API key: ")
    with TS3Connection(settings.host, settings.port, timeout=settings.timeout) as query:
        query.command(
            f"auth apikey={ts3_escape(api_key)}",
            "ClientQuery authentication",
            sensitive=True,
        )
        handlers = query.command(
            "serverconnectionhandlerlist", "listing connected server tabs"
        )
        if not handlers:
            raise ValueError("ClientQuery reported no open server tabs")
        available = {
            int(str(record["schandlerid"]))
            for record in handlers
            if str(record.get("schandlerid", "")).isdigit()
        }
        if not available:
            raise ValueError("ClientQuery reported no valid handler IDs")
        selected = settings.handler_id or min(available)
        if selected not in available:
            raise ValueError(f"handler ID {selected} is not open in the client")
        query.command(f"use schandlerid={selected}", "selecting the source tab")
        identity = query.command("whoami", "reading the source server name")
        server_name = str(identity[0].get("virtualserver_name", "unknown"))
        return selected, server_name


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        print(
            f"error: refusing to replace existing file: {args.output}", file=sys.stderr
        )
        return 1

    source = SourceSettings(
        host=args.source_host,
        port=args.source_port,
        handler_id=args.handler_id or None,
        timeout=args.source_timeout,
        api_key_env=args.api_key_env,
        output=args.export_path,
    )
    settings = ToolSettings(
        source=source,
        target=TargetSettings(
            host=args.target_host,
            port=args.target_port,
            server_id=args.virtual_server_id,
            username=args.query_username,
            timeout=args.target_timeout,
            password_env=args.password_env,
            input_path=args.input_path,
        ),
    )
    try:
        validate_source(settings.source)
        validate_target(settings.target, dry_run=True)
        handler_id = settings.source.handler_id
        if args.discover_source:
            handler_id, server_name = discover_handler(settings.source)
            print(f"source-discovered: handler={handler_id} server={server_name!r}")
    except (OSError, ConnectionError, QueryError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    contents = "\n".join(
        (
            "[source]",
            "# CUSTOMIZE: Confirm the ClientQuery host.",
            f"host = {toml_string(settings.source.host)}",
            "# CUSTOMIZE: Confirm the ClientQuery port.",
            f"port = {settings.source.port}",
            "# CUSTOMIZE: Set a handler ID, or 0 for the first open tab.",
            f"handler_id = {handler_id or 0}",
            "# CUSTOMIZE: Confirm the source timeout in seconds.",
            f"timeout_seconds = {settings.source.timeout:g}",
            "# CUSTOMIZE: Store only the API-key environment-variable name.",
            f"api_key_env = {toml_string(settings.source.api_key_env)}",
            "# CUSTOMIZE: Confirm the local export path.",
            f"output_path = {toml_string(str(settings.source.output))}",
            "",
            "[target]",
            "# CUSTOMIZE: Set the target ServerQuery host.",
            f"host = {toml_string(settings.target.host)}",
            "# CUSTOMIZE: Set the target ServerQuery port.",
            f"port = {settings.target.port}",
            "# CUSTOMIZE: Set the target virtual server ID.",
            f"virtual_server_id = {settings.target.server_id}",
            "# CUSTOMIZE: Set a query account allowed to create channels.",
            f"query_username = {toml_string(settings.target.username)}",
            "# CUSTOMIZE: Confirm the target timeout in seconds.",
            f"timeout_seconds = {settings.target.timeout:g}",
            "# CUSTOMIZE: Store only the password environment-variable name.",
            f"password_env = {toml_string(settings.target.password_env)}",
            "# CUSTOMIZE: Confirm the JSON input path.",
            f"input_path = {toml_string(str(settings.target.input_path))}",
            "",
        )
    )
    args.output.write_text(contents, encoding="utf-8")
    print(f"configuration-written: {args.output}")
    if not args.discover_source:
        print("remote-discovery=skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
