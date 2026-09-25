#!/usr/bin/env python3
"""Poll UniFi Traffic Flows and forward CIM-named events to Splunk HEC."""

from __future__ import annotations

import argparse
import base64
import http.client
import http.cookiejar
import ipaddress
import json
import logging
import os
import re
import signal
import ssl
import sys
import threading
import time
import tomllib
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

try:
    import fcntl
except ImportError:  # Windows has no flock; the single-writer lock is skipped there.
    fcntl = None

DEFAULT_SITE = "default"
DEFAULT_API_KEY_ENV = "UNIFI_API_KEY"
DEFAULT_HEC_URL = "https://127.0.0.1:8088/services/collector/event"
DEFAULT_HEC_TOKEN_ENV = "SPLUNK_HEC_TOKEN"
DEFAULT_SOURCETYPE = "unifi:flow"
DEFAULT_SOURCE = "unifi:traffic-flows"
DEFAULT_TIMEOUT = 60.0
DEFAULT_POLL_INTERVAL = 120
DEFAULT_LOOKBACK = 900
DEFAULT_PAGE_SIZE = 1000
DEFAULT_STATE_PATH = Path("checkpoint.json")

# The endpoint requires every array filter in the request body. Empty arrays
# disable server-side filtering, so every flow in the window comes back.
QUERY_ARRAYS = (
    "risk",
    "action",
    "direction",
    "protocol",
    "service",
    "source_mac",
    "source_ip",
    "source_host",
    "source_network_id",
    "destination_domain",
    "destination_ip",
    "destination_region",
    "policy",
    "policy_type",
    "source_port",
    "source_domain",
    "source_zone_id",
    "source_region",
    "destination_host",
    "destination_mac",
    "destination_port",
    "destination_network_id",
    "destination_zone_id",
    "in_network_id",
    "out_network_id",
    "next_ai_query",
    "except_for",
)

# Each poll re-reads this far behind the newest flow already sent, so a record
# the controller publishes late is still collected. Flow IDs drop the repeats.
OVERLAP_SECONDS = 300
# Bound on remembered flow IDs. At about 10,000 flows an hour, one overlap
# window holds roughly 850 IDs, so this keeps many windows of history.
SEEN_LIMIT = 40000
MAX_PAGES = 200
HEC_BATCH = 250
FIRST_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 600

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_POLL_FAILED = 3

USER_AGENT = "tools-and-scripts/1"
ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SITE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

LOG = logging.getLogger("unifi-flow-collector")

FlowId = str | int


@dataclass(frozen=True)
class UniFiSettings:
    """Controller connection settings that never contain credential values."""

    controller_url: str = ""
    site: str = DEFAULT_SITE
    api_key_env: str = DEFAULT_API_KEY_ENV
    username: str = ""
    password_env: str = ""
    verify_tls: bool = True
    ca_file: Path | None = None
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class HecSettings:
    """HEC connection and event metadata that never contain the token."""

    url: str = DEFAULT_HEC_URL
    token_env: str = DEFAULT_HEC_TOKEN_ENV
    index: str = ""
    sourcetype: str = DEFAULT_SOURCETYPE
    source: str = DEFAULT_SOURCE
    event_host: str = ""
    verify_tls: bool = True
    ca_file: Path | None = None
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class CollectorSettings:
    poll_interval: int = DEFAULT_POLL_INTERVAL
    lookback: int = DEFAULT_LOOKBACK
    page_size: int = DEFAULT_PAGE_SIZE
    state_path: Path = DEFAULT_STATE_PATH


@dataclass(frozen=True)
class ToolConfig:
    unifi: UniFiSettings
    hec: HecSettings
    collector: CollectorSettings


@dataclass(frozen=True)
class PollResult:
    fetched: int
    new: int
    written: int
    window_seconds: int


class RemoteError(RuntimeError):
    """A controller or HEC failure whose message never carries a credential."""


class HttpStatusError(RemoteError):
    def __init__(self, label: str, code: int, body: bytes) -> None:
        super().__init__(f"{label} returned HTTP {code}")
        self.code = code
        self.body = body


class SessionRejected(RemoteError):
    pass


