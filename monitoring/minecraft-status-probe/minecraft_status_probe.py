#!/usr/bin/env python3
"""Query a Minecraft Java server status endpoint using Server List Ping."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import struct
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 25565
DEFAULT_TIMEOUT = 10.0
DEFAULT_PROTOCOL_VERSION = 767  # Minecraft Java 1.21 / 1.21.1
DEFAULT_FORMAT = "text"
DEFAULT_CONFIG_NAME = "config.local.toml"
MAX_STATUS_LENGTH = 65536  # 64 KiB maximum status JSON payload
MAX_FRAME_LENGTH = MAX_STATUS_LENGTH + 64  # Maximum outer frame length


class ConnectionFailure(Exception):
    """Network connection or DNS resolution failed."""


class ProtocolFailure(Exception):
    """Server closed connection or sent malformed protocol packets."""


@dataclass(frozen=True)
class ProbeSettings:
    """Settings controlling probe behavior."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    protocol_version: int = DEFAULT_PROTOCOL_VERSION
    format: str = DEFAULT_FORMAT
    srv: bool = False
    ping: bool = False


@dataclass(frozen=True)
class StatusResult:
    """Result of a Minecraft status query."""

    host: str
    port: int
    latency_ms: float
    version_name: str | None
    protocol: int | None
    players_online: int | None
    players_max: int | None
    description: str
    ping_latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "latency_ms": self.latency_ms,
            "version_name": self.version_name,
            "protocol": self.protocol,
            "players_online": self.players_online,
            "players_max": self.players_max,
            "description": self.description,
        }
        if self.ping_latency_ms is not None:
            data["ping_latency_ms"] = self.ping_latency_ms
        return data


def encode_varint(value: int) -> bytes:
    """Encode an integer as a Minecraft protocol VarInt."""
    value &= 0xFFFFFFFF
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        output.append(byte)
        if not value:
            return bytes(output)


def read_exact(
    stream: socket.socket, length: int, deadline: float | None = None
) -> bytes:
    """Read an exact number of bytes from the socket bounded by an overall deadline."""
    if length < 0:
        raise ValueError(f"invalid negative read length: {length}")
    if length > MAX_STATUS_LENGTH:
        raise ValueError(
            f"requested read length {length} exceeds maximum limit of "
            f"{MAX_STATUS_LENGTH} bytes"
        )
    output = bytearray()
    while len(output) < length:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for response")
            stream.settimeout(remaining)
        chunk = stream.recv(length - len(output))
        if not chunk:
            raise ConnectionError("connection closed before the response completed")
        output.extend(chunk)
    return bytes(output)


def read_varint_and_size(
    stream: socket.socket, deadline: float | None = None
) -> tuple[int, int]:
    """Read a VarInt from the socket, returning (value, byte_count)."""
    value = 0
    for index in range(5):
        byte = read_exact(stream, 1, deadline=deadline)[0]
        value |= (byte & 0x7F) << (7 * index)
        if not (byte & 0x80):
            return value, index + 1
    raise ValueError("VarInt exceeds five bytes")


def read_varint(stream: socket.socket, deadline: float | None = None) -> int:
    """Read a VarInt from the socket."""
    val, _ = read_varint_and_size(stream, deadline=deadline)
    return val


def packet(payload: bytes) -> bytes:
    """Construct a length-prefixed packet."""
    return encode_varint(len(payload)) + payload


