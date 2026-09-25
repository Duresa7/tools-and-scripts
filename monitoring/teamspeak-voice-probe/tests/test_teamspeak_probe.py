import argparse
import importlib.util
import shutil
import socket
import struct
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "teamspeak_probe.py"
SPEC = importlib.util.spec_from_file_location("teamspeak_probe", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

CONFIGURE_PATH = Path(__file__).resolve().parents[1] / "configure.py"
CONF_SPEC = importlib.util.spec_from_file_location(
    "teamspeak_configure", CONFIGURE_PATH
)
assert CONF_SPEC and CONF_SPEC.loader
CONF_MODULE = importlib.util.module_from_spec(CONF_SPEC)
sys.modules[CONF_SPEC.name] = CONF_MODULE
CONF_SPEC.loader.exec_module(CONF_MODULE)

DIG_MISSING = MODULE.DIG_MISSING
INIT1_CLIENT_VERSION = MODULE.INIT1_CLIENT_VERSION
INIT1_MAC = MODULE.INIT1_MAC
ProbeResult = MODULE.ProbeResult
ProbeSettings = MODULE.ProbeSettings
PublicResult = MODULE.PublicResult
QueryFailed = MODULE.QueryFailed
QueryResult = MODULE.QueryResult
ServerReport = MODULE.ServerReport
ServerSettings = MODULE.ServerSettings
SrvLookupError = MODULE.SrvLookupError
SrvRecord = MODULE.SrvRecord
build_init1_step0 = MODULE.build_init1_step0
derive_faults = MODULE.derive_faults
find_dig = MODULE.find_dig
is_init1_reply = MODULE.is_init1_reply
label_value = MODULE.label_value
load_settings = MODULE.load_settings
main = MODULE.main
parse_server = MODULE.parse_server
parse_settings = MODULE.parse_settings
parse_srv_records = MODULE.parse_srv_records
probe_public = MODULE.probe_public
probe_voice = MODULE.probe_voice
query_server = MODULE.query_server
read_server_info = MODULE.read_server_info
render_metrics = MODULE.render_metrics
resolve_settings = MODULE.resolve_settings
sample = MODULE.sample
select_srv = MODULE.select_srv
srv_lookup = MODULE.srv_lookup
ts3_escape = MODULE.ts3_escape
validate_dns_server = MODULE.validate_dns_server
validate_hostname = MODULE.validate_hostname
validate_output_path = MODULE.validate_output_path
verdict = MODULE.verdict
write_textfile = MODULE.write_textfile


def test_init1_step0_default_packet_layout() -> None:
    packet = build_init1_step0()

    assert len(packet) == 34
    assert packet.startswith(INIT1_MAC)
    magic, step, flag = struct.unpack(">HHB", packet[8:13])
    assert magic == 101
    assert step == 0
    assert flag == 0x88
    assert packet[13:17] == INIT1_CLIENT_VERSION
    assert packet[17:18] == b"\x00"
    (timestamp,) = struct.unpack(">I", packet[18:22])
    assert timestamp > 0
    assert len(packet[22:26]) == 4
    assert packet[26:34] == b"\x00" * 8


def test_init1_step0_custom_timestamp_and_nonce() -> None:
    stamp = 1700000000
    nonce = b"TEST"
    packet = build_init1_step0(timestamp=stamp, nonce=nonce)

    assert len(packet) == 34
    assert packet[18:22] == struct.pack(">I", stamp)
    assert packet[22:26] == nonce


def test_init1_step0_rejects_invalid_nonce_length() -> None:
    with pytest.raises(ValueError, match="exactly four bytes"):
        build_init1_step0(nonce=b"123")
    with pytest.raises(ValueError, match="exactly four bytes"):
        build_init1_step0(nonce=b"12345")


def build_init1_step1_reply(request: bytes, server_data: bytes = b"\x00" * 16) -> bytes:
    """Build the documented 32-byte Init1 step-1 reply from a step-0 request."""
    nonce = request[22:26] if len(request) >= 26 else b"\x00" * 4
    return (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + server_data + nonce[::-1]
    )


def test_is_init1_reply_documented_layout_passes() -> None:
    step0 = build_init1_step0(nonce=b"WXYZ")
    valid_reply = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x42" * 16 + b"ZYXW"
    )
    assert len(valid_reply) == 32
    assert is_init1_reply(valid_reply, request=step0)
    assert is_init1_reply(valid_reply)


