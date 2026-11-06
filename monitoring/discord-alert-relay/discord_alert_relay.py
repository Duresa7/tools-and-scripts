#!/usr/bin/env python3
"""Webhook relay service transforming Grafana and Splunk alerts into Discord embeds."""

from __future__ import annotations

import argparse
import contextlib
import hmac
import http.server
import ipaddress
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("discord-alert-relay")

DEFAULT_LISTEN_ADDRESS = "127.0.0.1"
DEFAULT_LISTEN_PORT = 8080
DEFAULT_MAX_REQUEST_BYTES = 1048576  # 1 MiB
DEFAULT_SECRET_ENV = "ALERT_RELAY_SECRET"
DEFAULT_ALLOWED_SPLUNK_SOURCES = ("127.0.0.1",)
DEFAULT_MODE = "bot"
DEFAULT_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
DEFAULT_CHANNEL_ID = ""
DEFAULT_WEBHOOK_URL_ENV = "DISCORD_WEBHOOK_URL"
DEFAULT_API_BASE_URL = "https://discord.com/api/v10"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_AFTER_CAP_SECONDS = 5.0
DEFAULT_READINESS_INTERVAL_SECONDS = 60.0
DEFAULT_DEFAULT_CLASS = "infrastructure"
DEFAULT_CONDENSE_THRESHOLD = 4
DEFAULT_RESOLVED_COLOR = 0x2F9E44

DEFAULT_CLASSES: dict[str, dict[str, Any]] = {
    "infrastructure": {"prefix": "", "color": None},
    "security": {"prefix": "SECURITY ", "color": 0x7048E8},
    "updates": {"prefix": "UPDATES ", "color": 0x1C7ED6},
}

DEFAULT_SEVERITY_COLORS: dict[str, int] = {
    "critical": 0xE03131,
    "warning": 0xF08C00,
    "info": 0x1971C2,
}

FIELD_LIMIT = 1024
EMBED_LIMIT = 6000
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
URL_OK = re.compile(r"^https?://[^/\s]+\.[^/\s]+")

SHOW_LABELS = (
    "host",
    "name",
    "node",
    "id",
    "instance",
    "mountpoint",
    "device",
    "disk",
    "ups",
    "status",
)

SPLUNK_FIELDS = (
    "src",
    "src_ip",
    "dest_ip",
    "dest_port",
    "ports",
    "rule",
    "signature",
    "action",
    "count",
    "targets",
    "agent.name",
    "Machine",
    "Last",
    "Silent",
    "user",
    "rule.id",
    "rule.level",
    "rule.description",
    "syscheck.path",
    "src_zone",
    "dest_zone",
    "permalink",
)


