#!/usr/bin/env python3
"""Probe TeamSpeak 3 voice endpoints with a real Init1 handshake."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, BinaryIO

DEFAULT_LOCAL_HOST = "127.0.0.1"
DEFAULT_TIMEOUT = 4.0
DEFAULT_VIRTUAL_SERVER_ID = 1
INIT1_MAC = b"TS3INIT1"
INIT1_CLIENT_VERSION = bytes([0x09, 0x83, 0x8C, 0xCF])
DIG_MISSING = (
    "dig was not found on PATH. SRV lookups need it; install your distribution's "
    "DNS utilities package, for example bind9-dnsutils, dnsutils, or bind-utils"
)

HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}\.?$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?)*\.?$"
)
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SRV_LINE_PATTERN = re.compile(r"^(\d+)\s+(\d+)\s+(\d+)\s+(\S+)$")

TS3_ESCAPES = {
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

QUERY_FIELDS = (
    ("virtualserver_clientsonline", "teamspeak_clients_online"),
    ("virtualserver_channelsonline", "teamspeak_channels_online"),
    ("virtualserver_uptime", "teamspeak_uptime_seconds"),
    ("virtualserver_maxclients", "teamspeak_max_clients"),
)

METRICS = (
    (
        "teamspeak_local_up",
        "TeamSpeak voice answered the Init1 handshake on the local UDP port.",
    ),
    (
        "teamspeak_local_rtt_seconds",
        "Round trip of the local Init1 handshake, 0 when it failed.",
    ),
    (
        "teamspeak_public_up",
        "TeamSpeak voice answered at the SRV target users connect to.",
    ),
    (
        "teamspeak_public_rtt_seconds",
        "Round trip of the public Init1 handshake, 0 when it failed.",
    ),
    (
        "teamspeak_dns_srv_up",
        "The _ts3._udp SRV record resolved to a target and port.",
    ),
    (
        "teamspeak_tunnel_fault",
        "Local voice is up but the public address is not, so the tunnel, relay, "
        "or DNS is at fault.",
    ),
    (
        "teamspeak_server_fault",
        "The local voice service is not answering.",
    ),
    (
        "teamspeak_query_up",
        "ServerQuery login succeeded and serverinfo returned.",
    ),
    (
        "teamspeak_clients_online",
        "Clients online from ServerQuery, including query sessions.",
    ),
    ("teamspeak_channels_online", "Channels present, from ServerQuery."),
    ("teamspeak_uptime_seconds", "Virtual server uptime, from ServerQuery."),
    ("teamspeak_max_clients", "Configured client slots, from ServerQuery."),
    ("teamspeak_probe_duration_seconds", "Wall time of one full collection."),
    (
        "teamspeak_last_probe_timestamp_seconds",
        "Unix time of the last completed collection.",
    ),
)


class SrvLookupError(Exception):
    """An SRV lookup that produced no usable record."""


class QueryFailed(Exception):
    """A ServerQuery failure whose message never contains the sent command."""


@dataclass(frozen=True)
class ServerSettings:
    name: str
    address: str
    local_port: int
    query_port: int = 0
    query_username: str = ""
    query_password_env: str = ""
    virtual_server_id: int = DEFAULT_VIRTUAL_SERVER_ID


@dataclass(frozen=True)
class ProbeSettings:
    local_host: str = DEFAULT_LOCAL_HOST
    timeout: float = DEFAULT_TIMEOUT
    dns_server: str = ""
    output_path: Path | None = None
    servers: tuple[ServerSettings, ...] = ()


@dataclass(frozen=True, order=True)
class SrvRecord:
    priority: int
    weight: int
    port: int
    target: str


@dataclass(frozen=True)
class ProbeResult:
    up: bool
    rtt_seconds: float
    detail: str
    ip: str = ""


@dataclass(frozen=True)
class PublicResult:
    address: str
    srv: SrvRecord | None
    probe: ProbeResult


@dataclass(frozen=True)
class QueryResult:
    up: bool
    values: dict[str, int] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class ServerReport:
    server: ServerSettings
    local: ProbeResult
    public: PublicResult
    query: QueryResult | None


def build_init1_step0(
    timestamp: int | None = None, nonce: bytes | None = None
) -> bytes:
    """Return a TeamSpeak 3 Init1 step-0 packet."""

    stamp = int(time.time()) if timestamp is None else timestamp
    random_part = os.urandom(4) if nonce is None else nonce
    if len(random_part) != 4:
        raise ValueError("Init1 nonce must be exactly four bytes")
    return (
        INIT1_MAC
        + struct.pack(">HHB", 101, 0, 0x88)
        + INIT1_CLIENT_VERSION
        + b"\x00"
        + struct.pack(">I", stamp & 0xFFFFFFFF)
        + random_part
        + b"\x00" * 8
    )


# Citation: TeamSpeak 3 Protocol Paper (ReSpeak/tsdeclarations)
# URL: https://github.com/ReSpeak/tsdeclarations/blob/master/ts3protocol.md
# Sections:
# - Section 1.1.2: (Client <- Server) packet structure: MAC (8 bytes),
#   Packet ID (2 bytes, 0x0065), Packet Type + Flags (1 byte, 0x88), no Client ID.
# - Section 1.2 & 1.3: Packet Type 0x08 (Init1) with Unencrypted flag 0x80 -> 0x88.
# - Section 2: The (Low-Level) Initiation/Handshake (Packet ID 101 / 0x0065).
# - Section 2.2: Packet 1 (Client <- Server): Step 0x01 (1 byte),
#   Server data (16 bytes), reversed client random bytes [A0r] (4 bytes).
#   Total reply length: 11 header bytes + 21 body bytes = 32 bytes.
def is_init1_reply(data: bytes, request: bytes | None = None) -> bool:
    """Validate a TeamSpeak 3 Init1 step-1 server reply against documented layout."""
    if request is not None and data == request:
        return False
    if len(data) < 32:
        return False
    if not data.startswith(INIT1_MAC):
        return False
    if data[8:10] != b"\x00\x65":
        return False
    if data[10] != 0x88:
        return False
    if data[11] != 0x01:
        return False
    if request is not None and len(request) >= 26:
        expected_nonce = request[22:26][::-1]
        if data[28:32] != expected_nonce and data[-4:] != expected_nonce:
            return False
    return True


def probe_voice(host: str, port: int, timeout: float) -> ProbeResult:
    """Send one Init1 step-0 packet and wait for a TS3INIT1 reply."""

    try:
        address = socket.gethostbyname(host)
    except OSError as exc:
        return ProbeResult(False, 0.0, f"address-lookup-failed: {exc}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        started = time.monotonic()
        try:
            # A connected UDP socket only accepts datagrams from the probed
            # address, so a stray packet from another sender can't pass.
            sock.connect((address, port))
            request = build_init1_step0()
            sock.send(request)
            data = sock.recv(2048)
        except TimeoutError:
            return ProbeResult(False, 0.0, "no-response", address)
        except OSError as exc:
            return ProbeResult(False, 0.0, f"error: {exc.strerror or exc}", address)
        elapsed = time.monotonic() - started
    if not is_init1_reply(data, request):
        return ProbeResult(False, 0.0, "unexpected-reply", address)
    return ProbeResult(True, elapsed, "up", address)


def parse_srv_records(text: str) -> list[SrvRecord]:
    """Parse `dig +short SRV` output, ignoring CNAME and comment lines."""

    records: list[SrvRecord] = []
    for line in text.splitlines():
        match = SRV_LINE_PATTERN.match(line.strip())
        if match is None:
            continue
        priority, weight, port = (int(match.group(index)) for index in (1, 2, 3))
        target = match.group(4).rstrip(".")
        # A target of "." means the service is explicitly not offered.
        if not target or not 0 < port <= 65535:
            continue
        records.append(SrvRecord(priority, weight, port, target))
    return records


def select_srv(records: list[SrvRecord]) -> SrvRecord | None:
    """Pick the lowest priority, then the highest weight, deterministically."""

    return min(
        records,
        key=lambda record: (record.priority, -record.weight, record.target),
        default=None,
    )


def find_dig() -> str:
    path = shutil.which("dig")
    if path is None:
        raise ValueError(DIG_MISSING)
    return path


def srv_lookup(dig: str, name: str, dns_server: str, timeout: float) -> SrvRecord:
    query_time = max(1, round(timeout))
    command = [dig, "+short", f"+time={query_time}", "+tries=1"]
    if dns_server:
        command.append(f"@{dns_server}")
    command.extend(("-t", "SRV", "-q", name))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=query_time * 3 + 2,
        )
    except subprocess.TimeoutExpired:
        raise SrvLookupError(f"dig timed out resolving {name}") from None
    except OSError as exc:
        raise SrvLookupError(f"dig could not run: {exc}") from None
    if completed.returncode != 0:
        detail = next(
            (
                line.strip().lstrip(";").strip()
                for line in (completed.stdout + completed.stderr).splitlines()
                if line.strip()
            ),
            "no output",
        )
        raise SrvLookupError(f"dig exited {completed.returncode}: {detail}")
    record = select_srv(parse_srv_records(completed.stdout))
    if record is None:
        raise SrvLookupError(f"no SRV record for {name}")
    return record


def probe_public(
    address: str, dig: str, dns_server: str, timeout: float
) -> PublicResult:
    name = f"_ts3._udp.{address.rstrip('.')}"
    try:
        record = srv_lookup(dig, name, dns_server, timeout)
    except SrvLookupError as exc:
        return PublicResult(
            address, None, ProbeResult(False, 0.0, f"srv-lookup-failed: {exc}")
        )
    return PublicResult(
        address, record, probe_voice(record.target, record.port, timeout)
    )


def derive_faults(local_up: bool, public_up: bool) -> tuple[bool, bool]:
    """Return (tunnel_fault, server_fault) for one local and public pair."""

    return local_up and not public_up, not local_up


def verdict(local_up: bool, public_up: bool) -> str:
    tunnel_fault, server_fault = derive_faults(local_up, public_up)
    if server_fault:
        return "server-fault"
    if tunnel_fault:
        return "tunnel-or-dns-fault"
    return "ok"


def ts3_escape(value: str) -> str:
    return "".join(TS3_ESCAPES.get(character, character) for character in value)


def _query_line(reader: BinaryIO) -> str:
    raw = reader.readline(65536)
    if not raw:
        raise QueryFailed("ServerQuery closed the connection")
    return raw.decode("utf-8", errors="replace").strip()


def _query_command(
    sock: socket.socket, reader: BinaryIO, command: str, operation: str
) -> list[str]:
    sock.sendall((command + "\n").encode("utf-8"))
    lines: list[str] = []
    while True:
        line = _query_line(reader)
        if not line:
            continue
        if line.startswith("error "):
            fields = {}
            for token in line.split()[1:]:
                key, _, value = token.partition("=")
                fields[key] = value
            error_id = fields.get("id", "unknown")
            if error_id != "0":
                message = fields.get("msg", "unknown").replace(r"\s", " ")
                raise QueryFailed(f"{operation} failed: id={error_id} msg={message}")
            return lines
        lines.append(line)


def read_server_info(
    host: str,
    port: int,
    username: str,
    password: str,
    server_id: int,
    timeout: float,
) -> dict[str, int]:
    """Log in to ServerQuery, read serverinfo, and return the numeric fields."""

    with socket.create_connection((host, port), timeout=timeout) as sock:
        reader = sock.makefile("rb")
        try:
            if _query_line(reader) != "TS3":
                raise QueryFailed("endpoint did not identify as ServerQuery")
            _query_command(
                sock,
                reader,
                f"login client_login_name={ts3_escape(username)} "
                f"client_login_password={ts3_escape(password)}",
                "ServerQuery login",
            )
            _query_command(sock, reader, f"use sid={server_id}", "selecting server")
            lines = _query_command(sock, reader, "serverinfo", "reading serverinfo")
            with suppress(OSError):
                sock.sendall(b"quit\n")
        finally:
            reader.close()
    record: dict[str, str] = {}
    for line in lines:
        for token in line.split():
            key, separator, value = token.partition("=")
            if separator:
                record[key] = value
    return {
        metric: int(record[key])
        for key, metric in QUERY_FIELDS
        if record.get(key, "").isdigit()
    }


def query_server(
    server: ServerSettings, host: str, timeout: float
) -> QueryResult | None:
    if not server.query_port:
        return None
    password = os.environ.get(server.query_password_env, "")
    if not password:
        return QueryResult(
            False,
            detail=(
                f"environment variable {server.query_password_env!r} is empty or unset"
            ),
        )
    try:
        values = read_server_info(
            host,
            server.query_port,
            server.query_username,
            password,
            server.virtual_server_id,
            timeout,
        )
    except (OSError, QueryFailed) as exc:
        return QueryResult(False, detail=str(exc))
    return QueryResult(True, values)


def collect(settings: ProbeSettings, dig: str) -> list[ServerReport]:
    reports: list[ServerReport] = []
    for server in settings.servers:
        local = probe_voice(settings.local_host, server.local_port, settings.timeout)
        public = probe_public(
            server.address, dig, settings.dns_server, settings.timeout
        )
        query = query_server(server, settings.local_host, settings.timeout)
        reports.append(ServerReport(server, local, public, query))
    return reports


def label_value(value: str) -> str:
    return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


def sample(name: str, labels: dict[str, str], value: str) -> str:
    if not labels:
        return f"{name} {value}"
    rendered = ",".join(f'{key}="{label_value(text)}"' for key, text in labels.items())
    return f"{name}{{{rendered}}} {value}"


def render_metrics(
    reports: list[ServerReport], duration_seconds: float, timestamp: float
) -> str:
    """Render node_exporter textfile metrics with each family grouped once."""

    families: dict[str, list[str]] = {name: [] for name, _ in METRICS}
    for report in reports:
        base = {"server": report.server.name, "address": report.server.address}
        local_labels = {**base, "port": str(report.server.local_port)}
        srv = report.public.srv
        relay = f"{srv.target}:{srv.port}" if srv else "unresolved:0"
        public_labels = {**base, "relay": relay}
        tunnel_fault, server_fault = derive_faults(
            report.local.up, report.public.probe.up
        )
        entries = (
            ("teamspeak_local_up", local_labels, str(int(report.local.up))),
            (
                "teamspeak_local_rtt_seconds",
                local_labels,
                f"{report.local.rtt_seconds:.6f}",
            ),
            ("teamspeak_dns_srv_up", base, str(int(srv is not None))),
            ("teamspeak_public_up", public_labels, str(int(report.public.probe.up))),
            (
                "teamspeak_public_rtt_seconds",
                public_labels,
                f"{report.public.probe.rtt_seconds:.6f}",
            ),
            ("teamspeak_tunnel_fault", base, str(int(tunnel_fault))),
            ("teamspeak_server_fault", base, str(int(server_fault))),
        )
        for name, labels, value in entries:
            families[name].append(sample(name, labels, value))
        if report.query is not None:
            families["teamspeak_query_up"].append(
                sample("teamspeak_query_up", base, str(int(report.query.up)))
            )
            for metric, value in report.query.values.items():
                families[metric].append(sample(metric, base, str(value)))
    families["teamspeak_probe_duration_seconds"].append(
        sample("teamspeak_probe_duration_seconds", {}, f"{duration_seconds:.6f}")
    )
    families["teamspeak_last_probe_timestamp_seconds"].append(
        sample("teamspeak_last_probe_timestamp_seconds", {}, str(int(timestamp)))
    )

    lines: list[str] = []
    for name, help_text in METRICS:
        if families[name]:
            lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
            lines.extend(families[name])
    return "\n".join(lines) + "\n"


def validate_output_path(path: Path) -> None:
    # node_exporter reads only *.prom files, so any other name would be
    # written successfully and then silently never collected.
    if path.suffix != ".prom":
        raise ValueError(f"metrics output must end in .prom: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"metrics output directory does not exist: {path.parent}")


def write_textfile(path: Path, body: str) -> None:
    validate_output_path(path)
    # node_exporter can read the directory at any moment. The hidden temporary
    # file lacks the .prom suffix and sits in the same directory, so the
    # collector never sees a partial file and os.replace is atomic.
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _string(value: Any, field_name: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _port(value: Any, field_name: str, *, allow_zero: bool = False) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not (0 if allow_zero else 1) <= value <= 65535
    ):
        allowed = "0 to 65535" if allow_zero else "1 to 65535"
        raise ValueError(f"{field_name} must be an integer from {allowed}")
    return value


def _positive_float(value: Any, field_name: str, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return float(value)


def validate_hostname(value: str, field_name: str) -> str:
    # Values reach dig and socket calls. The pattern also rejects a leading
    # "-" or "+", which dig would read as an option instead of a name.
    if not HOSTNAME_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a hostname or IPv4 address: {value!r}")
    return value


def validate_dns_server(value: str, field_name: str) -> str:
    if not value:
        return value
    with suppress(ValueError):
        ipaddress.ip_address(value)
        return value
    return validate_hostname(value, field_name)


def parse_server(payload: Any, index: int) -> ServerSettings:
    prefix = f"servers[{index}]"
    if not isinstance(payload, dict):
        raise ValueError(f"{prefix} must be a TOML table")
    name = _string(payload.get("name"), f"{prefix}.name", "")
    if not SERVER_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"{prefix}.name must be 1 to 63 letters, digits, '.', '_', or '-'"
        )
    address = validate_hostname(
        _string(payload.get("address"), f"{prefix}.address", ""), f"{prefix}.address"
    )
    local_port = _port(payload.get("local_port"), f"{prefix}.local_port")
    query_port = _port(
        payload.get("query_port", 0), f"{prefix}.query_port", allow_zero=True
    )
    username = _string(payload.get("query_username"), f"{prefix}.query_username", "")
    password_env = _string(
        payload.get("query_password_env"), f"{prefix}.query_password_env", ""
    )
    server_id = payload.get("virtual_server_id", DEFAULT_VIRTUAL_SERVER_ID)
    if not isinstance(server_id, int) or isinstance(server_id, bool) or server_id < 1:
        raise ValueError(f"{prefix}.virtual_server_id must be a positive integer")
    if query_port:
        if not username:
            raise ValueError(f"{prefix}.query_username is required with query_port")
        if not ENVIRONMENT_NAME_PATTERN.fullmatch(password_env):
            raise ValueError(
                f"{prefix}.query_password_env must name an environment variable"
            )
    return ServerSettings(
        name=name,
        address=address,
        local_port=local_port,
        query_port=query_port,
        query_username=username,
        query_password_env=password_env,
        virtual_server_id=server_id,
    )


def parse_settings(payload: Any, base_directory: Path | None = None) -> ProbeSettings:
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    probe = payload.get("probe", {})
    metrics = payload.get("metrics", {})
    servers = payload.get("servers", [])
    if not isinstance(probe, dict) or not isinstance(metrics, dict):
        raise ValueError("[probe] and [metrics] must be TOML tables")
    if not isinstance(servers, list):
        raise ValueError("servers must be an array of [[servers]] tables")

    parsed_servers = tuple(
        parse_server(item, index) for index, item in enumerate(servers)
    )
    names = [server.name for server in parsed_servers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"server names must be unique: {duplicates}")

    output_text = _string(metrics.get("output_path"), "metrics.output_path", "")
    output_path = None
    if output_text:
        candidate = Path(output_text).expanduser()
        base = (base_directory or Path.cwd()).resolve()
        output_path = candidate if candidate.is_absolute() else base / candidate

    return ProbeSettings(
        local_host=validate_hostname(
            _string(probe.get("local_host"), "probe.local_host", DEFAULT_LOCAL_HOST),
            "probe.local_host",
        ),
        timeout=_positive_float(
            probe.get("timeout_seconds"), "probe.timeout_seconds", DEFAULT_TIMEOUT
        ),
        dns_server=validate_dns_server(
            _string(probe.get("dns_server"), "probe.dns_server", ""),
            "probe.dns_server",
        ),
        output_path=output_path,
        servers=parsed_servers,
    )


def load_settings(path: Path | None) -> ProbeSettings:
    if path is None:
        return ProbeSettings()
    with path.open("rb") as handle:
        return parse_settings(tomllib.load(handle), path.resolve().parent)


def resolve_settings(
    settings: ProbeSettings, args: argparse.Namespace
) -> ProbeSettings:
    resolved = replace(
        settings,
        local_host=(
            validate_hostname(args.local_host, "--local-host")
            if args.local_host is not None
            else settings.local_host
        ),
        timeout=(
            _positive_float(args.timeout, "--timeout", DEFAULT_TIMEOUT)
            if args.timeout is not None
            else settings.timeout
        ),
        dns_server=(
            validate_dns_server(args.dns_server, "--dns-server")
            if args.dns_server is not None
            else settings.dns_server
        ),
    )
    output = getattr(args, "output", None)
    if output is not None:
        resolved = replace(resolved, output_path=output.expanduser())
    return resolved


def describe(result: ProbeResult) -> str:
    if result.up:
        return f"up rtt={result.rtt_seconds * 1000:.2f}ms"
    return result.detail


def describe_public(result: PublicResult) -> str:
    if result.srv is None:
        return f"{result.address} {result.probe.detail}"
    return (
        f"{result.address} srv={result.srv.target}:{result.srv.port} "
        f"ip={result.probe.ip or 'unresolved'} {describe(result.probe)}"
    )


def run_check(settings: ProbeSettings, args: argparse.Namespace) -> int:
    ad_hoc = bool(args.public or args.local)
    if args.domain is not None and not args.public:
        raise ValueError("--domain applies only to --public names")
    public_names = [
        f"{name.rstrip('.')}.{args.domain.strip('.')}" if args.domain else name
        for name in args.public or []
    ]
    for name in public_names:
        validate_hostname(name, "--public")
    local_ports = [_port(port, "--local") for port in args.local or []]
    if not ad_hoc and not settings.servers:
        raise ValueError("no [[servers]] are configured; pass --public or --local")

    needs_dig = bool(public_names) or not ad_hoc
    dig = find_dig() if needs_dig else ""
    results: list[bool] = []

    if ad_hoc:
        for name in public_names:
            public = probe_public(name, dig, settings.dns_server, settings.timeout)
            print(f"public {describe_public(public)}")
            results.append(public.probe.up)
        for port in local_ports:
            local = probe_voice(settings.local_host, port, settings.timeout)
            print(f"local {settings.local_host}:{port} {describe(local)}")
            results.append(local.up)
    else:
        for server in settings.servers:
            local = probe_voice(
                settings.local_host, server.local_port, settings.timeout
            )
            print(
                f"{server.name} local {settings.local_host}:{server.local_port} "
                f"{describe(local)}"
            )
            public = probe_public(
                server.address, dig, settings.dns_server, settings.timeout
            )
            print(f"{server.name} public {describe_public(public)}")
            print(f"{server.name} verdict {verdict(local.up, public.probe.up)}")
            results.extend((local.up, public.probe.up))

    answered = sum(results)
    print(f"summary: {answered} of {len(results)} endpoints answered")
    return 0 if answered == len(results) else 2


def run_metrics(settings: ProbeSettings, args: argparse.Namespace) -> int:
    if not settings.servers:
        raise ValueError("metrics need at least one [[servers]] entry in --config")
    output_path = None if args.stdout else settings.output_path
    if not args.stdout:
        if output_path is None:
            raise ValueError("set metrics.output_path or pass --output")
        validate_output_path(output_path)
    dig = find_dig()

    started = time.monotonic()
    reports = collect(settings, dig)
    body = render_metrics(reports, time.monotonic() - started, time.time())
    for report in reports:
        if report.query is not None and not report.query.up:
            print(
                f"warning: {report.server.name} ServerQuery: {report.query.detail}",
                file=sys.stderr,
            )
    if output_path is None:
        sys.stdout.write(body)
        return 0
    write_textfile(output_path, body)
    print(f"metrics-written: {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="local TOML configuration copied from config.example.toml",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--local-host",
        help="override probe.local_host, the address your local voice ports use",
    )
    common.add_argument(
        "--timeout",
        type=float,
        help="override probe.timeout_seconds for each handshake and SRV lookup",
    )
    common.add_argument(
        "--dns-server",
        help="override probe.dns_server, the DNS server dig asks for SRV records",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check",
        parents=[common],
        help="probe endpoints and print one line per result",
        description="Probe endpoints and print one line per result.",
    )
    check.add_argument(
        "--public",
        nargs="+",
        metavar="ADDRESS",
        help="public addresses users type; each is resolved through _ts3._udp SRV",
    )
    check.add_argument(
        "--domain",
        help="append this domain to every --public name",
    )
    check.add_argument(
        "--local",
        nargs="+",
        type=int,
        metavar="PORT",
        help="local UDP voice ports probed on the local host",
    )

    metrics = subparsers.add_parser(
        "metrics",
        parents=[common],
        help="write node_exporter textfile metrics for every configured server",
        description="Write node_exporter textfile metrics for every configured server.",
    )
    destination = metrics.add_mutually_exclusive_group()
    destination.add_argument(
        "--output",
        type=Path,
        help="override metrics.output_path; the file name must end in .prom",
    )
    destination.add_argument(
        "--stdout",
        action="store_true",
        help="print the metrics instead of writing the textfile",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(load_settings(args.config), args)
        if args.command == "check":
            return run_check(settings, args)
        return run_metrics(settings, args)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