def test_is_init1_reply_old_wrong_layout_fails() -> None:
    # Old wrong layout unpacked >HH from data[8:12] and required step==1
    # (0x0001 at offsets 10-11). Under the documented layout, offset 10 is
    # type byte 0x88 and offset 11 is step byte 0x01.
    old_wrong_reply = INIT1_MAC + struct.pack(">HHB", 101, 1, 0x88) + b"\x00" * 16
    step0 = build_init1_step0()
    assert not is_init1_reply(old_wrong_reply)
    assert not is_init1_reply(old_wrong_reply, request=step0)


def test_is_init1_reply_truncated_fails() -> None:
    step0 = build_init1_step0(nonce=b"ABCD")
    valid_reply = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x00" * 16 + b"DCBA"
    )
    for length in (0, 7, 8, 10, 11, 12, 13, 20, 31):
        assert not is_init1_reply(valid_reply[:length], request=step0)
        assert not is_init1_reply(valid_reply[:length])


def test_is_init1_reply_nonce_mismatch_fails() -> None:
    step0 = build_init1_step0(nonce=b"ABCD")
    # Echoes non-reversed nonce instead of reversed
    non_reversed_reply = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x00" * 16 + b"ABCD"
    )
    assert not is_init1_reply(non_reversed_reply, request=step0)
    # Echoes completely different nonce
    wrong_nonce_reply = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x00" * 16 + b"ZZZZ"
    )
    assert not is_init1_reply(wrong_nonce_reply, request=step0)


def test_is_init1_reply_echo_and_corruptions_fail() -> None:
    step0 = build_init1_step0(nonce=b"ABCD")
    valid_reply = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x00" * 16 + b"DCBA"
    )
    # Echo server returning identical request packet must fail
    assert not is_init1_reply(step0, request=step0)
    assert not is_init1_reply(step0)
    assert not is_init1_reply(valid_reply, request=valid_reply)

    # Corruptions must fail
    assert not is_init1_reply(b"TS3INIT2" + valid_reply[8:], request=step0)
    assert not is_init1_reply(b"GARBAGE" + b"\x00" * 25, request=step0)
    assert not is_init1_reply(b"")
    # Wrong packet ID (102 != 101)
    wrong_pid = (
        INIT1_MAC + struct.pack(">HB", 102, 0x88) + b"\x01" + b"\x00" * 16 + b"DCBA"
    )
    assert not is_init1_reply(wrong_pid, request=step0)
    # Wrong type byte (0x80 != 0x88)
    wrong_type = (
        INIT1_MAC + struct.pack(">HB", 101, 0x80) + b"\x01" + b"\x00" * 16 + b"DCBA"
    )
    assert not is_init1_reply(wrong_type, request=step0)
    # Wrong step byte (0x02 != 0x01)
    wrong_step = (
        INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x02" + b"\x00" * 16 + b"DCBA"
    )
    assert not is_init1_reply(wrong_step, request=step0)