def parse_color(value: Any) -> int | None:
    """Parse color into integer or None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lstrip("#")
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        try:
            return int(cleaned, 16)
        except ValueError:
            return None
    return None


@dataclass
class RelayConfig:
    listen_address: str = DEFAULT_LISTEN_ADDRESS
    listen_port: int = DEFAULT_LISTEN_PORT
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    secret_env: str = DEFAULT_SECRET_ENV
    allowed_splunk_sources: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_SPLUNK_SOURCES)
    )
    mode: str = DEFAULT_MODE
    bot_token_env: str = DEFAULT_BOT_TOKEN_ENV
    channel_id: str = DEFAULT_CHANNEL_ID
    webhook_url_env: str = DEFAULT_WEBHOOK_URL_ENV
    api_base_url: str = DEFAULT_API_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retry_after_cap_seconds: float = DEFAULT_RETRY_AFTER_CAP_SECONDS
    readiness_interval_seconds: float = DEFAULT_READINESS_INTERVAL_SECONDS
    default_class: str = DEFAULT_DEFAULT_CLASS
    condense_threshold: int = DEFAULT_CONDENSE_THRESHOLD
    resolved_color: int = DEFAULT_RESOLVED_COLOR
    classes: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_CLASSES.items()}
    )
    severity_colors: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SEVERITY_COLORS)
    )


def safe_url(url: str | None) -> str | None:
    """Validate that a URL is well-formed with http/https and a domain dot."""
    if url and URL_OK.match(url):
        return url
    return None


def embed_length(embed: dict[str, Any]) -> int:
    """Calculate total character count across embed fields."""
    total = len(embed.get("title") or "")
    total += len(embed.get("description") or "")
    footer = embed.get("footer")
    if isinstance(footer, dict) and "text" in footer:
        total += len(str(footer["text"]))
    author = embed.get("author")
    if isinstance(author, dict) and "name" in author:
        total += len(str(author["name"]))
    for field_item in embed.get("fields", []):
        if isinstance(field_item, dict):
            total += len(str(field_item.get("name") or ""))
            total += len(str(field_item.get("value") or ""))
    return total


def fit_embed(embed: dict[str, Any]) -> dict[str, Any]:
    """Ensure embed stays within Discord's 6000-character limit without looping.

    Non-description components are treated as a fixed floor. If the floor
    leaves remaining budget, description is truncated to fit. If the floor
    itself exceeds the limit, description is dropped, followed by fields.
    """
    if embed_length(embed) <= EMBED_LIMIT:
        return embed
    description = embed.get("description") or ""
    floor = embed_length(embed) - len(description)
    if floor < EMBED_LIMIT:
        allowed = EMBED_LIMIT - floor - 1
        embed["description"] = description[:allowed] + "…"
        return embed
    embed.pop("description", None)
    if embed_length(embed) > EMBED_LIMIT:
        embed.pop("fields", None)
    return embed


def alert_class(labels: dict[str, Any], config: RelayConfig) -> str:
    """Determine the alert class from labels or fall back to default."""
    cls = labels.get("class", config.default_class)
    return cls if cls in config.classes else config.default_class


def title_and_color(
    labels: dict[str, Any], resolved: bool, config: RelayConfig
) -> tuple[str, int]:
    """Compute embed title and color from alert metadata."""
    cls_name = alert_class(labels, config)
    style = config.classes.get(cls_name, {"prefix": "", "color": None})
    prefix = style.get("prefix", "")
    name = labels.get("alertname", "Alert")
    if resolved:
        return f"Resolved: {prefix}{name}", config.resolved_color
    severity = str(labels.get("severity", "warning")).lower()
    color = style.get("color")
    if color is None:
        color = config.severity_colors.get(
            severity, config.severity_colors.get("warning", 0xF08C00)
        )
    return f"{prefix}{severity.upper()}: {name}", color


def build_embed(alert: dict[str, Any], config: RelayConfig) -> dict[str, Any]:
    """Build a single Discord embed dictionary from a Grafana alert."""
    labels = alert.get("labels") or {}
    ann = alert.get("annotations") or {}
    resolved = alert.get("status") == "resolved"
    title, color = title_and_color(labels, resolved, config)
    embed: dict[str, Any] = {
        "title": title[:TITLE_LIMIT],
        "color": color,
    }
    summary = ann.get("summary")
    if summary:
        embed["description"] = str(summary)[:DESCRIPTION_LIMIT]
    url = safe_url(alert.get("dashboardURL") or alert.get("generatorURL"))
    if url:
        embed["url"] = url

    fields: list[dict[str, Any]] = []
    for key in SHOW_LABELS:
        if labels.get(key):
            fields.append(
                {"name": key, "value": str(labels[key])[:FIELD_LIMIT], "inline": True}
            )
    if not resolved and ann.get("description"):
        fields.append(
            {
                "name": "Detail",
                "value": str(ann["description"])[:FIELD_LIMIT],
                "inline": False,
            }
        )
    silence_url = alert.get("silenceURL")
    if silence_url and not resolved:
        value = f"[in Grafana]({silence_url})"
        if len(value) <= FIELD_LIMIT:
            fields.append({"name": "Silence", "value": value, "inline": False})
    if fields:
        embed["fields"] = fields

    folder = labels.get("grafana_folder") or "Alerts"
    embed["footer"] = {"text": f"Grafana · {folder}"}
    return fit_embed(embed)


def build_condensed_embed(
    alerts: list[dict[str, Any]], config: RelayConfig
) -> dict[str, Any]:
    """Build a condensed Discord embed when several alerts share name and status."""
    first = alerts[0]
    labels = first.get("labels") or {}
    resolved = first.get("status") == "resolved"
    title, color = title_and_color(labels, resolved, config)
    lines: list[str] = []
    for alert in alerts:
        summary = (alert.get("annotations", {}).get("summary") or "").strip()
        alert_labels = alert.get("labels") or {}
        subject = (
            alert_labels.get("host")
            or alert_labels.get("name")
            or alert_labels.get("instance")
            or ""
        )
        lines.append(f"• {summary}" if summary else f"• {subject}")

    embed: dict[str, Any] = {
        "title": f"{title} ({len(alerts)})"[:TITLE_LIMIT],
        "description": "\n".join(lines)[:DESCRIPTION_LIMIT],
        "color": color,
    }
    url = safe_url(first.get("dashboardURL") or first.get("generatorURL"))
    if url:
        embed["url"] = url
    silence_url = first.get("silenceURL")
    if silence_url and not resolved:
        value = f"[in Grafana]({silence_url})"
        if len(value) <= FIELD_LIMIT:
            embed["fields"] = [{"name": "Silence", "value": value, "inline": False}]
    folder = labels.get("grafana_folder") or "Alerts"
    embed["footer"] = {"text": f"Grafana · {folder}"}
    return fit_embed(embed)


def embeds_for(
    alerts: list[dict[str, Any]], config: RelayConfig
) -> list[dict[str, Any]]:
    """Group Grafana alerts and produce single or condensed embeds."""
    groups: OrderedDict[tuple[str | None, str | None], list[dict[str, Any]]] = (
        OrderedDict()
    )
    for alert in alerts:
        labels = alert.get("labels") or {}
        key = (labels.get("alertname"), alert.get("status"))
        groups.setdefault(key, []).append(alert)
    embeds: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) >= config.condense_threshold:
            embeds.append(build_condensed_embed(group, config))
        else:
            embeds.extend(build_embed(a, config) for a in group)
    return embeds


def build_splunk_embed(payload: dict[str, Any], config: RelayConfig) -> dict[str, Any]:
    """Build a Discord embed from a Splunk webhook alert payload."""
    result = payload.get("result") or {}
    name = payload.get("search_name") or "Splunk alert"
    source, _, what = name.partition(" - ")
    if not what:
        source, what = "Splunk", name

    cls_style = config.classes.get(
        "security", {"prefix": "SECURITY ", "color": 0x7048E8}
    )
    prefix = cls_style.get("prefix", "SECURITY ")
    color = cls_style.get("color")
    if color is None:
        color = 0x7048E8

    desc = (result.get("description") or result.get("summary") or "")[
        :DESCRIPTION_LIMIT
    ]
    embed: dict[str, Any] = {
        "title": f"{prefix}{what}"[:TITLE_LIMIT],
        "color": color,
    }
    if desc:
        embed["description"] = desc
    url = safe_url(payload.get("results_link"))
    if url:
        embed["url"] = url

    fields: list[dict[str, Any]] = []
    for key in SPLUNK_FIELDS:
        value = result.get(key)
        if value in (None, "", [], "null"):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value[:12])
        fields.append({"name": key, "value": str(value)[:FIELD_LIMIT], "inline": True})
    if fields:
        embed["fields"] = fields

    app_name = payload.get("app") or ""
    footer_parts = [p for p in ("Splunk", source, app_name) if p]
    embed["footer"] = {"text": " · ".join(footer_parts)}
    return fit_embed(embed)


def is_ip_allowed(client_ip_str: str, allowed_sources: list[str]) -> bool:
    """Check if the client IP address matches any allowed source IP or CIDR block."""
    try:
        client_addr = ipaddress.ip_address(client_ip_str)
        if isinstance(client_addr, ipaddress.IPv6Address) and client_addr.ipv4_mapped:
            client_addr = client_addr.ipv4_mapped
    except ValueError:
        return False

    for source in allowed_sources:
        try:
            network = ipaddress.ip_network(source.strip(), strict=False)
            if client_addr in network:
                return True
        except ValueError:
            continue
    return False


class DiscordClient:
    """REST-based Discord client using only Python standard library."""

    def __init__(
        self,
        config: RelayConfig,
        bot_token: str = "",
        webhook_url: str = "",
        sleep_fn: Any = time.sleep,
        on_readiness_change: Any = None,
    ):
        self.config = config
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.sleep_fn = sleep_fn
        self.on_readiness_change = on_readiness_change
        self.is_ready = False

    def set_ready(self, ready: bool) -> None:
        self.is_ready = ready
        if self.on_readiness_change is not None:
            with contextlib.suppress(Exception):
                self.on_readiness_change(ready)

    def check_ready(self) -> tuple[bool, str]:
        """Perform startup check against Discord API."""
        if self.config.mode == "bot":
            if not self.config.channel_id:
                self.set_ready(False)
                return False, "channel_id is required in bot mode"
            url = (
                f"{self.config.api_base_url.rstrip('/')}/channels/"
                f"{self.config.channel_id}"
            )
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "User-Agent": "DiscordAlertRelay/1.0",
            }
        elif self.config.mode == "webhook":
            url = self.webhook_url
            if not url:
                self.set_ready(False)
                return False, "webhook_url is required in webhook mode"
            headers = {
                "User-Agent": "DiscordAlertRelay/1.0",
            }
        else:
            self.set_ready(False)
            return False, f"unsupported delivery mode: {self.config.mode}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.timeout_seconds
            ) as resp:
                if 200 <= resp.status < 300:
                    self.set_ready(True)
                    return True, f"HTTP {resp.status}"
                self.set_ready(False)
                return False, f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            self.set_ready(False)
            return False, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.set_ready(False)
            return False, f"connection failed: {type(exc).__name__}"
        except Exception as exc:
            self.set_ready(False)
            return False, f"connection failed: {type(exc).__name__}"

    def _execute_post(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> tuple[bool, str, int | None, float | None]:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.timeout_seconds
            ) as resp:
                if 200 <= resp.status < 300:
                    return True, f"HTTP {resp.status}", resp.status, None
                return False, f"HTTP {resp.status}", resp.status, None
        except urllib.error.HTTPError as exc:
            retry_after: float | None = None
            if exc.code == 429:
                header_val = exc.headers.get("Retry-After")
                if header_val is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        retry_after = float(header_val)
                if retry_after is None:
                    with contextlib.suppress(Exception):
                        raw = exc.read()
                        body = json.loads(raw.decode("utf-8"))
                        if isinstance(body, dict) and "retry_after" in body:
                            retry_after = float(body["retry_after"])
            elif exc.code in (401, 403):
                self.set_ready(False)
            return False, f"HTTP {exc.code}", exc.code, retry_after
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.set_ready(False)
            return False, f"request failed: {type(exc).__name__}", None, None
        except Exception as exc:
            return False, f"request failed: {type(exc).__name__}", None, None

    def post_embed(self, embed: dict[str, Any]) -> tuple[bool, str]:
        """Post a single embed to Discord channel or incoming webhook."""
        data = json.dumps({"embeds": [embed]}).encode("utf-8")
        if self.config.mode == "bot":
            url = (
                f"{self.config.api_base_url.rstrip('/')}/channels/"
                f"{self.config.channel_id}/messages"
            )
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordAlertRelay/1.0",
            }
        else:
            url = self.webhook_url
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "DiscordAlertRelay/1.0",
            }

        success, detail, code, retry_after = self._execute_post(url, data, headers)
        if success:
            return True, detail

        if (
            code == 429
            and retry_after is not None
            and retry_after <= self.config.retry_after_cap_seconds
        ):
            self.sleep_fn(retry_after)
            retry_success, retry_detail, _, _ = self._execute_post(url, data, headers)
            if retry_success:
                return True, retry_detail
            return False, retry_detail

        return False, detail


class RelayHTTPHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for Grafana, Splunk, and health endpoints."""

    server: DiscordRelayServer

    def log_message(self, format_str: str, *args: Any) -> None:
        """Route standard HTTP server logs through Python logging."""
        logger.info(
            "%s - - [%s] " + format_str,
            self.client_address[0],
            self.log_date_time_string(),
            *args,
        )

    def do_GET(self) -> None:
        if self.path == "/health":
            self.handle_health()
        else:
            self.send_error_response(404, "not found")

    def do_POST(self) -> None:
        if self.path == "/grafana":
            self.handle_grafana()
        elif self.path == "/splunk":
            self.handle_splunk()
        else:
            self.send_error_response(404, "not found")

    def send_error_response(self, status: int, message: str) -> None:
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes | None:
        """Read and validate request body length against size limits."""
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self.send_error_response(411, "length required")
            return None
        try:
            content_length = int(length_header)
            if content_length < 0:
                raise ValueError
        except ValueError:
            self.send_error_response(400, "invalid content-length")
            return None

        if content_length > self.server.config.max_request_bytes:
            self.send_error_response(413, "payload too large")
            return None

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            self.send_error_response(400, "incomplete request body")
            return None
        return body

    def handle_health(self) -> None:
        """Answer GET /health with 200 if Discord API check passed, else 503."""
        if self.server.is_discord_ready:
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"discord not ready\n"
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def handle_grafana(self) -> None:
        """Handle POST /grafana with constant-time bearer secret check."""
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.secret}"
        if not (auth.startswith("Bearer ") and hmac.compare_digest(auth, expected)):
            logger.warning(
                "rejected webhook from %s: bad or missing bearer",
                self.client_address[0],
            )
            self.send_error_response(401, "unauthorized")
            return

        body = self.read_body()
        if body is None:
            return

        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except Exception:
            self.send_error_response(400, "body is not json")
            return

        if not self.server.is_discord_ready:
            logger.error("received grafana webhook but discord session not ready")
            self.send_error_response(503, "discord not ready")
            return

        alerts = payload.get("alerts") or []
        embeds = embeds_for(alerts, self.server.config)
        posted = 0
        for embed in embeds:
            success, detail = self.server.discord_client.post_embed(embed)
            if success:
                posted += 1
                logger.info("posted %s from grafana", embed.get("title", ""))
            else:
                logger.error("failed posting embed to discord: %s", detail)

        all_posted = posted == len(embeds)
        status = 200 if all_posted else 502
        resp_data = json.dumps({"posted": posted, "alerts": len(alerts)}).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)

    def handle_splunk(self) -> None:
        """Handle POST /splunk guarded by source-address allowlist."""
        client_ip = self.client_address[0]
        if not is_ip_allowed(client_ip, self.server.config.allowed_splunk_sources):
            logger.warning(
                "rejected splunk webhook from %s: address not in allowlist",
                client_ip,
            )
            self.send_error_response(403, "forbidden")
            return

        body = self.read_body()
        if body is None:
            return

        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except Exception:
            self.send_error_response(400, "body is not json")
            return

        if not self.server.is_discord_ready:
            logger.error(
                "received splunk alert %s but discord session not ready",
                payload.get("search_name"),
            )
            self.send_error_response(503, "discord not ready")
            return

        embed = build_splunk_embed(payload, self.server.config)
        success, detail = self.server.discord_client.post_embed(embed)
        posted = 1 if success else 0
        if success:
            logger.info(
                "posted %s from splunk sid=%s",
                embed.get("title", ""),
                payload.get("sid", ""),
            )
        else:
            logger.error("failed posting splunk embed to discord: %s", detail)

        status = 200 if success else 502
        resp_data = json.dumps({"posted": posted, "alerts": 1}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)