class RejectRedirects(HTTPRedirectHandler):
    """Keep API keys, session cookies, and HEC tokens on the configured endpoint."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class EventSink(Protocol):
    def send(self, envelopes: list[dict[str, Any]]) -> None: ...


# Configuration parsing


def _reject_unknown(table: dict[str, Any], allowed: Iterable[str], name: str) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ValueError(f"[{name}] has unknown keys: {', '.join(unknown)}")


def _string(value: Any, field: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _boolean(value: Any, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _positive_int(value: Any, field: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _positive_float(value: Any, field: str, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return float(value)


def _local_path(value: Any, field: str, base: Path) -> Path | None:
    text = _string(value, field, "")
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base / path


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_url(url: str, field: str, *, base_only: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field} must not contain a query or fragment")
    try:
        parsed.port  # noqa: B018 - the property raises on an invalid port
    except ValueError:
        raise ValueError(f"{field} has an invalid port") from None
    if base_only and parsed.path not in {"", "/"}:
        raise ValueError(f"{field} must hold only the scheme, host, and optional port")
    # A credential sent over plain HTTP can be read on the wire, so HTTP is
    # accepted only when the connection never leaves this machine.
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
        raise ValueError(f"{field} may use plain HTTP only for a loopback address")


def _validate_env_name(value: str, field: str) -> None:
    if not ENVIRONMENT_NAME.fullmatch(value):
        raise ValueError(f"{field} must be a valid environment-variable name")


def _validate_tls(url: str, verify: bool, ca_file: Path | None, prefix: str) -> None:
    if ca_file is not None and not verify:
        raise ValueError(f"{prefix}.ca_file requires {prefix}.verify_tls = true")
    if ca_file is not None and urlparse(url).scheme != "https":
        raise ValueError(f"{prefix}.ca_file requires an HTTPS URL")


def validate_unifi(settings: UniFiSettings) -> None:
    validate_url(settings.controller_url, "unifi.controller_url", base_only=True)
    if not SITE_NAME.fullmatch(settings.site):
        raise ValueError("unifi.site must contain only letters, digits, ., _, or -")
    session_fields = bool(settings.username) or bool(settings.password_env)
    if settings.api_key_env and session_fields:
        raise ValueError(
            "unifi.api_key_env and local-account sign-in are mutually exclusive"
        )
    if settings.api_key_env:
        _validate_env_name(settings.api_key_env, "unifi.api_key_env")
    elif not (settings.username and settings.password_env):
        raise ValueError(
            "set unifi.api_key_env, or set both unifi.username and unifi.password_env"
        )
    else:
        _validate_env_name(settings.password_env, "unifi.password_env")
    _validate_tls(
        settings.controller_url, settings.verify_tls, settings.ca_file, "unifi"
    )
    if settings.timeout <= 0:
        raise ValueError("unifi.timeout_seconds must be greater than zero")


def validate_hec(settings: HecSettings) -> None:
    validate_url(settings.url, "hec.url", base_only=False)
    _validate_env_name(settings.token_env, "hec.token_env")
    if any(character.isspace() for character in settings.index):
        raise ValueError("hec.index must not contain whitespace")
    if not settings.sourcetype:
        raise ValueError("hec.sourcetype must not be empty")
    if not settings.source:
        raise ValueError("hec.source must not be empty")
    _validate_tls(settings.url, settings.verify_tls, settings.ca_file, "hec")
    if settings.timeout <= 0:
        raise ValueError("hec.timeout_seconds must be greater than zero")


def validate_collector(settings: CollectorSettings) -> None:
    if settings.poll_interval <= 0:
        raise ValueError("collector.poll_interval_seconds must be greater than zero")
    if settings.lookback <= 0:
        raise ValueError("collector.lookback_seconds must be greater than zero")
    if settings.page_size <= 0:
        raise ValueError("collector.page_size must be greater than zero")
    if not str(settings.state_path):
        raise ValueError("collector.state_path must not be empty")


def parse_config(
    payload: Any, base_directory: Path, *, validate: bool = True
) -> ToolConfig:
    """Parse TOML content; relative paths resolve from the config directory."""

    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    _reject_unknown(payload, ("unifi", "hec", "collector"), "root")
    tables: dict[str, dict[str, Any]] = {}
    for name in ("unifi", "hec", "collector"):
        table = payload.get(name, {})
        if not isinstance(table, dict):
            raise ValueError(f"[{name}] must be a TOML table")
        tables[name] = table
    unifi, hec, collector = tables["unifi"], tables["hec"], tables["collector"]
    base = base_directory.resolve()

    _reject_unknown(
        unifi,
        (
            "controller_url",
            "site",
            "api_key_env",
            "username",
            "password_env",
            "verify_tls",
            "ca_file",
            "timeout_seconds",
        ),
        "unifi",
    )
    _reject_unknown(
        hec,
        (
            "url",
            "token_env",
            "index",
            "sourcetype",
            "source",
            "event_host",
            "verify_tls",
            "ca_file",
            "timeout_seconds",
        ),
        "hec",
    )
    _reject_unknown(
        collector,
        ("poll_interval_seconds", "lookback_seconds", "page_size", "state_path"),
        "collector",
    )

    state_path = _local_path(
        collector.get("state_path"), "collector.state_path", base
    ) or (base / DEFAULT_STATE_PATH)
    config = ToolConfig(
        unifi=UniFiSettings(
            controller_url=_string(
                unifi.get("controller_url"), "unifi.controller_url", ""
            ).rstrip("/"),
            site=_string(unifi.get("site"), "unifi.site", DEFAULT_SITE),
            api_key_env=_string(
                unifi.get("api_key_env"), "unifi.api_key_env", DEFAULT_API_KEY_ENV
            ),
            username=_string(unifi.get("username"), "unifi.username", ""),
            password_env=_string(unifi.get("password_env"), "unifi.password_env", ""),
            verify_tls=_boolean(unifi.get("verify_tls"), "unifi.verify_tls", True),
            ca_file=_local_path(unifi.get("ca_file"), "unifi.ca_file", base),
            timeout=_positive_float(
                unifi.get("timeout_seconds"), "unifi.timeout_seconds", DEFAULT_TIMEOUT
            ),
        ),
        hec=HecSettings(
            url=_string(hec.get("url"), "hec.url", DEFAULT_HEC_URL),
            token_env=_string(
                hec.get("token_env"), "hec.token_env", DEFAULT_HEC_TOKEN_ENV
            ),
            index=_string(hec.get("index"), "hec.index", ""),
            sourcetype=_string(
                hec.get("sourcetype"), "hec.sourcetype", DEFAULT_SOURCETYPE
            ),
            source=_string(hec.get("source"), "hec.source", DEFAULT_SOURCE),
            event_host=_string(hec.get("event_host"), "hec.event_host", ""),
            verify_tls=_boolean(hec.get("verify_tls"), "hec.verify_tls", True),
            ca_file=_local_path(hec.get("ca_file"), "hec.ca_file", base),
            timeout=_positive_float(
                hec.get("timeout_seconds"), "hec.timeout_seconds", DEFAULT_TIMEOUT
            ),
        ),
        collector=CollectorSettings(
            poll_interval=_positive_int(
                collector.get("poll_interval_seconds"),
                "collector.poll_interval_seconds",
                DEFAULT_POLL_INTERVAL,
            ),
            lookback=_positive_int(
                collector.get("lookback_seconds"),
                "collector.lookback_seconds",
                DEFAULT_LOOKBACK,
            ),
            page_size=_positive_int(
                collector.get("page_size"), "collector.page_size", DEFAULT_PAGE_SIZE
            ),
            state_path=state_path,
        ),
    )
    if validate:
        validate_unifi(config.unifi)
        validate_hec(config.hec)
        validate_collector(config.collector)
    return config


def load_config(path: Path, *, validate: bool = True) -> ToolConfig:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return parse_config(payload, path.parent, validate=validate)


def _pick(override: Any, current: Any) -> Any:
    return current if override is None else override


def resolve_config(config: ToolConfig, args: argparse.Namespace) -> ToolConfig:
    """Apply command-line overrides on top of the local configuration."""

    unifi = config.unifi
    session_override = args.username is not None or args.password_env is not None
    if args.api_key_env is not None and session_override:
        raise ValueError(
            "--api-key-env cannot be combined with --username or --password-env"
        )
    if args.api_key_env is not None:
        unifi = replace(
            unifi, api_key_env=args.api_key_env, username="", password_env=""
        )
    elif session_override:
        unifi = replace(
            unifi,
            api_key_env="",
            username=_pick(args.username, unifi.username),
            password_env=_pick(args.password_env, unifi.password_env),
        )
    unifi = replace(
        unifi,
        controller_url=_pick(args.controller_url, unifi.controller_url).rstrip("/"),
        site=_pick(args.site, unifi.site),
        ca_file=(
            Path(args.unifi_ca_file).expanduser()
            if args.unifi_ca_file is not None
            else unifi.ca_file
        ),
    )
    hec = replace(
        config.hec,
        url=_pick(args.hec_url, config.hec.url),
        token_env=_pick(args.hec_token_env, config.hec.token_env),
        index=_pick(args.index, config.hec.index),
        sourcetype=_pick(args.sourcetype, config.hec.sourcetype),
        ca_file=(
            Path(args.hec_ca_file).expanduser()
            if args.hec_ca_file is not None
            else config.hec.ca_file
        ),
    )
    collector = replace(
        config.collector,
        poll_interval=_pick(args.poll_interval, config.collector.poll_interval),
        state_path=(
            Path(args.state_file).expanduser()
            if args.state_file is not None
            else config.collector.state_path
        ),
    )
    validate_unifi(unifi)
    validate_hec(hec)
    validate_collector(collector)
    return ToolConfig(unifi=unifi, hec=hec, collector=collector)


def credential(env_name: str) -> str:
    value = os.environ.get(env_name, "")
    if not value:
        raise ValueError(f"environment variable {env_name!r} is empty or unset")
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        raise ValueError(
            f"environment variable {env_name!r} must contain printable ASCII"
        )
    return value


# HTTP helpers


def tls_context(verify: bool, ca_file: Path | None, label: str) -> ssl.SSLContext:
    if ca_file is not None and not ca_file.is_file():
        raise ValueError(f"{label} CA file not found: {ca_file}")
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def open_request(
    opener: OpenerDirector, request: Request, timeout: float, label: str
) -> tuple[Any, bytes]:
    """Return response headers and body, turning every failure into RemoteError."""

    try:
        with opener.open(request, timeout=timeout) as response:
            return response.headers, response.read()
    except HTTPError as exc:
        try:
            body = exc.read(4096)
        except (OSError, http.client.HTTPException):
            body = b""
        raise HttpStatusError(label, exc.code, body) from None
    except (URLError, OSError, http.client.HTTPException):
        raise RemoteError(
            f"{label} request failed; check connectivity and TLS"
        ) from None


def csrf_from_token_cookie(jar: http.cookiejar.CookieJar) -> str | None:
    """Read the CSRF value that older UniFi OS releases carry in the TOKEN JWT."""

    token = next((cookie.value for cookie in jar if cookie.name == "TOKEN"), None)
    if not token or token.count(".") < 2:
        return None
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    value = data.get("csrfToken") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


class UniFiClient:
    """Traffic-flow reader using an API key or a local-account session."""

    def __init__(self, settings: UniFiSettings, secret: str) -> None:
        self.settings = settings
        self.host = urlparse(settings.controller_url).hostname or ""
        self._secret = secret
        self._context = (
            tls_context(settings.verify_tls, settings.ca_file, "UniFi")
            if urlparse(settings.controller_url).scheme == "https"
            else None
        )
        self._opener: OpenerDirector | None = None
        self._jar = http.cookiejar.CookieJar()
        self._csrf: str | None = None

    @property
    def uses_api_key(self) -> bool:
        return bool(self.settings.api_key_env)

    def reset(self) -> None:
        """Drop the session so the next request signs in again."""

        self._opener = None
        self._csrf = None

    def _new_opener(self) -> OpenerDirector:
        self._jar = http.cookiejar.CookieJar()
        handlers: list[Any] = [RejectRedirects(), HTTPCookieProcessor(self._jar)]
        if self._context is not None:
            handlers.append(HTTPSHandler(context=self._context))
        return build_opener(*handlers)

    def login(self) -> None:
        opener = self._new_opener()
        body = json.dumps(
            {
                "username": self.settings.username,
                "password": self._secret,
                "rememberMe": True,
            }
        ).encode()
        request = Request(
            f"{self.settings.controller_url}/api/auth/login",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            headers, _ = open_request(
                opener, request, self.settings.timeout, "UniFi sign-in"
            )
        except HttpStatusError as exc:
            raise RemoteError(f"UniFi sign-in failed: HTTP {exc.code}") from None
        self._csrf = (
            headers.get("X-Updated-CSRF-Token")
            or headers.get("X-CSRF-Token")
            or csrf_from_token_cookie(self._jar)
        )
        self._opener = opener
        LOG.info("signed in to UniFi controller at %s", self.host)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        if self.uses_api_key:
            return self._send(path, payload)
        if self._opener is None:
            self.login()
        try:
            return self._send(path, payload)
        except SessionRejected:
            LOG.info("UniFi session rejected, signing in again")
            self.login()
            return self._send(path, payload)

    def _send(self, path: str, payload: dict[str, Any]) -> Any:
        if self._opener is None:
            self._opener = self._new_opener()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.uses_api_key:
            headers["X-API-KEY"] = self._secret
        elif self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        request = Request(
            f"{self.settings.controller_url}{path}",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            response_headers, body = open_request(
                self._opener, request, self.settings.timeout, "UniFi"
            )
        except HttpStatusError as exc:
            if exc.code in {401, 403}:
                if self.uses_api_key:
                    raise RemoteError(
                        f"UniFi rejected the API key: HTTP {exc.code}"
                    ) from None
                raise SessionRejected(
                    f"UniFi rejected the session: HTTP {exc.code}"
                ) from None
            raise
        updated = response_headers.get("X-Updated-CSRF-Token")
        if updated:
            self._csrf = updated
        try:
            return json.loads(body)
        except ValueError:
            raise RemoteError("UniFi returned a response that is not JSON") from None


def _hec_detail(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    # Response text can reflect authorization headers. Only numeric codes are safe.
    code = payload.get("code")
    return f": code={code}" if type(code) is int else ""


class HecSink:
    """Send event envelopes to Splunk HEC and require code 0 for every batch."""

    def __init__(self, settings: HecSettings, token: str) -> None:
        self.settings = settings
        self._token = token
        handlers: list[Any] = [RejectRedirects()]
        if urlparse(settings.url).scheme == "https":
            handlers.append(
                HTTPSHandler(
                    context=tls_context(settings.verify_tls, settings.ca_file, "HEC")
                )
            )
        self._opener = build_opener(*handlers)

    def send(self, envelopes: list[dict[str, Any]]) -> None:
        payload = "".join(json.dumps(envelope) for envelope in envelopes).encode()
        request = Request(
            self.settings.url,
            data=payload,
            headers={
                "Authorization": f"Splunk {self._token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            _, body = open_request(
                self._opener, request, self.settings.timeout, "Splunk HEC"
            )
        except HttpStatusError as exc:
            raise RemoteError(
                f"Splunk HEC returned HTTP {exc.code}{_hec_detail(exc.body)}"
            ) from None
        try:
            result = json.loads(body)
        except ValueError:
            result = None
        if (
            not isinstance(result, dict)
            or type(result.get("code")) is not int
            or result["code"] != 0
        ):
            raise RemoteError(f"Splunk HEC rejected the batch{_hec_detail(body)}")


class StdoutSink:
    """Print event envelopes as JSON lines instead of sending them."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def send(self, envelopes: list[dict[str, Any]]) -> None:
        for envelope in envelopes:
            self._stream.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        self._stream.flush()


