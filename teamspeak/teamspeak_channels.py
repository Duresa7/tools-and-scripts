#!/usr/bin/env python3
"""Export and import a TeamSpeak 3 channel hierarchy."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

ESCAPES = {
    "\\": r"\\",
    "/": r"\/",
    " ": r"\s",
    "|": r"\p",
    "\a": r"\a",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
    "\v": r"\v",
}
UNESCAPES = {
    "\\": "\\",
    "/": "/",
    "s": " ",
    "p": "|",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}

EXTRA_CHANNEL_VARIABLES = (
    "channel_description",
    "channel_codec_latency_factor",
    "channel_codec_is_unencrypted",
    "channel_delete_delay",
    "channel_flag_maxclients_unlimited",
    "channel_flag_maxfamilyclients_unlimited",
    "channel_flag_maxfamilyclients_inherited",
    "channel_name_phonetic",
    "channel_banner_gfx_url",
    "channel_banner_mode",
)

COPY_PROPERTIES = (
    "channel_name",
    "channel_topic",
    "channel_description",
    "channel_codec",
    "channel_codec_quality",
    "channel_maxclients",
    "channel_maxfamilyclients",
    "channel_flag_permanent",
    "channel_flag_semi_permanent",
    "channel_flag_default",
    "channel_flag_maxclients_unlimited",
    "channel_flag_maxfamilyclients_unlimited",
    "channel_flag_maxfamilyclients_inherited",
    "channel_needed_talk_power",
    "channel_name_phonetic",
    "channel_banner_gfx_url",
    "channel_banner_mode",
)


class QueryError(RuntimeError):
    """A query error that omits the command and any credential it carried."""

    def __init__(
        self, operation: str, error_id: str, message: str, extra_message: str = ""
    ) -> None:
        details = f"{operation} failed: id={error_id} msg={message or 'unknown'}"
        if extra_message:
            details += f" extra_msg={extra_message}"
        super().__init__(details)


@dataclass(frozen=True)
class ImportStats:
    created: int = 0
    skipped: int = 0
    failed: int = 0


def ts3_escape(value: str) -> str:
    return "".join(ESCAPES.get(character, character) for character in value)


def ts3_unescape(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            output.append(UNESCAPES.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            output.append(character)
            index += 1
    return "".join(output)


def parse_record(piece: str) -> dict[str, str | bool]:
    record: dict[str, str | bool] = {}
    for token in piece.split():
        if "=" in token:
            key, value = token.split("=", 1)
            record[key] = ts3_unescape(value)
        else:
            record[token] = True
    return record


class TS3Connection:
    """Small ClientQuery and ServerQuery connection."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 10.0,
        banner_timeout: float = 0.25,
    ) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._drain_banner(banner_timeout)
        self._socket.settimeout(timeout)
        self._reader = self._socket.makefile("rb")

    def _drain_banner(self, banner_timeout: float) -> None:
        self._socket.settimeout(banner_timeout)
        while True:
            try:
                data = self._socket.recv(4096)
            except TimeoutError:
                break
            if not data:
                raise ConnectionError("query endpoint closed while sending its banner")

    def command(
        self, command: str, operation: str, *, sensitive: bool = False
    ) -> list[dict[str, str | bool]]:
        self._socket.sendall((command + "\n").encode("utf-8"))
        records: list[dict[str, str | bool]] = []
        while True:
            raw = self._reader.readline()
            if not raw:
                raise ConnectionError(f"query endpoint closed during {operation}")
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text.startswith("error "):
                error = parse_record(text.removeprefix("error "))
                error_id = str(error.get("id", "unknown"))
                if error_id != "0":
                    raise QueryError(
                        operation,
                        error_id,
                        str(error.get("msg", "")),
                        "" if sensitive else str(error.get("extra_msg", "")),
                    )
                return records
            records.extend(parse_record(piece) for piece in text.split("|"))

    def close(self) -> None:
        with suppress(OSError):
            self._socket.sendall(b"quit\n")
        try:
            self._reader.close()
        finally:
            self._socket.close()

    def __enter__(self) -> TS3Connection:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def credential(env_name: str, prompt: str) -> str:
    value = os.environ.get(env_name)
    if value:
        return value
    if sys.stdin.isatty():
        value = getpass.getpass(prompt)
        if value:
            return value
    raise ValueError(f"set {env_name} or run from an interactive terminal")