class DiscordRelayServer(http.server.ThreadingHTTPServer):
    """Threading HTTP server managing relay state and Discord readiness."""

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[http.server.BaseHTTPRequestHandler],
        config: RelayConfig,
        discord_client: DiscordClient,
        secret: str,
    ):
        super().__init__(server_address, RequestHandlerClass)
        self.config = config
        self.discord_client = discord_client
        self.secret = secret
        self.is_discord_ready = False
        self._lock = threading.Lock()
        self._poller_thread: threading.Thread | None = None
        self._poller_shutdown: threading.Event | None = None

        self.discord_client.on_readiness_change = self.set_discord_ready

    def set_discord_ready(self, ready: bool) -> None:
        with self._lock:
            self.is_discord_ready = ready
        self.discord_client.is_ready = ready

    def check_discord_api(self) -> bool:
        """Execute Discord API startup check and record readiness."""
        ready, detail = self.discord_client.check_ready()
        self.set_discord_ready(ready)
        if ready:
            logger.info("discord api check succeeded: %s", detail)
        else:
            logger.warning("discord api check failed: %s", detail)
        return ready

    def start_poller(self, interval: float | None = None) -> None:
        if self._poller_thread is not None and self._poller_thread.is_alive():
            return
        poll_interval = (
            interval if interval is not None else self.config.readiness_interval_seconds
        )
        self._poller_shutdown = threading.Event()
        shutdown_evt = self._poller_shutdown

        def readiness_worker() -> None:
            while not shutdown_evt.wait(poll_interval):
                self.check_discord_api()

        self._poller_thread = threading.Thread(
            target=readiness_worker, daemon=True, name="readiness-poller"
        )
        self._poller_thread.start()

    def stop_poller(self) -> None:
        if self._poller_shutdown is not None:
            self._poller_shutdown.set()
        if self._poller_thread is not None:
            self._poller_thread.join(timeout=2.0)
            self._poller_thread = None

    def server_close(self) -> None:
        self.stop_poller()
        super().server_close()