def text_description(value: Any) -> str:
    """Extract plain text from a Minecraft JSON chat description structure."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    parts = [str(value.get("text", ""))]
    parts.extend(text_description(item) for item in value.get("extra", []))
    return "".join(parts)


def resolve_srv(host: str, timeout: float) -> tuple[str, int]:
    """Resolve _minecraft._tcp SRV record using dig."""
    dig_path = shutil.which("dig")
    if not dig_path:
        raise ConnectionFailure(
            "dig command not found; required for SRV record resolution"
        )
    try:
        result = subprocess.run(
            [
                dig_path,
                f"+time={max(1, round(timeout))}",
                "+tries=1",
                "+short",
                "SRV",
                f"_minecraft._tcp.{host.rstrip('.')}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectionFailure(f"SRV lookup timed out for {host}") from exc

    if result.returncode != 0:
        raise ConnectionFailure(
            f"SRV lookup failed for {host}: {result.stderr.strip()}"
        )

    records: list[tuple[int, int, str, int]] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        priority, weight, port, target = fields
        records.append((int(priority), -int(weight), target.rstrip("."), int(port)))

    if not records:
        raise ConnectionFailure(f"no Minecraft SRV record found for {host}")

    _, _, target, port = min(records)
    return target, port


def query_status(
    connect_host: str,
    port: int,
    timeout: float,
    protocol_version: int = DEFAULT_PROTOCOL_VERSION,
    ping: bool = False,
    display_host: str | None = None,
) -> StatusResult:
    """Connect to a Minecraft server, perform handshake, and retrieve status."""
    encoded_host = connect_host.encode("utf-8")
    handshake = (
        encode_varint(protocol_version)
        + encode_varint(len(encoded_host))
        + encoded_host
        + struct.pack(">H", port)
        + encode_varint(1)
    )

    started = time.monotonic()
    deadline = started + timeout
    try:
        stream = socket.create_connection((connect_host, port), timeout=timeout)
    except TimeoutError as exc:
        raise ConnectionFailure(
            f"connection timed out connecting to {connect_host}:{port}"
        ) from exc
    except OSError as exc:
        raise ConnectionFailure(
            f"failed to connect to {connect_host}:{port}: {exc}"
        ) from exc

    with stream:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out before sending handshake")
            stream.settimeout(remaining)

            stream.sendall(packet(encode_varint(0) + handshake))
            stream.sendall(packet(encode_varint(0)))

            total_length, _ = read_varint_and_size(stream, deadline=deadline)
            if total_length < 2:
                raise ProtocolFailure(
                    f"response frame length {total_length} is too short"
                )
            if total_length > MAX_FRAME_LENGTH:
                raise ProtocolFailure(
                    f"response frame length {total_length} exceeds limit of "
                    f"{MAX_FRAME_LENGTH} bytes"
                )

            packet_id, packet_id_bytes = read_varint_and_size(stream, deadline=deadline)
            if packet_id != 0:
                raise ProtocolFailure(f"unexpected response packet id {packet_id}")

            payload_length, payload_len_bytes = read_varint_and_size(
                stream, deadline=deadline
            )
            if payload_length > MAX_STATUS_LENGTH:
                raise ProtocolFailure(
                    f"status response payload length {payload_length} exceeds "
                    f"limit of {MAX_STATUS_LENGTH} bytes"
                )
            if payload_length < 0:
                raise ProtocolFailure(f"invalid payload length {payload_length}")

            actual_frame_length = packet_id_bytes + payload_len_bytes + payload_length
            if total_length != actual_frame_length:
                raise ProtocolFailure(
                    f"response frame length mismatch: declared {total_length}, "
                    f"expected {actual_frame_length}"
                )

            raw_json = read_exact(stream, payload_length, deadline=deadline).decode(
                "utf-8"
            )
            try:
                response = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise ProtocolFailure(f"malformed JSON status response: {exc}") from exc
            if not isinstance(response, dict):
                raise ProtocolFailure("status response is not a JSON object")
            latency_ms = round((time.monotonic() - started) * 1000, 1)

            ping_latency_ms: float | None = None
            if ping:
                ping_time = time.monotonic()
                ping_payload = struct.pack(">q", int(time.time() * 1000))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out before sending ping")
                stream.settimeout(remaining)
                stream.sendall(packet(encode_varint(1) + ping_payload))

                pong_len, _ = read_varint_and_size(stream, deadline=deadline)
                pong_id, pong_id_bytes = read_varint_and_size(stream, deadline=deadline)
                if pong_id != 1:
                    raise ProtocolFailure(f"unexpected pong packet id {pong_id}")
                if pong_len != pong_id_bytes + 8:
                    raise ProtocolFailure(
                        f"pong frame length mismatch: declared {pong_len}, "
                        f"expected {pong_id_bytes + 8}"
                    )
                pong_payload = read_exact(stream, 8, deadline=deadline)
                if pong_payload != ping_payload:
                    raise ProtocolFailure("pong payload did not match ping payload")
                ping_latency_ms = round((time.monotonic() - ping_time) * 1000, 1)
        except TimeoutError as exc:
            raise ConnectionFailure(
                f"timed out waiting for response from {connect_host}:{port}"
            ) from exc
        except (ConnectionError, ValueError) as exc:
            raise ProtocolFailure(
                f"protocol error from {connect_host}:{port}: {exc}"
            ) from exc

    players = response.get("players")
    if not isinstance(players, dict):
        players = {}
    version = response.get("version")
    if not isinstance(version, dict):
        version = {}

    return StatusResult(
        host=display_host or connect_host,
        port=port,
        latency_ms=latency_ms,
        version_name=version.get("name"),
        protocol=version.get("protocol"),
        players_online=players.get("online"),
        players_max=players.get("max"),
        description=text_description(response.get("description")),
        ping_latency_ms=ping_latency_ms,
    )


def format_output(result: StatusResult, output_format: str) -> str:
    """Format the probe result as text or JSON."""
    if output_format == "json":
        return json.dumps(result.to_dict(), indent=2)
    ver = result.version_name or "unknown"
    proto = result.protocol if result.protocol is not None else "unknown"
    online = result.players_online if result.players_online is not None else 0
    max_p = result.players_max if result.players_max is not None else 0
    lines = [
        f"host: {result.host}:{result.port}",
        "status: online",
        f"version: {ver} (protocol {proto})",
        f"players: {online}/{max_p}",
        f"latency: {result.latency_ms} ms",
    ]
    if result.ping_latency_ms is not None:
        lines.append(f"ping_latency: {result.ping_latency_ms} ms")
    lines.append(f"description: {result.description}")
    return "\n".join(lines)


def parse_settings(payload: Any) -> ProbeSettings:
    """Parse probe configuration from a TOML document payload."""
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    section = payload.get("probe", payload)
    if not isinstance(section, dict):
        raise ValueError("[probe] must be a TOML table")

    host = section.get("host", DEFAULT_HOST)
    if not isinstance(host, str) or not host.strip():
        raise ValueError("probe.host must be a non-empty string")

    port = section.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("probe.port must be an integer between 1 and 65535")

    timeout_raw = section.get("timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout_raw, (int, float)) or timeout_raw <= 0:
        raise ValueError("probe.timeout_seconds must be a positive number")
    timeout = float(timeout_raw)

    protocol = section.get("protocol_version", DEFAULT_PROTOCOL_VERSION)
    if not isinstance(protocol, int) or protocol < 0:
        raise ValueError("probe.protocol_version must be a non-negative integer")

    out_format = section.get("format", DEFAULT_FORMAT)
    if out_format not in ("text", "json"):
        raise ValueError("probe.format must be 'text' or 'json'")

    srv = section.get("srv", False)
    if not isinstance(srv, bool):
        raise ValueError("probe.srv must be true or false")

    ping = section.get("ping", False)
    if not isinstance(ping, bool):
        raise ValueError("probe.ping must be true or false")

    return ProbeSettings(
        host=host.strip(),
        port=port,
        timeout=timeout,
        protocol_version=protocol,
        format=out_format,
        srv=srv,
        ping=ping,
    )


def load_settings(path: Path | None) -> ProbeSettings:
    """Load probe settings from a TOML file if present."""
    if path is None:
        return ProbeSettings()
    with path.open("rb") as handle:
        return parse_settings(tomllib.load(handle))


def build_parser() -> argparse.ArgumentParser:
    """Construct the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Probe a Minecraft Java server using Server List Ping."
    )
    parser.add_argument(
        "host",
        nargs="?",
        help="server hostname or IP address; overrides config host",
    )
    parser.add_argument(
        "--host",
        dest="host_flag",
        help="server hostname or IP address; overrides config host",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="server TCP port; overrides config port (default: 25565)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="timeout in seconds; overrides config timeout (default: 10.0)",
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        help="protocol version number; overrides config (default: 767)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        help="output format; overrides config format (default: text)",
    )
    parser.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="shorthand for --format json",
    )
    parser.add_argument(
        "--text",
        action="store_const",
        const="text",
        dest="format",
        help="shorthand for --format text",
    )
    srv_group = parser.add_mutually_exclusive_group()
    srv_group.add_argument(
        "--srv",
        action="store_true",
        default=None,
        help="resolve _minecraft._tcp SRV record",
    )
    srv_group.add_argument(
        "--no-srv",
        action="store_false",
        dest="srv",
        help="disable SRV record resolution",
    )
    ping_group = parser.add_mutually_exclusive_group()
    ping_group.add_argument(
        "--ping",
        action="store_true",
        default=None,
        help="measure round-trip ping/pong latency",
    )
    ping_group.add_argument(
        "--no-ping",
        action="store_false",
        dest="ping",
        help="disable ping/pong latency measurement",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to local TOML config file (default: config.local.toml if present)",
    )
    return parser