# Field mapping


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _name_of(side: dict[str, Any]) -> Any:
    """Best human label for a flow endpoint, falling back to the address."""

    for key in ("client_name", "device_name", "host_name"):
        value = side.get(key)
        if value:
            return value
    return side.get("ip")


def _domains(value: Any) -> Any:
    domains = [value] if isinstance(value, str) else value
    if not isinstance(domains, list) or not domains:
        return None
    # Multivalue in Splunk; the first entry is the label Insights shows.
    return domains if len(domains) > 1 else domains[0]


def _collapse(values: list[Any]) -> Any:
    # One value stays scalar so `stats by rule` groups the way a reader expects.
    unique = list(
        dict.fromkeys(
            value for value in values if isinstance(value, str | int) and value != ""
        )
    )
    if not unique:
        return None
    return unique[0] if len(unique) == 1 else unique


def flatten(flow: dict[str, Any], controller: str) -> dict[str, Any]:
    """Map one controller flow record onto flat, CIM-named fields.

    CIM Network_Traffic uses src, dest, bytes, packets, transport, app, and
    action. UniFi's allowed and blocked values already match the CIM action
    vocabulary, so they pass through unchanged. UniFi-only attributes such as
    risk, zones, region, and policy keep their own names.
    """

    src = _mapping(flow.get("source"))
    dst = _mapping(flow.get("destination"))
    traffic = _mapping(flow.get("traffic_data"))
    inbound = _mapping(flow.get("in"))
    outbound = _mapping(flow.get("out"))
    raw_policies = flow.get("policies")
    policies = [
        policy
        for policy in (raw_policies if isinstance(raw_policies, list) else [])
        if isinstance(policy, dict)
    ]
    protocol = flow.get("protocol")

    event: dict[str, Any] = {
        "flow_id": flow.get("id"),
        "action": flow.get("action"),
        "risk": flow.get("risk"),
        "app": flow.get("service"),
        "service": flow.get("service"),
        "transport": protocol.lower() if isinstance(protocol, str) else None,
        "protocol": protocol,
        "direction": flow.get("direction"),
        "count": flow.get("count"),
        "vendor_product": "UniFi Network",
        "dvc": controller,
        "src": _name_of(src),
        "src_ip": src.get("ip"),
        "src_port": src.get("port"),
        "src_mac": src.get("mac"),
        "src_name": src.get("client_name"),
        "src_host": src.get("host_name"),
        "src_device": src.get("device_name"),
        "src_device_model": src.get("device_model"),
        "src_oui": src.get("client_oui"),
        "src_network": src.get("network_name"),
        "src_subnet": src.get("subnet"),
        "src_zone": src.get("zone_name"),
        "src_region": src.get("region"),
        "dest": _name_of(dst),
        "dest_ip": dst.get("ip"),
        "dest_port": dst.get("port"),
        "dest_mac": dst.get("mac"),
        "dest_name": dst.get("client_name"),
        "dest_host": dst.get("host_name"),
        "dest_device": dst.get("device_name"),
        "dest_device_model": dst.get("device_model"),
        "dest_oui": dst.get("client_oui"),
        "dest_network": dst.get("network_name"),
        "dest_subnet": dst.get("subnet"),
        "dest_zone": dst.get("zone_name"),
        "dest_region": dst.get("region"),
        "in_network": inbound.get("network_name"),
        "out_network": outbound.get("network_name"),
        "bytes": traffic.get("bytes_total"),
        "bytes_in": traffic.get("bytes_rx"),
        "bytes_out": traffic.get("bytes_tx"),
        "packets": traffic.get("packets_total"),
        "packets_in": traffic.get("packets_rx"),
        "packets_out": traffic.get("packets_tx"),
        "dest_domain": _domains(dst.get("domains")),
        "src_domain": _domains(src.get("domains")),
    }

    duration_ms = flow.get("duration_milliseconds")
    if _is_number(duration_ms):
        event["duration_ms"] = duration_ms
        event["duration"] = round(duration_ms / 1000.0, 3)
    for key, field in (
        ("flow_start_time", "flow_start"),
        ("flow_end_time", "flow_end"),
    ):
        if _is_number(flow.get(key)):
            event[field] = flow[key] / 1000.0

    if policies:

        def values(key: str) -> list[Any]:
            return [
                policy[key]
                for policy in policies
                if isinstance(policy.get(key), str | int | float) and policy.get(key)
            ]

        event["rule"] = _collapse(
            [
                policy.get("name") or policy.get("internal_type")
                for policy in policies
                if isinstance(policy.get("name") or policy.get("internal_type"), str)
                and (policy.get("name") or policy.get("internal_type"))
            ]
        )
        event["policy_type"] = _collapse(values("type"))
        event["policy_internal_type"] = _collapse(values("internal_type"))
        # An IPS match carries the signature category that classified the flow.
        # It is the only place the reason for a high risk band is stated.
        event["ips_category"] = _collapse(values("ips_category"))

    # Blocked flows are what Insights highlights, so they get a direct flag.
    event["is_blocked"] = 1 if flow.get("action") == "blocked" else 0
    event["is_external"] = (
        1 if "External" in (src.get("zone_name"), dst.get("zone_name")) else 0
    )

    # Only fields with a value reach the index.
    return {
        key: value
        for key, value in event.items()
        if value is not None and value != "" and value != []
    }