def load_config(path: Path | None = None) -> RelayConfig:
    """Load configuration from TOML file or return defaults."""
    config = RelayConfig()
    if path is None:
        local_path = Path("config.local.toml")
        if local_path.is_file():
            path = local_path
        else:
            tool_local = Path(__file__).with_name("config.local.toml")
            if tool_local.is_file():
                path = tool_local

    if path is None or not path.is_file():
        return config

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    server_sec = data.get("server") or {}
    if "listen_address" in server_sec:
        config.listen_address = str(server_sec["listen_address"])
    if "listen_port" in server_sec:
        config.listen_port = int(server_sec["listen_port"])
    if "max_request_bytes" in server_sec:
        config.max_request_bytes = int(server_sec["max_request_bytes"])
    if "secret_env" in server_sec:
        config.secret_env = str(server_sec["secret_env"])
    if "allowed_splunk_sources" in server_sec:
        sources = server_sec["allowed_splunk_sources"]
        if isinstance(sources, list):
            config.allowed_splunk_sources = [str(s) for s in sources]

    discord_sec = data.get("discord") or {}
    if "mode" in discord_sec:
        config.mode = str(discord_sec["mode"])
    if "bot_token_env" in discord_sec:
        config.bot_token_env = str(discord_sec["bot_token_env"])
    if "channel_id" in discord_sec:
        config.channel_id = str(discord_sec["channel_id"])
    if "webhook_url_env" in discord_sec:
        config.webhook_url_env = str(discord_sec["webhook_url_env"])
    if "api_base_url" in discord_sec:
        config.api_base_url = str(discord_sec["api_base_url"])
    if "timeout_seconds" in discord_sec:
        config.timeout_seconds = float(discord_sec["timeout_seconds"])
    if "retry_after_cap_seconds" in discord_sec:
        config.retry_after_cap_seconds = float(discord_sec["retry_after_cap_seconds"])
    if "readiness_interval_seconds" in discord_sec:
        config.readiness_interval_seconds = float(
            discord_sec["readiness_interval_seconds"]
        )
    elif "readiness_interval_seconds" in server_sec:
        config.readiness_interval_seconds = float(
            server_sec["readiness_interval_seconds"]
        )

    alerts_sec = data.get("alerts") or {}
    if "default_class" in alerts_sec:
        config.default_class = str(alerts_sec["default_class"])
    if "condense_threshold" in alerts_sec:
        config.condense_threshold = int(alerts_sec["condense_threshold"])
    if "resolved_color" in alerts_sec:
        parsed_color = parse_color(alerts_sec["resolved_color"])
        if parsed_color is not None:
            config.resolved_color = parsed_color

    classes_sec = data.get("classes") or {}
    if isinstance(classes_sec, dict):
        for name, style_data in classes_sec.items():
            if isinstance(style_data, dict):
                current = config.classes.setdefault(name, {"prefix": "", "color": None})
                if "prefix" in style_data:
                    current["prefix"] = str(style_data["prefix"])
                if "color" in style_data:
                    current["color"] = parse_color(style_data["color"])

    sev_sec = data.get("severity_colors") or {}
    if isinstance(sev_sec, dict):
        for sev, color_val in sev_sec.items():
            parsed_sev_color = parse_color(color_val)
            if parsed_sev_color is not None:
                config.severity_colors[sev] = parsed_sev_color

    return config