def run_fake_udp_server(
    reply_payload: bytes | Callable[[bytes], bytes] | None,
    ready: threading.Event,
    stop: threading.Event,
    ports: list[int],
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    ports.append(sock.getsockname()[1])
    sock.settimeout(0.2)
    ready.set()
    try:
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            if callable(reply_payload):
                sock.sendto(reply_payload(data), addr)
            elif reply_payload is not None:
                sock.sendto(reply_payload, addr)
    finally:
        sock.close()


def run_echo_udp_server(
    ready: threading.Event,
    stop: threading.Event,
    ports: list[int],
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    ports.append(sock.getsockname()[1])
    sock.settimeout(0.2)
    ready.set()
    try:
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            sock.sendto(data, addr)
    finally:
        sock.close()


def test_probe_voice_rejects_udp_echo_server() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_echo_udp_server,
        args=(ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=1.0)
        assert result.up is False
        assert result.detail == "unexpected-reply"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_correct_reply() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_fake_udp_server,
        args=(build_init1_step1_reply, ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=1.0)
        assert result.up is True
        assert result.rtt_seconds > 0.0
        assert result.detail == "up"
        assert result.ip == "127.0.0.1"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_rejects_old_wrong_layout_server() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    old_wrong_reply = INIT1_MAC + struct.pack(">HHB", 101, 1, 0x88) + b"\x00" * 16
    thread = threading.Thread(
        target=run_fake_udp_server,
        args=(old_wrong_reply, ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=1.0)
        assert result.up is False
        assert result.detail == "unexpected-reply"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_rejects_truncated_reply_server() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    truncated_reply = INIT1_MAC + struct.pack(">HB", 101, 0x88) + b"\x01" + b"\x00" * 10
    thread = threading.Thread(
        target=run_fake_udp_server,
        args=(truncated_reply, ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=1.0)
        assert result.up is False
        assert result.detail == "unexpected-reply"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_garbage_reply() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_fake_udp_server,
        args=(b"SOMETHING_ELSE_ENTIRELY", ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=1.0)
        assert result.up is False
        assert result.rtt_seconds == 0.0
        assert result.detail == "unexpected-reply"
        assert result.ip == "127.0.0.1"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_silent_port_timeout() -> None:
    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_fake_udp_server,
        args=(None, ready, stop, ports),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        result = probe_voice("127.0.0.1", port, timeout=0.1)
        assert result.up is False
        assert result.rtt_seconds == 0.0
        assert result.detail == "no-response"
        assert result.ip == "127.0.0.1"
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_probe_voice_address_lookup_failure() -> None:
    result = probe_voice("invalid.nonexistent.example.invalid", 9987, timeout=0.2)

    assert result.up is False
    assert result.rtt_seconds == 0.0
    assert result.detail.startswith("address-lookup-failed:")


def test_parse_srv_records() -> None:
    raw = """
    ; comment line
    cname.example.com.
    10 20 9987 target1.example.com.
    20 10 9988 target2.example.com.
    0 0 0 .
    10 10 0 invalidport.example.com.
    """
    records = parse_srv_records(raw)

    assert records == [
        SrvRecord(priority=10, weight=20, port=9987, target="target1.example.com"),
        SrvRecord(priority=20, weight=10, port=9988, target="target2.example.com"),
    ]


def test_select_srv() -> None:
    r1 = SrvRecord(10, 50, 9987, "target-a.example.com")
    r2 = SrvRecord(10, 80, 9987, "target-b.example.com")
    r3 = SrvRecord(20, 100, 9987, "target-c.example.com")

    assert select_srv([r1, r2, r3]) == r2
    assert select_srv([r1, r3]) == r1
    assert select_srv([]) is None


def test_find_dig_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    with pytest.raises(ValueError, match="dig was not found on PATH"):
        find_dig()


def test_srv_lookup_with_fake_dig(tmp_path: Path) -> None:
    fake_dig = tmp_path / "fake_dig"
    fake_dig.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "+short" ]; then\n'
        '  echo "10 60 9987 relay.example.com."\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_dig.chmod(0o755)

    record = srv_lookup(str(fake_dig), "_ts3._udp.voice.example.com", "", timeout=2.0)

    assert record == SrvRecord(10, 60, 9987, "relay.example.com")


def test_srv_lookup_passes_dns_server(tmp_path: Path) -> None:
    args_file = tmp_path / "args.txt"
    fake_dig = tmp_path / "fake_dig"
    fake_dig.write_text(
        f"#!/bin/sh\n"
        f'echo "$@" > "{args_file}"\n'
        f'echo "10 10 9987 direct.example.com."\n'
        f"exit 0\n",
        encoding="utf-8",
    )
    fake_dig.chmod(0o755)

    srv_lookup(
        str(fake_dig),
        "_ts3._udp.voice.example.com",
        "192.0.2.53",
        timeout=2.0,
    )
    captured = args_file.read_text(encoding="utf-8")

    assert "@192.0.2.53" in captured


def test_srv_lookup_errors(tmp_path: Path) -> None:
    fake_dig_fail = tmp_path / "fake_dig_fail"
    fake_dig_fail.write_text("#!/bin/sh\necho 'connection timed out' >&2\nexit 9\n")
    fake_dig_fail.chmod(0o755)

    with pytest.raises(SrvLookupError, match="dig exited 9: connection timed out"):
        srv_lookup(str(fake_dig_fail), "name", "", timeout=1.0)

    fake_dig_empty = tmp_path / "fake_dig_empty"
    fake_dig_empty.write_text("#!/bin/sh\nexit 0\n")
    fake_dig_empty.chmod(0o755)

    with pytest.raises(SrvLookupError, match="no SRV record for name"):
        srv_lookup(str(fake_dig_empty), "name", "", timeout=1.0)


def test_probe_public_handling(tmp_path: Path) -> None:
    fake_dig = tmp_path / "fake_dig"
    fake_dig.write_text(
        "#!/bin/sh\necho '10 10 9987 127.0.0.1.'\nexit 0\n",
        encoding="utf-8",
    )
    fake_dig.chmod(0o755)

    res = probe_public("voice.example.com", str(fake_dig), "", timeout=0.1)

    assert res.address == "voice.example.com"
    assert res.srv == SrvRecord(10, 10, 9987, "127.0.0.1")
    assert res.probe.up is False

    fake_dig_fail = tmp_path / "fake_dig_fail"
    fake_dig_fail.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_dig_fail.chmod(0o755)

    res_fail = probe_public("voice.example.com", str(fake_dig_fail), "", timeout=0.1)

    assert res_fail.srv is None
    assert "srv-lookup-failed:" in res_fail.probe.detail


def test_derive_faults_and_verdicts() -> None:
    assert derive_faults(local_up=True, public_up=True) == (False, False)
    assert verdict(local_up=True, public_up=True) == "ok"

    assert derive_faults(local_up=True, public_up=False) == (True, False)
    assert verdict(local_up=True, public_up=False) == "tunnel-or-dns-fault"

    assert derive_faults(local_up=False, public_up=True) == (False, True)
    assert verdict(local_up=False, public_up=True) == "server-fault"

    assert derive_faults(local_up=False, public_up=False) == (False, True)
    assert verdict(local_up=False, public_up=False) == "server-fault"


def test_label_value_and_sample() -> None:
    assert label_value('foo "bar" \\\n baz') == 'foo \\"bar\\" \\\\\\n baz'
    assert sample("test_metric", {}, "123") == "test_metric 123"
    assert (
        sample("test_metric", {"server": "voice1", "id": "1"}, "42")
        == 'test_metric{server="voice1",id="1"} 42'
    )


def test_render_metrics() -> None:
    srv = SrvRecord(10, 10, 9987, "relay.example.com")
    server = ServerSettings(
        name="primary",
        address="primary.example.com",
        local_port=9987,
        query_port=10011,
        query_username="serveradmin",
        query_password_env="TS_PASSWORD",
    )
    report = ServerReport(
        server=server,
        local=ProbeResult(True, 0.0012, "up", "127.0.0.1"),
        public=PublicResult(
            "primary.example.com",
            srv,
            ProbeResult(True, 0.0154, "up", "192.0.2.10"),
        ),
        query=QueryResult(True, {"teamspeak_clients_online": 7}),
    )

    rendered = render_metrics([report], duration_seconds=0.05, timestamp=1700000000.0)

    assert "# HELP teamspeak_local_up" in rendered
    assert "# TYPE teamspeak_local_up gauge" in rendered
    assert (
        'teamspeak_local_up{server="primary",address="primary.example.com",'
        'port="9987"} 1' in rendered
    )
    assert (
        'teamspeak_public_up{server="primary",address="primary.example.com",'
        'relay="relay.example.com:9987"} 1'
    ) in rendered
    assert (
        'teamspeak_tunnel_fault{server="primary",address="primary.example.com"} 0'
        in rendered
    )
    assert (
        'teamspeak_server_fault{server="primary",address="primary.example.com"} 0'
        in rendered
    )
    assert (
        'teamspeak_query_up{server="primary",address="primary.example.com"} 1'
        in rendered
    )
    assert (
        'teamspeak_clients_online{server="primary",address="primary.example.com"} 7'
        in rendered
    )
    assert "teamspeak_probe_duration_seconds 0.050000" in rendered
    assert "teamspeak_last_probe_timestamp_seconds 1700000000" in rendered


def test_validate_output_path(tmp_path: Path) -> None:
    valid = tmp_path / "teamspeak.prom"
    validate_output_path(valid)

    with pytest.raises(ValueError, match=r"must end in \.prom"):
        validate_output_path(tmp_path / "teamspeak.txt")

    with pytest.raises(ValueError, match="directory does not exist"):
        validate_output_path(tmp_path / "missing_dir" / "teamspeak.prom")


def test_write_textfile_atomic(tmp_path: Path) -> None:
    prom_path = tmp_path / "teamspeak.prom"
    body = "# HELP test gauge\n# TYPE test gauge\ntest 1\n"

    write_textfile(prom_path, body)

    assert prom_path.read_text(encoding="utf-8") == body
    assert oct(prom_path.stat().st_mode)[-3:] == "644"

    # Verify no leftover temporary files in directory
    files = list(tmp_path.iterdir())
    assert files == [prom_path]


def test_ts3_escape() -> None:
    raw = r"Space here\backslash/slash|pipe"
    escaped = ts3_escape(raw)

    assert escaped == r"Space\shere\\backslash\/slash\ppipe"


def run_fake_teamspeak_query_server(
    ready: threading.Event,
    stop: threading.Event,
    ports: list[int],
    expected_password: str,
    fail_login: bool = False,
) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    ports.append(listener.getsockname()[1])
    listener.settimeout(0.5)
    listener.listen(1)
    ready.set()
    try:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                conn.sendall(b"TS3\n\r\n")
                reader = conn.makefile("rb")
                try:
                    login_line = reader.readline().decode("utf-8")
                    if fail_login:
                        conn.sendall(
                            b"error id=520 msg=invalid\\slogin\\sname\\sor\\spassword\n"
                        )
                        continue
                    if f"client_login_password={expected_password}" not in login_line:
                        conn.sendall(b"error id=1 msg=bad\\spassword\n")
                        continue
                    conn.sendall(b"error id=0 msg=ok\n")
                    use_line = reader.readline().decode("utf-8")
                    if "use sid=" in use_line:
                        conn.sendall(b"error id=0 msg=ok\n")
                    info_line = reader.readline().decode("utf-8")
                    if "serverinfo" in info_line:
                        conn.sendall(
                            b"virtualserver_clientsonline=15 "
                            b"virtualserver_channelsonline=6 "
                            b"virtualserver_uptime=43200 "
                            b"virtualserver_maxclients=32\n"
                            b"error id=0 msg=ok\n"
                        )
                    reader.readline()  # quit
                finally:
                    reader.close()
    finally:
        listener.close()


def test_server_query_retrieves_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "SuperSecretPassword456!"
    monkeypatch.setenv("TS_TEST_QUERY_PASSWORD", password)

    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_fake_teamspeak_query_server,
        args=(ready, stop, ports, password, False),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        server = ServerSettings(
            name="test-server",
            address="voice.example.com",
            local_port=9987,
            query_port=port,
            query_username="serveradmin",
            query_password_env="TS_TEST_QUERY_PASSWORD",
        )
        res = query_server(server, "127.0.0.1", timeout=2.0)
        assert res is not None
        assert res.up is True
        assert res.values == {
            "teamspeak_clients_online": 15,
            "teamspeak_channels_online": 6,
            "teamspeak_uptime_seconds": 43200,
            "teamspeak_max_clients": 32,
        }
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_server_query_only_reads_password_from_named_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TS_UNSET_PASS_VAR", raising=False)
    server = ServerSettings(
        name="test-server",
        address="voice.example.com",
        local_port=9987,
        query_port=10011,
        query_username="serveradmin",
        query_password_env="TS_UNSET_PASS_VAR",
    )
    # When env var is unset or empty, it must not attempt a connection
    res = query_server(server, "127.0.0.1", timeout=1.0)

    assert res is not None
    assert res.up is False
    assert "TS_UNSET_PASS_VAR" in res.detail
    assert "is empty or unset" in res.detail


def test_server_query_failure_does_not_leak_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_pass = "NeverLeakThisSecretValue!"
    monkeypatch.setenv("TS_QUERY_SECRET_ENV", secret_pass)

    ready = threading.Event()
    stop = threading.Event()
    ports: list[int] = []
    thread = threading.Thread(
        target=run_fake_teamspeak_query_server,
        args=(ready, stop, ports, secret_pass, True),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=2.0)
    port = ports[0]
    try:
        server = ServerSettings(
            name="test-server",
            address="voice.example.com",
            local_port=9987,
            query_port=port,
            query_username="serveradmin",
            query_password_env="TS_QUERY_SECRET_ENV",
        )
        res = query_server(server, "127.0.0.1", timeout=2.0)
        assert res is not None
        assert res.up is False
        assert "invalid login name or password" in res.detail
        assert secret_pass not in res.detail
    finally:
        stop.set()
        thread.join(timeout=1.0)


def test_server_query_disabled_returns_none() -> None:
    server = ServerSettings(
        name="voice",
        address="voice.example.com",
        local_port=9987,
        query_port=0,
    )

    assert query_server(server, "127.0.0.1", timeout=1.0) is None


def test_read_server_info_non_ts3_greeting() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)

    def handle():
        conn, _ = listener.accept()
        with conn:
            conn.sendall(b"SSH-2.0-OpenSSH_8.9p1\n")

    t = threading.Thread(target=handle, daemon=True)
    t.start()
    try:
        with pytest.raises(QueryFailed, match="did not identify as ServerQuery"):
            read_server_info("127.0.0.1", port, "admin", "pass", 1, timeout=1.0)
    finally:
        listener.close()
        t.join(timeout=1.0)


def test_parse_settings_valid() -> None:
    payload = {
        "probe": {
            "local_host": "127.0.0.1",
            "timeout_seconds": 3.5,
            "dns_server": "192.0.2.1",
        },
        "metrics": {"output_path": "/tmp/test.prom"},
        "servers": [
            {
                "name": "srv1",
                "address": "srv1.example.com",
                "local_port": 9987,
                "query_port": 10011,
                "query_username": "serveradmin",
                "query_password_env": "SRV1_PASS",
                "virtual_server_id": 2,
            }
        ],
    }
    settings = parse_settings(payload)

    assert settings.local_host == "127.0.0.1"
    assert settings.timeout == 3.5
    assert settings.dns_server == "192.0.2.1"
    assert settings.output_path == Path("/tmp/test.prom")
    assert len(settings.servers) == 1
    assert settings.servers[0].name == "srv1"
    assert settings.servers[0].virtual_server_id == 2


def test_parse_settings_duplicate_server_names() -> None:
    payload = {
        "servers": [
            {"name": "srv1", "address": "a.example.com", "local_port": 9987},
            {"name": "srv1", "address": "b.example.com", "local_port": 9988},
        ]
    }
    with pytest.raises(ValueError, match="server names must be unique"):
        parse_settings(payload)


def test_parse_server_validation_errors() -> None:
    with pytest.raises(ValueError, match="must be a TOML table"):
        parse_server("not-a-dict", 0)

    with pytest.raises(ValueError, match=r"servers\[0\]\.name must be"):
        parse_server({"name": "", "address": "a.example.com", "local_port": 9987}, 0)

    with pytest.raises(ValueError, match="query_username is required"):
        parse_server(
            {
                "name": "srv",
                "address": "a.example.com",
                "local_port": 9987,
                "query_port": 10011,
            },
            0,
        )

    with pytest.raises(ValueError, match="query_password_env must name an environment"):
        parse_server(
            {
                "name": "srv",
                "address": "a.example.com",
                "local_port": 9987,
                "query_port": 10011,
                "query_username": "admin",
                "query_password_env": "123-bad-name!",
            },
            0,
        )


def test_validate_hostname() -> None:
    assert validate_hostname("voice.example.com", "host") == "voice.example.com"
    assert validate_hostname("192.0.2.1", "host") == "192.0.2.1"

    with pytest.raises(ValueError, match="must be a hostname or IPv4 address"):
        validate_hostname("-invalid", "host")
    with pytest.raises(ValueError, match="must be a hostname or IPv4 address"):
        validate_hostname("+invalid", "host")
    with pytest.raises(ValueError, match="must be a hostname or IPv4 address"):
        validate_hostname("space not allowed", "host")


def test_validate_dns_server() -> None:
    assert validate_dns_server("", "dns") == ""
    assert validate_dns_server("192.0.2.53", "dns") == "192.0.2.53"
    assert validate_dns_server("dns.example.com", "dns") == "dns.example.com"


def test_resolve_settings_cli_overrides() -> None:
    base = ProbeSettings(
        local_host="127.0.0.1",
        timeout=4.0,
        dns_server="",
        output_path=Path("/default/path.prom"),
    )
    args = argparse.Namespace(
        local_host="192.0.2.20",
        timeout=8.0,
        dns_server="192.0.2.53",
        output=Path("/cli/path.prom"),
    )

    resolved = resolve_settings(base, args)

    assert resolved.local_host == "192.0.2.20"
    assert resolved.timeout == 8.0
    assert resolved.dns_server == "192.0.2.53"
    assert resolved.output_path == Path("/cli/path.prom")


def test_resolve_settings_cli_none_preserves_base() -> None:
    base = ProbeSettings(
        local_host="127.0.0.1",
        timeout=4.0,
        dns_server="192.0.2.53",
        output_path=Path("/default/path.prom"),
    )
    args = argparse.Namespace(
        local_host=None,
        timeout=None,
        dns_server=None,
        output=None,
    )

    resolved = resolve_settings(base, args)

    assert resolved.local_host == "127.0.0.1"
    assert resolved.timeout == 4.0
    assert resolved.dns_server == "192.0.2.53"
    assert resolved.output_path == Path("/default/path.prom")


def test_configure_writes_new_configuration(tmp_path: Path) -> None:
    target = tmp_path / "config.local.toml"
    exit_code = CONF_MODULE.main(
        [
            "--output",
            str(target),
            "--server",
            "voice1",
            "voice1.example.com",
            "9987",
            "--server",
            "voice2",
            "voice2.example.com",
            "9988",
        ]
    )

    assert exit_code == 0
    assert target.is_file()
    settings = load_settings(target)
    assert len(settings.servers) == 2
    assert settings.servers[0].name == "voice1"
    assert settings.servers[1].name == "voice2"


def test_configure_refuses_to_overwrite_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.local.toml"
    target.write_text("# existing content\n", encoding="utf-8")

    exit_code = CONF_MODULE.main(["--output", str(target)])

    assert exit_code == 1
    assert target.read_text(encoding="utf-8") == "# existing content\n"
    captured = capsys.readouterr()
    assert "refusing to replace existing file" in captured.err


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    for cmd in (
        ["--help"],
        ["check", "--help"],
        ["metrics", "--help"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(cmd)
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "usage:" in captured.out

    with pytest.raises(SystemExit) as exc_conf:
        CONF_MODULE.main(["--help"])
    assert exc_conf.value.code == 0


def test_check_exit_code_zero_when_all_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "probe_voice",
        lambda host, port, timeout: ProbeResult(True, 0.001, "up", "127.0.0.1"),
    )
    exit_code = main(["check", "--local", "9987", "9988"])

    assert exit_code == 0


def test_check_exit_code_two_when_any_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(host: str, port: int, timeout: float) -> ProbeResult:
        if port == 9987:
            return ProbeResult(True, 0.001, "up", "127.0.0.1")
        return ProbeResult(False, 0.0, "no-response", "127.0.0.1")

    monkeypatch.setattr(MODULE, "probe_voice", fake_probe)
    exit_code = main(["check", "--local", "9987", "9988"])

    assert exit_code == 2


def test_check_input_error_exit_code_one(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["check"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "input-error:" in captured.err


def test_metrics_stdout_exit_code_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.local.toml"
    config.write_text(
        """
        [probe]
        local_host = "127.0.0.1"

        [[servers]]
        name = "test"
        address = "test.example.com"
        local_port = 9987
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "find_dig", lambda: "/usr/bin/dig")
    monkeypatch.setattr(
        MODULE,
        "probe_voice",
        lambda host, port, timeout: ProbeResult(True, 0.001, "up", "127.0.0.1"),
    )
    monkeypatch.setattr(
        MODULE,
        "probe_public",
        lambda address, dig, dns, timeout: PublicResult(
            address,
            SrvRecord(10, 10, 9987, "127.0.0.1"),
            ProbeResult(True, 0.002, "up", "127.0.0.1"),
        ),
    )

    exit_code = main(["--config", str(config), "metrics", "--stdout"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "teamspeak_local_up" in captured.out


def test_bad_arguments_exit_code_two() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--nonexistent-flag"])
    assert exc.value.code == 2


def test_metrics_missing_password_env_exits_zero_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TS_UNSET_VAR", raising=False)
    config = tmp_path / "config.local.toml"
    config.write_text(
        """
        [probe]
        local_host = "127.0.0.1"

        [[servers]]
        name = "test"
        address = "test.example.com"
        local_port = 9987
        query_port = 10011
        query_username = "serveradmin"
        query_password_env = "TS_UNSET_VAR"
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "find_dig", lambda: "/usr/bin/dig")
    monkeypatch.setattr(
        MODULE,
        "probe_voice",
        lambda host, port, timeout: ProbeResult(True, 0.001, "up", "127.0.0.1"),
    )
    monkeypatch.setattr(
        MODULE,
        "probe_public",
        lambda address, dig, dns, timeout: PublicResult(
            address,
            SrvRecord(10, 10, 9987, "127.0.0.1"),
            ProbeResult(True, 0.002, "up", "127.0.0.1"),
        ),
    )

    exit_code = main(["--config", str(config), "metrics", "--stdout"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert (
        'teamspeak_query_up{server="test",address="test.example.com"} 0' in captured.out
    )
    assert (
        "warning: test ServerQuery: environment variable 'TS_UNSET_VAR' "
        "is empty or unset" in captured.err
    )