def hec_envelope(
    time_seconds: float,
    event: dict[str, Any],
    settings: HecSettings,
    default_host: str,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {"time": time_seconds}
    host = settings.event_host or default_host
    if host:
        envelope["host"] = host
    envelope["source"] = settings.source
    envelope["sourcetype"] = settings.sourcetype
    if settings.index:
        envelope["index"] = settings.index
    envelope["event"] = event
    return envelope


# Checkpoint


def _is_flow_id(value: Any) -> bool:
    return isinstance(value, str | int) and not isinstance(value, bool) and value != ""


def _checkpoint_fields(data: Any) -> tuple[int, list[FlowId]]:
    # Both keys are required so an unrelated JSON file named by mistake is
    # never treated as a checkpoint and overwritten.
    if not isinstance(data, dict) or set(data) != {"last_time_ms", "seen"}:
        raise ValueError("expected an object with only last_time_ms and seen")
    last = data["last_time_ms"]
    if not isinstance(last, int) or isinstance(last, bool) or last < 0:
        raise ValueError("last_time_ms must be a non-negative integer")
    seen = data["seen"]
    if not isinstance(seen, list) or not all(_is_flow_id(item) for item in seen):
        raise ValueError("seen must be a list of flow IDs")
    return last, seen


class Checkpoint:
    """Newest collected flow time plus a bounded set of flow IDs already sent."""

    def __init__(
        self, path: Path, last_time_ms: int = 0, seen: Iterable[FlowId] = ()
    ) -> None:
        self.path = path
        self.last_time_ms = last_time_ms
        self.seen: OrderedDict[FlowId, None] = OrderedDict.fromkeys(seen)
        self._trim()

    @classmethod
    def load(cls, path: Path, *, quarantine_invalid: bool) -> Checkpoint:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            LOG.info("no checkpoint at %s, starting from the lookback window", path)
            return cls(path)
        try:
            last, seen = _checkpoint_fields(json.loads(raw))
        except ValueError as exc:
            if not quarantine_invalid:
                LOG.warning(
                    "ignoring invalid checkpoint %s for this run: %s", path, exc
                )
                return cls(path)
            # The unreadable file is kept for inspection instead of overwritten.
            aside = path.with_name(f"{path.name}.invalid-{time.time_ns()}")
            os.replace(path, aside)
            LOG.warning(
                "checkpoint %s is invalid (%s); moved it to %s and starting from "
                "the lookback window",
                path,
                exc,
                aside,
            )
            return cls(path)
        LOG.info("resumed checkpoint: last_time_ms=%s seen=%d", last, len(seen))
        return cls(path, last, seen)

    def _trim(self) -> None:
        while len(self.seen) > SEEN_LIMIT:
            self.seen.popitem(last=False)

    def is_new(self, flow_id: FlowId) -> bool:
        return flow_id not in self.seen

    def mark_sent(self, flow_id: FlowId) -> None:
        self.seen[flow_id] = None
        self._trim()

    def advance(self, time_ms: int) -> None:
        self.last_time_ms = max(self.last_time_ms, time_ms)

    def save(self) -> None:
        payload = {"last_time_ms": self.last_time_ms, "seen": list(self.seen)}
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            # One rename leaves either the old or the new checkpoint on disk,
            # never a partial file.
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()


def require_state_directory(state_path: Path) -> None:
    if state_path.is_dir():
        raise ValueError(f"state path is a directory: {state_path}")
    parent = state_path.parent
    if not parent.is_dir():
        raise ValueError(f"state directory does not exist: {parent}")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError(f"state directory is not writable: {parent}")


@contextmanager
def state_lock(state_path: Path) -> Iterator[None]:
    """Hold an exclusive lock so two collectors never share one checkpoint."""

    lock_path = state_path.with_name(f"{state_path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    f"another collector holds the checkpoint lock: {lock_path}"
                ) from None
        yield


# Polling


def window_start_ms(checkpoint: Checkpoint, now_ms: int, lookback: int) -> int:
    if checkpoint.last_time_ms:
        return checkpoint.last_time_ms - OVERLAP_SECONDS * 1000
    return now_ms - lookback * 1000


def flow_page(envelope: Any) -> list[dict[str, Any]]:
    if not isinstance(envelope, dict):
        raise RemoteError("traffic-flows response is not a JSON object")
    data = envelope.get("data")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise RemoteError("traffic-flows response has no data list of flow objects")
    return data


def fetch_new_flows(
    client: UniFiClient,
    checkpoint: Checkpoint,
    settings: CollectorSettings,
    site: str,
    start_ms: int,
    now_ms: int,
) -> tuple[int, list[tuple[FlowId, int, dict[str, Any]]]]:
    path = f"/proxy/network/v2/api/site/{quote(site, safe='')}/traffic-flows"
    pending: list[tuple[FlowId, int, dict[str, Any]]] = []
    in_poll: set[FlowId] = set()
    fetched = 0

    for page in range(MAX_PAGES):
        query: dict[str, Any] = {field: [] for field in QUERY_ARRAYS}
        query.update(
            {
                "timestampFrom": start_ms,
                "timestampTo": now_ms,
                "pageNumber": page,
                "pageSize": settings.page_size,
                "search_text": "",
                "skip_count": False,
            }
        )
        envelope = client.post(path, query)
        flows = flow_page(envelope)
        fetched += len(flows)

        for flow in flows:
            flow_id = flow.get("id")
            time_ms = flow.get("time")
            if not _is_flow_id(flow_id) or not _is_number(time_ms):
                continue
            if flow_id in in_poll or not checkpoint.is_new(flow_id):
                continue
            in_poll.add(flow_id)
            pending.append((flow_id, int(time_ms), flatten(flow, client.host)))

        if not envelope.get("has_next") or not flows:
            break
    else:
        raise RemoteError("traffic-flow page cap reached; checkpoint unchanged")
    return fetched, pending


def deliver(
    pending: list[tuple[FlowId, int, dict[str, Any]]],
    sink: EventSink,
    checkpoint: Checkpoint,
    settings: HecSettings,
    default_host: str,
    *,
    persist: bool,
) -> int:
    written = 0
    try:
        for start in range(0, len(pending), HEC_BATCH):
            batch = pending[start : start + HEC_BATCH]
            sink.send(
                [
                    hec_envelope(time_ms / 1000.0, event, settings, default_host)
                    for _, time_ms, event in batch
                ]
            )
            for flow_id, _, _ in batch:
                checkpoint.mark_sent(flow_id)
            written += len(batch)
    except Exception:
        # IDs from accepted batches are kept so a retry doesn't send them again.
        # The newest-time mark stays where it was, so the retry window still
        # covers every flow that HEC did not accept.
        if persist and written:
            with suppress(OSError):
                checkpoint.save()
        raise
    for _, time_ms, _ in pending:
        checkpoint.advance(time_ms)
    if persist:
        checkpoint.save()
    return written


def poll_once(
    client: UniFiClient,
    sink: EventSink,
    checkpoint: Checkpoint,
    config: ToolConfig,
    *,
    persist: bool,
) -> PollResult:
    now_ms = int(time.time() * 1000)
    start_ms = window_start_ms(checkpoint, now_ms, config.collector.lookback)
    fetched, pending = fetch_new_flows(
        client, checkpoint, config.collector, config.unifi.site, start_ms, now_ms
    )
    written = deliver(
        pending, sink, checkpoint, config.hec, client.host, persist=persist
    )
    result = PollResult(
        fetched=fetched,
        new=len(pending),
        written=written,
        window_seconds=(now_ms - start_ms) // 1000,
    )
    LOG.info(
        "poll: fetched=%d new=%d written=%d window=%ds",
        result.fetched,
        result.new,
        result.written,
        result.window_seconds,
    )
    return result


def next_backoff(current: int) -> int:
    return min(current * 2 or FIRST_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS)


def run(
    client: UniFiClient,
    sink: EventSink,
    checkpoint: Checkpoint,
    config: ToolConfig,
    *,
    once: bool,
    persist: bool,
    stop: threading.Event,
) -> int:
    LOG.info(
        "starting: site=%s interval=%ds page_size=%d index=%s output=%s",
        config.unifi.site,
        config.collector.poll_interval,
        config.collector.page_size,
        config.hec.index or "token-default",
        "hec" if persist else "stdout",
    )
    backoff = 0
    while not stop.is_set():
        started = time.monotonic()
        try:
            poll_once(client, sink, checkpoint, config, persist=persist)
            backoff = 0
        except BrokenPipeError:
            raise
        except Exception as exc:  # keep the service alive across outages
            if once:
                LOG.error("poll failed: %s", exc)
                return EXIT_POLL_FAILED
            backoff = next_backoff(backoff)
            LOG.error("poll failed (%s); retrying in %ds", exc, backoff)
            # A stale session is a common cause, so the next attempt signs in.
            client.reset()
        if once:
            break
        elapsed = time.monotonic() - started
        stop.wait(backoff or max(1.0, config.collector.poll_interval - elapsed))
    LOG.info("stopped")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="local TOML configuration copied from config.example.toml",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one poll and exit; a failed poll exits with status 3",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--send",
        dest="dry_run",
        action="store_false",
        help="send to HEC and persist the checkpoint; default is preview",
    )
    output.add_argument(
        "--dry-run",
        "--stdout",
        dest="dry_run",
        action="store_true",
        default=True,
        help=(
            "print mapped HEC events as JSON lines; send nothing to HEC and "
            "leave the checkpoint unchanged"
        ),
    )
    unifi = parser.add_argument_group("UniFi overrides")
    unifi.add_argument(
        "--controller-url", help="UniFi console base URL, such as https://192.0.2.1"
    )
    unifi.add_argument("--site", help="UniFi Network site name")
    unifi.add_argument(
        "--api-key-env",
        help="name of the environment variable holding the UniFi API key",
    )
    unifi.add_argument(
        "--username", help="sign in with this local account instead of an API key"
    )
    unifi.add_argument(
        "--password-env",
        help="name of the environment variable holding the local account password",
    )
    unifi.add_argument("--unifi-ca-file", help="PEM file that verifies the console")
    hec = parser.add_argument_group("Splunk HEC overrides")
    hec.add_argument("--hec-url", help="full HEC event endpoint URL")
    hec.add_argument(
        "--hec-token-env",
        help="name of the environment variable holding the HEC token",
    )
    hec.add_argument("--hec-ca-file", help="PEM file that verifies HEC")
    hec.add_argument("--index", help="destination index; empty uses the token default")
    hec.add_argument("--sourcetype", help="sourcetype written on each event")
    collector = parser.add_argument_group("collection overrides")
    collector.add_argument("--poll-interval", type=int, help="seconds between polls")
    collector.add_argument("--state-file", help="checkpoint file path")
    return parser