def apply_cli_overrides(config: RelayConfig, args: argparse.Namespace) -> RelayConfig:
    """Apply CLI argument overrides over file configuration."""
    if args.listen_address is not None:
        config.listen_address = args.listen_address
    if args.listen_port is not None:
        config.listen_port = args.listen_port
    if args.secret_env is not None:
        config.secret_env = args.secret_env
    if args.allowed_splunk_sources:
        config.allowed_splunk_sources = args.allowed_splunk_sources
    if args.mode is not None:
        config.mode = args.mode
    if args.bot_token_env is not None:
        config.bot_token_env = args.bot_token_env
    if args.channel_id is not None:
        config.channel_id = args.channel_id
    if args.webhook_url_env is not None:
        config.webhook_url_env = args.webhook_url_env
    if args.api_base_url is not None:
        config.api_base_url = args.api_base_url
    if args.timeout_seconds is not None:
        config.timeout_seconds = args.timeout_seconds
    if args.retry_after_cap_seconds is not None:
        config.retry_after_cap_seconds = args.retry_after_cap_seconds
    if args.readiness_interval_seconds is not None:
        config.readiness_interval_seconds = args.readiness_interval_seconds
    if args.default_class is not None:
        config.default_class = args.default_class
    if args.condense_threshold is not None:
        config.condense_threshold = args.condense_threshold
    if args.max_request_bytes is not None:
        config.max_request_bytes = args.max_request_bytes
    return config