def resolve_settings(base: ProbeSettings, args: argparse.Namespace) -> ProbeSettings:
    """Merge CLI arguments over configuration settings."""
    host = args.host_flag or args.host or base.host
    port = args.port if args.port is not None else base.port
    timeout = args.timeout if args.timeout is not None else base.timeout
    protocol_version = (
        args.protocol_version
        if args.protocol_version is not None
        else base.protocol_version
    )
    out_format = args.format if args.format is not None else base.format
    srv = args.srv if args.srv is not None else base.srv
    ping = args.ping if args.ping is not None else base.ping

    if srv and args.port is not None:
        raise ValueError("--srv and --port cannot be used together")
    if not (1 <= port <= 65535):
        raise ValueError("port must be an integer between 1 and 65535")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    if protocol_version < 0:
        raise ValueError("protocol_version must be non-negative")

    return ProbeSettings(
        host=host,
        port=port,
        timeout=timeout,
        protocol_version=protocol_version,
        format=out_format,
        srv=srv,
        ping=ping,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the probe and return an exit code."""
    args = build_parser().parse_args(argv)

    config_path = args.config
    if config_path is None:
        default_path = Path(__file__).resolve().with_name(DEFAULT_CONFIG_NAME)
        if default_path.is_file():
            config_path = default_path

    try:
        settings = load_settings(config_path)
        resolved = resolve_settings(settings, args)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: configuration failure: {exc}", file=sys.stderr)
        return 1

    try:
        if resolved.srv:
            connect_host, port = resolve_srv(resolved.host, resolved.timeout)
            display_host: str | None = resolved.host
        else:
            connect_host = resolved.host
            port = resolved.port
            display_host = None

        result = query_status(
            connect_host=connect_host,
            port=port,
            timeout=resolved.timeout,
            protocol_version=resolved.protocol_version,
            ping=resolved.ping,
            display_host=display_host,
        )
    except ConnectionFailure as exc:
        print(f"error: connection failure: {exc}", file=sys.stderr)
        return 2
    except ProtocolFailure as exc:
        print(f"error: protocol failure: {exc}", file=sys.stderr)
        return 3

    print(format_output(result, resolved.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