def normalize_channels(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("channel export must contain a JSON list")

    channels: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ValueError(f"channel entry {index} must be an object")
        channel = dict(raw)
        channel_id = str(channel.get("cid", ""))
        parent_id = str(channel.get("pid", "0"))
        name = channel.get("channel_name")
        if not channel_id:
            raise ValueError(f"channel entry {index} has no cid")
        if channel_id in seen_ids:
            raise ValueError(f"duplicate channel cid: {channel_id}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"channel {channel_id} has no channel_name")
        seen_ids.add(channel_id)
        channel["cid"] = channel_id
        channel["pid"] = parent_id
        channel["channel_order"] = str(channel.get("channel_order", "0"))
        channels.append(channel)
    return channels


def order_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return parents before children and reject cycles or missing parents."""

    by_id = {str(channel["cid"]): channel for channel in channels}
    positions = {str(channel["cid"]): index for index, channel in enumerate(channels)}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(channel_id: str) -> int:
        if channel_id in depths:
            return depths[channel_id]
        if channel_id in visiting:
            raise ValueError(f"cycle in channel hierarchy at cid={channel_id}")
        visiting.add(channel_id)
        parent_id = str(by_id[channel_id].get("pid", "0"))
        if parent_id == "0":
            result = 0
        elif parent_id not in by_id:
            raise ValueError(
                f"channel cid={channel_id} references missing parent cid={parent_id}"
            )
        else:
            result = depth(parent_id) + 1
        visiting.remove(channel_id)
        depths[channel_id] = result
        return result

    for channel_id in by_id:
        depth(channel_id)
    return sorted(
        channels,
        key=lambda channel: (
            depths[str(channel["cid"])],
            positions[str(channel["cid"])],
        ),
    )


def channelcreate_command(
    channel: dict[str, Any], new_parent_id: str, new_order_id: str
) -> str:
    parts = ["channelcreate"]
    for key in COPY_PROPERTIES:
        value = channel.get(key)
        if value is None or value == "" or value is True:
            continue
        if key == "channel_maxclients" and (
            str(value) == "-1"
            or channel.get("channel_flag_maxclients_unlimited") in ("1", True)
        ):
            continue
        if key == "channel_maxfamilyclients" and (
            str(value) == "-1"
            or channel.get("channel_flag_maxfamilyclients_unlimited") in ("1", True)
            or channel.get("channel_flag_maxfamilyclients_inherited") in ("1", True)
        ):
            continue
        parts.append(f"{key}={ts3_escape(str(value))}")
    parts.append(f"cpid={new_parent_id}")
    parts.append(f"channel_order={new_order_id}")
    return " ".join(parts)


def export_channels(
    host: str, port: int, api_key: str, handler_id: int | None, timeout: float
) -> tuple[list[dict[str, str | bool]], str]:
    with TS3Connection(host, port, timeout=timeout) as connection:
        connection.command(
            f"auth apikey={ts3_escape(api_key)}",
            "ClientQuery authentication",
            sensitive=True,
        )
        handlers = connection.command(
            "serverconnectionhandlerlist", "listing connected server tabs"
        )
        if not handlers:
            raise ValueError("no server tabs are open in the TeamSpeak client")

        selected_handler = str(handler_id or handlers[0].get("schandlerid", ""))
        if not selected_handler:
            raise ValueError("ClientQuery returned a tab without a handler ID")
        connection.command(
            f"use schandlerid={selected_handler}", "selecting the source server tab"
        )
        whoami = connection.command("whoami", "reading source server identity")
        server_name = str(whoami[0].get("virtualserver_name", "unknown"))

        channels = connection.command(
            "channellist -topic -flags -voice -limits -icon -secondsempty",
            "listing source channels",
        )
        output: list[dict[str, str | bool]] = []
        for channel in channels:
            channel_id = str(channel.get("cid", ""))
            if not channel_id:
                raise ValueError("ClientQuery returned a channel without a cid")
            merged = dict(channel)
            try:
                variables = connection.command(
                    f"channelvariable cid={channel_id} "
                    + " ".join(EXTRA_CHANNEL_VARIABLES),
                    f"reading variables for channel cid={channel_id}",
                )
            except QueryError as exc:
                print(f"warning: {exc}", file=sys.stderr)
            else:
                for record in variables:
                    for key, value in record.items():
                        if key not in {"cid", "pid"}:
                            merged[key] = "" if value is True else value
            output.append(merged)
        return output, server_name


def write_export(path: Path, channels: list[dict[str, Any]], force: bool) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {path.parent}")
    if path.exists() and not force:
        raise FileExistsError(f"output already exists: {path}")

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            json.dump(channels, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        if force:
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def import_channels(
    channels: list[dict[str, Any]],
    host: str,
    port: int,
    server_id: int,
    username: str,
    password: str | None,
    timeout: float,
    dry_run: bool,
    skip_existing: bool,
) -> ImportStats:
    ordered = order_channels(channels)
    if dry_run:
        return _apply_import(ordered, None, dry_run=True, existing={})
    if password is None:
        raise ValueError("ServerQuery password is required")

    with TS3Connection(host, port, timeout=timeout) as connection:
        connection.command(
            "login "
            f"client_login_name={ts3_escape(username)} "
            f"client_login_password={ts3_escape(password)}",
            "ServerQuery authentication",
            sensitive=True,
        )
        connection.command(f"use sid={server_id}", "selecting the target server")
        connection.command("whoami", "reading target server identity")

        existing: dict[tuple[str, str], str] = {}
        if skip_existing:
            for channel in connection.command(
                "channellist", "listing existing target channels"
            ):
                name = str(channel.get("channel_name", ""))
                parent = str(channel.get("pid", "0"))
                channel_id = str(channel.get("cid", ""))
                if name and channel_id:
                    existing.setdefault((name, parent), channel_id)
        return _apply_import(ordered, connection, dry_run=False, existing=existing)


def _apply_import(
    channels: list[dict[str, Any]],
    connection: TS3Connection | None,
    dry_run: bool,
    existing: dict[tuple[str, str], str],
) -> ImportStats:
    old_to_new = {"0": "0"}
    created = skipped = failed = 0

    for channel in channels:
        old_id = str(channel["cid"])
        old_parent = str(channel.get("pid", "0"))
        name = str(channel["channel_name"])
        if old_parent not in old_to_new:
            print(
                f"failed: {name!r} depends on a parent that was not created",
                file=sys.stderr,
            )
            failed += 1
            continue

        new_parent = old_to_new[old_parent]
        existing_id = existing.get((name, new_parent))
        if existing_id:
            old_to_new[old_id] = existing_id
            print(f"skip-existing: {name!r} under parent {new_parent}")
            skipped += 1
            continue

        old_order = str(channel.get("channel_order", "0"))
        new_order = old_to_new.get(old_order, "0")
        command = channelcreate_command(channel, new_parent, new_order)
        if dry_run:
            new_id = f"dry-{old_id}"
            old_to_new[old_id] = new_id
            print(f"would-create: name={name!r} parent={new_parent} order={new_order}")
            created += 1
            continue

        if connection is None:
            raise RuntimeError("live import requires a query connection")
        try:
            result = connection.command(command, f"creating channel {name!r}")
            new_id = str(result[0].get("cid", "")) if result else ""
            if not new_id:
                raise ValueError(f"channelcreate returned no cid for {name!r}")
        except (ConnectionError, QueryError, ValueError) as exc:
            print(f"failed: {exc}", file=sys.stderr)
            failed += 1
            continue

        old_to_new[old_id] = new_id
        print(f"created: {name!r} old={old_id} new={new_id} parent={new_parent}")
        created += 1
    return ImportStats(created=created, skipped=skipped, failed=failed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # CUSTOMIZE: Connection details and credential environment-variable names
    # are options so users can adapt the tool without editing this source file.
    export = subparsers.add_parser("export", help="Export through ClientQuery")
    export.add_argument(
        "--host",
        default="127.0.0.1",
        help="ClientQuery host; change only when the desktop client is remote",
    )
    export.add_argument(
        "--port", type=int, default=25639, help="ClientQuery plugin port"
    )
    export.add_argument(
        "--output",
        type=Path,
        default=Path("channels.json"),
        help="new export path (default: channels.json)",
    )
    export.add_argument(
        "--handler-id",
        type=int,
        help="source server-tab handler ID; needed only with multiple open tabs",
    )
    export.add_argument(
        "--api-key-env",
        default="TS3_CLIENTQUERY_API_KEY",
        help="environment variable containing your ClientQuery API key",
    )
    export.add_argument(
        "--force", action="store_true", help="replace an existing export file"
    )
    export.add_argument(
        "--timeout", type=float, default=10.0, help="query timeout in seconds"
    )

    importer = subparsers.add_parser("import", help="Import through ServerQuery")
    importer.add_argument(
        "--input", type=Path, required=True, help="channel export JSON to import"
    )
    importer.add_argument(
        "--host",
        default="127.0.0.1",
        help="target ServerQuery host or IP address",
    )
    importer.add_argument(
        "--port", type=int, default=10011, help="target ServerQuery TCP port"
    )
    importer.add_argument(
        "--server-id", type=int, default=1, help="target TeamSpeak virtual server ID"
    )
    importer.add_argument(
        "--username", default="serveradmin", help="target ServerQuery username"
    )
    importer.add_argument(
        "--password-env",
        default="TS3_SERVERQUERY_PASSWORD",
        help="environment variable containing your ServerQuery password",
    )
    importer.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the export without connecting to the target",
    )
    importer.add_argument(
        "--skip-existing",
        action="store_true",
        help="reuse same-name target channels instead of failing",
    )
    importer.add_argument(
        "--timeout", type=float, default=10.0, help="query timeout in seconds"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.command == "import" and args.dry_run and args.skip_existing:
        parser.error("--dry-run cannot inspect the server for --skip-existing")

    try:
        if args.command == "export":
            api_key = credential(args.api_key_env, "ClientQuery API key: ")
            channels, server_name = export_channels(
                args.host,
                args.port,
                api_key,
                args.handler_id,
                args.timeout,
            )
            write_export(args.output, channels, args.force)
            print(
                f"exported: server={server_name!r} channels={len(channels)} "
                f"path={args.output}"
            )
            return 0

        with args.input.open(encoding="utf-8") as handle:
            channels = normalize_channels(json.load(handle))
        password = (
            None
            if args.dry_run
            else credential(args.password_env, "ServerQuery password: ")
        )
        stats = import_channels(
            channels,
            args.host,
            args.port,
            args.server_id,
            args.username,
            password,
            args.timeout,
            args.dry_run,
            args.skip_existing,
        )
    except (
        OSError,
        ConnectionError,
        QueryError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"operation-error: {exc}", file=sys.stderr)
        return 1

    print(
        f"import-finished: created={stats.created} skipped={stats.skipped} "
        f"failed={stats.failed}"
    )
    return 2 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