def validate_config(config: RelayConfig) -> None:
    """Validate configuration fields."""
    if not (0 <= config.listen_port <= 65535):
        raise ValueError(f"invalid listen_port: {config.listen_port}")
    if config.max_request_bytes <= 0:
        raise ValueError("max_request_bytes must be positive")
    if config.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if config.retry_after_cap_seconds < 0:
        raise ValueError("retry_after_cap_seconds must be non-negative")
    if config.readiness_interval_seconds <= 0:
        raise ValueError("readiness_interval_seconds must be positive")
    if config.condense_threshold < 1:
        raise ValueError("condense_threshold must be at least 1")
    if config.mode not in ("bot", "webhook"):
        raise ValueError(
            f"invalid delivery mode '{config.mode}'; must be 'bot' or 'webhook'"
        )
    if config.mode == "bot" and not config.channel_id:
        raise ValueError("channel_id is required in bot mode")


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for Discord alert relay."""
    parser = argparse.ArgumentParser(
        description="Discord alert relay for Grafana and Splunk webhooks."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to TOML configuration file",
    )
    parser.add_argument(
        "--listen-address",
        help="address to bind the HTTP server to",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        help="port to bind the HTTP server to",
    )
    parser.add_argument(
        "--secret-env",
        help="environment variable name for Grafana bearer secret",
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
        help="Discord delivery mode ('bot' or 'webhook')",
    )
    parser.add_argument(
        "--bot-token-env",
        help="environment variable name for Discord bot token",
    )
    parser.add_argument(
        "--channel-id",
        help="Discord channel ID for bot messages",
    )
    parser.add_argument(
        "--webhook-url-env",
        help="environment variable name for Discord webhook URL",
    )
    parser.add_argument(
        "--api-base-url",
        help="Discord API base URL (default: https://discord.com/api/v10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        dest="timeout_seconds",
        help="request timeout in seconds for Discord calls",
    )
    parser.add_argument(
        "--retry-after-cap",
        type=float,
        dest="retry_after_cap_seconds",
        help="maximum wait time in seconds for HTTP 429 rate limit retry",
    )
    parser.add_argument(
        "--readiness-interval",
        type=float,
        dest="readiness_interval_seconds",
        help="interval in seconds between Discord readiness checks",
    )
    parser.add_argument(
        "--default-class",
        help="default alert class name",
    )
    parser.add_argument(
        "--condense-threshold",
        type=int,
        help="number of similar alerts to trigger a condensed embed",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        help="maximum allowed request body size in bytes",
    )
    parser.add_argument(
        "--dry-run",
        "--validate",
        action="store_true",
        dest="dry_run",
        help="validate configuration, credentials, and Discord connectivity, then exit",
    )
    return parser


def create_server(
    config: RelayConfig,
    secret: str,
    bot_token: str = "",
    webhook_url: str = "",
    sleep_fn: Any = time.sleep,
) -> DiscordRelayServer:
    """Create and configure a DiscordRelayServer instance."""
    validate_config(config)
    discord_client = DiscordClient(
        config=config,
        bot_token=bot_token,
        webhook_url=webhook_url,
        sleep_fn=sleep_fn,
    )
    server_address = (config.listen_address, config.listen_port)
    return DiscordRelayServer(
        server_address=server_address,
        RequestHandlerClass=RelayHTTPHandler,
        config=config,
        discord_client=discord_client,
        secret=secret,
    )


def main(argv: list[str] | None = None) -> int:
    """Main process entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        config = apply_cli_overrides(config, args)
        validate_config(config)
    except Exception as exc:
        logger.error("configuration error: %s", exc)
        return 1

    secret = os.environ.get(config.secret_env, "")
    if not secret:
        logger.error(
            "required environment variable %s is not set or empty",
            config.secret_env,
        )
        return 1

    bot_token = ""
    webhook_url = ""
    if config.mode == "bot":
        bot_token = os.environ.get(config.bot_token_env, "")
        if not bot_token:
            logger.error(
                "required environment variable %s is not set or empty",
                config.bot_token_env,
            )
            return 1
    elif config.mode == "webhook":
        webhook_url = os.environ.get(config.webhook_url_env, "")
        if not webhook_url:
            logger.error(
                "required environment variable %s is not set or empty",
                config.webhook_url_env,
            )
            return 1

    discord_client = DiscordClient(
        config=config,
        bot_token=bot_token,
        webhook_url=webhook_url,
    )

    if args.dry_run:
        logger.info("running in dry-run mode: testing Discord API connectivity")
        ready, detail = discord_client.check_ready()
        if ready:
            logger.info("dry-run check succeeded: %s", detail)
            return 0
        logger.error("dry-run check failed: %s", detail)
        return 1

    try:
        server = DiscordRelayServer(
            server_address=(config.listen_address, config.listen_port),
            RequestHandlerClass=RelayHTTPHandler,
            config=config,
            discord_client=discord_client,
            secret=secret,
        )
    except OSError as exc:
        logger.error("failed to bind server: %s", exc)
        return 1

    # Perform initial Discord API readiness check
    server.check_discord_api()

    # Background poller to refresh readiness on configured interval
    server.start_poller()

    def shutdown_signal(signum: int, frame: Any) -> None:
        logger.info("received signal %s; shutting down", signum)
        server.stop_poller()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown_signal)
    signal.signal(signal.SIGTERM, shutdown_signal)

    logger.info(
        "http server listening on %s:%d (mode=%s)",
        config.listen_address,
        config.listen_port,
        config.mode,
    )

    try:
        server.serve_forever()
    finally:
        server.stop_poller()
        server.server_close()
        logger.info("server stopped cleanly")

    return 0


if __name__ == "__main__":
    sys.exit(main())
