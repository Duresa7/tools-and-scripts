import argparse
import contextlib
import importlib.util
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "minecraft_status_probe.py"
SPEC = importlib.util.spec_from_file_location("minecraft_status_probe", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ConnectionFailure = MODULE.ConnectionFailure
DEFAULT_FORMAT = MODULE.DEFAULT_FORMAT
DEFAULT_HOST = MODULE.DEFAULT_HOST
DEFAULT_PORT = MODULE.DEFAULT_PORT
DEFAULT_PROTOCOL_VERSION = MODULE.DEFAULT_PROTOCOL_VERSION
DEFAULT_TIMEOUT = MODULE.DEFAULT_TIMEOUT
ProbeSettings = MODULE.ProbeSettings
ProtocolFailure = MODULE.ProtocolFailure
StatusResult = MODULE.StatusResult
build_parser = MODULE.build_parser
encode_varint = MODULE.encode_varint
format_output = MODULE.format_output
load_settings = MODULE.load_settings
main = MODULE.main
packet = MODULE.packet
parse_settings = MODULE.parse_settings
query_status = MODULE.query_status
read_exact = MODULE.read_exact
read_varint = MODULE.read_varint
resolve_settings = MODULE.resolve_settings
text_description = MODULE.text_description


class FakeMinecraftServer:
    """Minimal TCP server simulating Minecraft Server List Ping protocol."""

    def __init__(self, handler: Callable[[socket.socket], None]):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.handler = handler
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            conn, _ = self.sock.accept()
            with conn:
                self.handler(conn)
        except OSError:
            pass

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()
        self._thread.join(timeout=1.0)


def standard_server_handler(
    conn: socket.socket,
    status_dict: dict | None = None,
    handle_ping: bool = True,
) -> None:
    if status_dict is None:
        status_dict = {
            "version": {"name": "1.21.1", "protocol": 767},
            "players": {"online": 5, "max": 20},
            "description": {
                "text": "A Minecraft Test Server",
                "extra": [{"text": " - Welcome!"}],
            },
        }

    # 1. Read handshake packet: len, id, payload
    hs_len = read_varint(conn)
    read_exact(conn, hs_len)

    # 2. Read status request packet: len, id
    req_len = read_varint(conn)
    read_exact(conn, req_len)

    # 3. Send status response packet
    raw_json = json.dumps(status_dict).encode("utf-8")
    payload = encode_varint(0) + encode_varint(len(raw_json)) + raw_json
    conn.sendall(packet(payload))

    # 4. Optional ping / pong exchange
    if handle_ping:
        try:
            conn.settimeout(0.5)
            ping_len = read_varint(conn)
            ping_id = read_varint(conn)
            if ping_id == 1 and ping_len >= 9:
                ping_payload = read_exact(conn, 8)
                pong_data = encode_varint(1) + ping_payload
                conn.sendall(packet(pong_data))
        except (TimeoutError, ConnectionError):
            pass


def test_varint_encoding_and_decoding() -> None:
    test_values = [0, 1, 2, 127, 128, 255, 25565, 2097151, 2147483647]
    for val in test_values:
        encoded = encode_varint(val)
        # Create a mock socket-like object via socketpair
        client, server = socket.socketpair()
        with client, server:
            client.sendall(encoded)
            decoded = read_varint(server)
            assert decoded == val


def test_text_description_parsing() -> None:
    assert text_description("Simple MOTD") == "Simple MOTD"
    assert text_description(123) == ""

    complex_desc = {
        "text": "Header",
        "extra": [
            " - subtitle",
            {"text": " - sub2", "extra": [{"text": "!"}]},
        ],
    }
    assert text_description(complex_desc) == "Header - subtitle - sub2!"


def test_successful_status_query() -> None:
    server = FakeMinecraftServer(standard_server_handler)
    try:
        result = query_status("127.0.0.1", server.port, timeout=2.0)
        assert result.host == "127.0.0.1"
        assert result.port == server.port
        assert result.version_name == "1.21.1"
        assert result.protocol == 767
        assert result.players_online == 5
        assert result.players_max == 20
        assert result.description == "A Minecraft Test Server - Welcome!"
        assert result.latency_ms >= 0
        assert result.ping_latency_ms is None
    finally:
        server.close()


def test_successful_ping_latency_measurement() -> None:
    server = FakeMinecraftServer(standard_server_handler)
    try:
        result = query_status("127.0.0.1", server.port, timeout=2.0, ping=True)
        assert result.ping_latency_ms is not None
        assert result.ping_latency_ms >= 0
    finally:
        server.close()


def test_output_formatting() -> None:
    result = StatusResult(
        host="mc.example.net",
        port=25565,
        latency_ms=12.4,
        version_name="1.21.1",
        protocol=767,
        players_online=3,
        players_max=20,
        description="A Minecraft Server",
        ping_latency_ms=11.2,
    )

    text_out = format_output(result, "text")
    assert "host: mc.example.net:25565" in text_out
    assert "status: online" in text_out
    assert "version: 1.21.1 (protocol 767)" in text_out
    assert "players: 3/20" in text_out
    assert "latency: 12.4 ms" in text_out
    assert "ping_latency: 11.2 ms" in text_out
    assert "description: A Minecraft Server" in text_out

    json_out = format_output(result, "json")
    data = json.loads(json_out)
    assert data["host"] == "mc.example.net"
    assert data["port"] == 25565
    assert data["latency_ms"] == 12.4
    assert data["ping_latency_ms"] == 11.2
    assert data["players_online"] == 3
    assert data["players_max"] == 20


def test_malformed_packet_id() -> None:
    def bad_id_handler(conn: socket.socket) -> None:
        hs_len = read_varint(conn)
        read_exact(conn, hs_len)
        req_len = read_varint(conn)
        read_exact(conn, req_len)
        # Send packet with ID 2 instead of 0
        payload = encode_varint(2) + b"unexpected"
        conn.sendall(packet(payload))

    server = FakeMinecraftServer(bad_id_handler)
    try:
        with pytest.raises(ProtocolFailure, match="unexpected response packet id 2"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_malformed_varint() -> None:
    def bad_varint_handler(conn: socket.socket) -> None:
        hs_len = read_varint(conn)
        read_exact(conn, hs_len)
        req_len = read_varint(conn)
        read_exact(conn, req_len)
        # 5 bytes with the high bit set violate VarInt encoding
        conn.sendall(b"\xff\xff\xff\xff\xff")

    server = FakeMinecraftServer(bad_varint_handler)
    try:
        with pytest.raises(ProtocolFailure, match="VarInt exceeds five bytes"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_malformed_json_response() -> None:
    def bad_json_handler(conn: socket.socket) -> None:
        hs_len = read_varint(conn)
        read_exact(conn, hs_len)
        req_len = read_varint(conn)
        read_exact(conn, req_len)
        bad_json = b"not-valid-json"
        payload = encode_varint(0) + encode_varint(len(bad_json)) + bad_json
        conn.sendall(packet(payload))

    server = FakeMinecraftServer(bad_json_handler)
    try:
        with pytest.raises(ProtocolFailure, match="malformed JSON status response"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_early_connection_close() -> None:
    def early_close_handler(conn: socket.socket) -> None:
        hs_len = read_varint(conn)
        read_exact(conn, hs_len)
        # Close connection immediately without responding

    server = FakeMinecraftServer(early_close_handler)
    try:
        with pytest.raises(
            ProtocolFailure,
            match=r"(connection closed|Connection reset)",
        ):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_connection_timeout() -> None:
    def sleeping_handler(conn: socket.socket) -> None:
        time.sleep(1.0)

    server = FakeMinecraftServer(sleeping_handler)
    try:
        with pytest.raises(ConnectionFailure, match="timed out"):
            query_status("127.0.0.1", server.port, timeout=0.2)
    finally:
        server.close()


def test_connection_refused() -> None:
    # Pick a port that is not listening
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    unused_port = sock.getsockname()[1]
    sock.close()

    with pytest.raises(ConnectionFailure, match="failed to connect"):
        query_status("127.0.0.1", unused_port, timeout=1.0)


def test_config_precedence() -> None:
    # 1. Defaults
    default_settings = ProbeSettings()
    assert default_settings.host == DEFAULT_HOST
    assert default_settings.port == DEFAULT_PORT
    assert default_settings.format == DEFAULT_FORMAT

    # 2. Config overrides defaults
    config_dict = {
        "probe": {
            "host": "mc.example.net",
            "port": 25566,
            "timeout_seconds": 15.0,
            "protocol_version": 765,
            "format": "json",
            "srv": False,
            "ping": True,
        }
    }
    parsed = parse_settings(config_dict)
    assert parsed.host == "mc.example.net"
    assert parsed.port == 25566
    assert parsed.timeout == 15.0
    assert parsed.protocol_version == 765
    assert parsed.format == "json"
    assert parsed.ping is True

    # 3. CLI arguments override config
    cli_args = argparse.Namespace(
        host="192.0.2.10",
        host_flag=None,
        port=25570,
        timeout=5.0,
        protocol_version=None,
        format="text",
        srv=None,
        ping=False,
    )
    resolved = resolve_settings(parsed, cli_args)
    assert resolved.host == "192.0.2.10"
    assert resolved.port == 25570
    assert resolved.timeout == 5.0
    assert resolved.protocol_version == 765  # kept from config
    assert resolved.format == "text"
    assert resolved.ping is False  # overridden by CLI


def test_srv_and_port_mutual_exclusion() -> None:
    parsed = ProbeSettings()
    cli_args = argparse.Namespace(
        host="mc.example.net",
        host_flag=None,
        port=25565,
        timeout=10.0,
        protocol_version=767,
        format="text",
        srv=True,
        ping=False,
    )
    with pytest.raises(ValueError, match="--srv and --port cannot be used together"):
        resolve_settings(parsed, cli_args)


def test_main_cli_execution_success(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeMinecraftServer(standard_server_handler)
    try:
        ret = main(["--port", str(server.port), "--format", "json"])
        assert ret == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["port"] == server.port
        assert data["version_name"] == "1.21.1"
    finally:
        server.close()


def test_main_cli_network_failure(capsys: pytest.CaptureFixture[str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    unused_port = sock.getsockname()[1]
    sock.close()

    ret = main(["--port", str(unused_port), "--timeout", "0.5"])
    assert ret == 2
    captured = capsys.readouterr()
    assert "error: connection failure" in captured.err


def test_main_cli_protocol_failure(capsys: pytest.CaptureFixture[str]) -> None:
    def bad_handler(conn: socket.socket) -> None:
        read_exact(conn, read_varint(conn))
        read_exact(conn, read_varint(conn))
        # Send bad packet ID
        conn.sendall(packet(encode_varint(99) + b"error"))

    server = FakeMinecraftServer(bad_handler)
    try:
        ret = main(["--port", str(server.port), "--timeout", "1.0"])
        assert ret == 3
        captured = capsys.readouterr()
        assert "error: protocol failure" in captured.err
    finally:
        server.close()


def test_main_cli_invalid_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    ret = main(["--port", "999999"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "error: configuration failure" in captured.err


def test_configurator_writes_and_refuses_overwrite(tmp_path: Path) -> None:
    output_file = tmp_path / "config.local.toml"
    configure_path = MODULE_PATH.with_name("configure.py")

    cmd = [
        sys.executable,
        str(configure_path),
        "--output",
        str(output_file),
        "--host",
        "mc.example.net",
    ]

    first = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert first.returncode == 0
    assert "configuration-written" in first.stdout
    assert output_file.is_file()

    # Second run should refuse to overwrite
    second = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert second.returncode == 1
    assert "refusing to replace existing file" in second.stderr

    loaded = load_settings(output_file)
    assert loaded.host == "mc.example.net"


def test_cli_help() -> None:
    for script_name in ("minecraft_status_probe.py", "configure.py"):
        script_path = MODULE_PATH.with_name(script_name)
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout


def test_zero_length_status_frame_rejected() -> None:
    def zero_len_handler(conn: socket.socket) -> None:
        read_exact(conn, read_varint(conn))
        read_exact(conn, read_varint(conn))
        # Zero-length frame followed by valid packet ID 0 and JSON
        raw_json = json.dumps({"version": {"name": "1.21.1", "protocol": 767}}).encode(
            "utf-8"
        )
        # Send length 0 frame prefix, then packet ID 0, payload len, and json
        conn.sendall(
            encode_varint(0)
            + encode_varint(0)
            + encode_varint(len(raw_json))
            + raw_json
        )

    server = FakeMinecraftServer(zero_len_handler)
    try:
        with pytest.raises(ProtocolFailure, match=r"(frame length|too short)"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_frame_length_mismatch_rejected() -> None:
    def mismatch_handler(conn: socket.socket) -> None:
        read_exact(conn, read_varint(conn))
        read_exact(conn, read_varint(conn))
        raw_json = json.dumps({"version": {"name": "1.21.1", "protocol": 767}}).encode(
            "utf-8"
        )
        # Declared outer length is 5, but actual content is larger
        payload = encode_varint(0) + encode_varint(len(raw_json)) + raw_json
        conn.sendall(encode_varint(5) + payload)

    server = FakeMinecraftServer(mismatch_handler)
    try:
        with pytest.raises(ProtocolFailure, match="frame length mismatch"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_pong_frame_length_mismatch_rejected() -> None:
    def bad_pong_len_handler(conn: socket.socket) -> None:
        # standard status response
        standard_server_handler(conn, handle_ping=False)
        # Read ping request
        ping_len = read_varint(conn)
        read_exact(conn, ping_len)
        # Send pong with invalid frame length 0
        conn.sendall(encode_varint(0) + encode_varint(1) + struct.pack(">q", 12345))

    server = FakeMinecraftServer(bad_pong_len_handler)
    try:
        with pytest.raises(ProtocolFailure, match=r"pong frame length"):
            query_status("127.0.0.1", server.port, timeout=2.0, ping=True)
    finally:
        server.close()


def test_payload_length_exceeds_maximum() -> None:
    def huge_payload_handler(conn: socket.socket) -> None:
        read_exact(conn, read_varint(conn))
        read_exact(conn, read_varint(conn))
        # Declare payload length 100,000 (exceeds 64 KiB limit)
        huge_len = 100_000
        # Frame length: 1 (packet id 0) + 3 (VarInt 100000) + 100000
        frame_len = 1 + len(encode_varint(huge_len)) + huge_len
        conn.sendall(
            encode_varint(frame_len) + encode_varint(0) + encode_varint(huge_len)
        )

    server = FakeMinecraftServer(huge_payload_handler)
    try:
        with pytest.raises(ProtocolFailure, match=r"(exceeds|limit)"):
            query_status("127.0.0.1", server.port, timeout=2.0)
    finally:
        server.close()


def test_missing_dig_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    ret = main(["--srv", "mc.example.net"])
    assert ret == 2
    captured = capsys.readouterr()
    assert "error: connection failure: dig command not found" in captured.err