def _silence_stdout() -> None:
    with suppress(OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def collect(args: argparse.Namespace, stop: threading.Event) -> int:
    try:
        config = resolve_config(load_config(args.config, validate=False), args)
        client = UniFiClient(
            config.unifi,
            credential(config.unifi.api_key_env or config.unifi.password_env),
        )
        sink: EventSink
        if args.dry_run:
            sink = StdoutSink(sys.stdout)
        else:
            sink = HecSink(config.hec, credential(config.hec.token_env))
            require_state_directory(config.collector.state_path)
    except (OSError, ValueError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    state_path = config.collector.state_path
    with ExitStack() as stack:
        try:
            if not args.dry_run:
                stack.enter_context(state_lock(state_path))
            checkpoint = Checkpoint.load(
                state_path, quarantine_invalid=not args.dry_run
            )
        except (OSError, ValueError) as exc:
            print(f"input-error: {exc}", file=sys.stderr)
            return EXIT_INPUT
        try:
            return run(
                client,
                sink,
                checkpoint,
                config,
                once=args.once,
                persist=not args.dry_run,
                stop=stop,
            )
        except BrokenPipeError:
            # The reader of a preview closed the pipe, such as `| head`.
            _silence_stdout()
            return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOG.info("received signal %s, finishing the current poll then exiting", signum)
        stop.set()

    previous = {
        number: signal.signal(number, request_stop)
        for number in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return collect(args, stop)
    finally:
        for number, handler_before in previous.items():
            signal.signal(number, handler_before)
        LOG.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
