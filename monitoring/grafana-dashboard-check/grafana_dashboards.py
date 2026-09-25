#!/usr/bin/env python3
"""Check Grafana dashboard grids offline and run dashboard PromQL against Prometheus."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import ssl
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

DEFAULT_URL = "http://127.0.0.1:9090"
DEFAULT_TIMEOUT = 30.0
GRID_COLUMNS = 24

# Grafana resolves its built-in variables before a query leaves the browser.
# These values match a 6-hour time range with a 1-minute step, so the check
# sends the expression Grafana would send for that range.
DEFAULT_VARIABLES = {
    "__interval": "1m",
    "__interval_ms": "60000",
    "__rate_interval": "5m",
    "__range": "6h",
    "__range_ms": "21600000",
    "__range_s": "21600",
}

VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Whole-name matching keeps $host from rewriting part of $hostname, and keeps
# $__range from rewriting part of $__range_s.
VARIABLE_REFERENCE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)

# These statuses describe the endpoint or the credentials, not one expression.
# Every remaining query would fail the same way, so the run stops early.
ENDPOINT_STATUSES = frozenset({401, 403, 404, 407})

TOP_LEVEL_KEYS = {"dashboards", "prometheus", "queries"}
DASHBOARD_KEYS = {"paths", "require_single_datasource"}
PROMETHEUS_KEYS = {
    "url",
    "timeout_seconds",
    "ca_file",
    "bearer_token_env",
    "basic_username",
    "basic_password_env",
}
QUERY_KEYS = {"variables", "allow_empty_titles", "allow_empty_file"}


@dataclass(frozen=True)
class HttpSettings:
    """Connection settings that never contain credential values."""

    url: str = DEFAULT_URL
    timeout: float = DEFAULT_TIMEOUT
    ca_file: Path | None = None
    bearer_token_env: str = ""
    basic_username: str = ""
    basic_password_env: str = ""


@dataclass(frozen=True)
class Settings:
    dashboards: tuple[Path, ...] = ()
    require_single_datasource: bool = True
    http: HttpSettings = field(default_factory=HttpSettings)
    variables: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VARIABLES))
    allow_empty_titles: tuple[str, ...] = ()
    allow_empty_file: Path | None = None


@dataclass(frozen=True)
class Dashboard:
    path: Path
    data: dict[str, Any]

    @property
    def title(self) -> str:
        title = self.data.get("title")
        return title if isinstance(title, str) and title else str(self.path)


@dataclass(frozen=True)
class Query:
    panel_title: str
    ref_id: str
    expr: str

    @property
    def label(self) -> str:
        return f"{self.panel_title} [{self.ref_id}]"


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    series: int
    message: str = ""


class EndpointError(Exception):
    """A request failure that would repeat for every remaining query."""


class RejectRedirects(HTTPRedirectHandler):
    """Keep authorization headers from being forwarded to another endpoint."""

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


def _reject_unknown_keys(section: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(
        key for key in section if key not in allowed and not key.startswith("_comment")
    )
    if unknown:
        raise ValueError(f"{name} has unknown keys: {', '.join(unknown)}")


def _section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _optional_string(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _config_path(value: Any, field_name: str, base_directory: Path) -> Path | None:
    text = _optional_string(value, field_name)
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_directory / path


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def _timeout(value: Any, field_name: str) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be a number greater than zero")
    return float(value)


def parse_variables(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    variables: dict[str, str] = {}
    for name, substitute in value.items():
        if not VARIABLE_NAME.fullmatch(name):
            raise ValueError(
                f"{field_name} has an invalid variable name {name!r}; "
                "write names without the leading $"
            )
        if not isinstance(substitute, str):
            raise ValueError(f"{field_name}.{name} must be a string")
        variables[name] = substitute
    return variables


def validate_http_settings(settings: HttpSettings) -> None:
    parsed = urlparse(settings.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Prometheus URL must use HTTP or HTTPS and name a host")
    # Credentials come only from environment variables, never from a URL that
    # would land in config files, shell history, and process listings.
    if parsed.username or parsed.password:
        raise ValueError("Prometheus URL must not contain a username or password")
    if parsed.query or parsed.fragment:
        raise ValueError("Prometheus URL must not contain a query string or fragment")
    if parsed.path.rstrip("/").endswith(("/api/v1", "/api/v1/query")):
        raise ValueError("set the Prometheus base URL without /api/v1")
    _timeout(settings.timeout, "timeout")
    if settings.ca_file and parsed.scheme != "https":
        raise ValueError("a custom CA file requires an HTTPS URL")
    if settings.bearer_token_env and (
        settings.basic_username or settings.basic_password_env
    ):
        raise ValueError("bearer and basic authentication are mutually exclusive")
    if bool(settings.basic_username) != bool(settings.basic_password_env):
        raise ValueError(
            "basic authentication requires a username and password env name"
        )


def parse_http_settings(section: dict[str, Any], base_directory: Path) -> HttpSettings:
    _reject_unknown_keys(section, PROMETHEUS_KEYS, "prometheus")
    settings = HttpSettings(
        url=_optional_string(section.get("url", DEFAULT_URL), "prometheus.url"),
        timeout=_timeout(
            section.get("timeout_seconds", DEFAULT_TIMEOUT),
            "prometheus.timeout_seconds",
        ),
        ca_file=_config_path(
            section.get("ca_file"), "prometheus.ca_file", base_directory
        ),
        bearer_token_env=_optional_string(
            section.get("bearer_token_env"), "prometheus.bearer_token_env"
        ),
        basic_username=_optional_string(
            section.get("basic_username"), "prometheus.basic_username"
        ),
        basic_password_env=_optional_string(
            section.get("basic_password_env"), "prometheus.basic_password_env"
        ),
    )
    validate_http_settings(settings)
    return settings


def parse_config(payload: Any, base_directory: Path) -> Settings:
    """Parse a local configuration. Relative paths start at base_directory."""

    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a JSON object")
    _reject_unknown_keys(payload, TOP_LEVEL_KEYS, "configuration")

    dashboards = _section(payload, "dashboards")
    _reject_unknown_keys(dashboards, DASHBOARD_KEYS, "dashboards")
    paths = _string_list(dashboards.get("paths", []), "dashboards.paths")
    require_single = dashboards.get("require_single_datasource", True)
    if not isinstance(require_single, bool):
        raise ValueError("dashboards.require_single_datasource must be true or false")

    queries = _section(payload, "queries")
    _reject_unknown_keys(queries, QUERY_KEYS, "queries")
    variables = parse_variables(queries.get("variables", {}), "queries.variables")

    return Settings(
        dashboards=tuple(
            path if path.is_absolute() else base_directory / path
            for path in (Path(item).expanduser() for item in paths)
        ),
        require_single_datasource=require_single,
        http=parse_http_settings(_section(payload, "prometheus"), base_directory),
        variables={**DEFAULT_VARIABLES, **variables},
        allow_empty_titles=_string_list(
            queries.get("allow_empty_titles", []), "queries.allow_empty_titles"
        ),
        allow_empty_file=_config_path(
            queries.get("allow_empty_file"), "queries.allow_empty_file", base_directory
        ),
    )


def load_config(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"configuration is not valid JSON: {path}: {exc}") from None
    return parse_config(payload, path.resolve().parent)


def _cli_paths(args: argparse.Namespace) -> tuple[Path, ...]:
    return tuple(path.expanduser() for path in args.paths)


def resolve_layout_settings(config: Settings, args: argparse.Namespace) -> Settings:
    return replace(
        config,
        dashboards=_cli_paths(args) or config.dashboards,
        require_single_datasource=(
            config.require_single_datasource
            if args.require_single_datasource is None
            else args.require_single_datasource
        ),
    )


def resolve_http_settings(
    config: HttpSettings, args: argparse.Namespace
) -> HttpSettings:
    if args.no_auth and any(
        value is not None
        for value in (
            args.bearer_token_env,
            args.basic_username,
            args.basic_password_env,
        )
    ):
        raise ValueError("--no-auth cannot be combined with authentication overrides")

    bearer_token_env = config.bearer_token_env
    basic_username = config.basic_username
    basic_password_env = config.basic_password_env
    if args.no_auth:
        bearer_token_env = ""
        basic_username = ""
        basic_password_env = ""
    elif args.bearer_token_env is not None:
        bearer_token_env = args.bearer_token_env
        basic_username = ""
        basic_password_env = ""
    elif args.basic_username is not None or args.basic_password_env is not None:
        bearer_token_env = ""
        basic_username = (
            args.basic_username
            if args.basic_username is not None
            else config.basic_username
        )
        basic_password_env = (
            args.basic_password_env
            if args.basic_password_env is not None
            else config.basic_password_env
        )

    settings = replace(
        config,
        url=args.url if args.url is not None else config.url,
        timeout=args.timeout if args.timeout is not None else config.timeout,
        ca_file=(
            None
            if args.no_ca_file
            else (
                Path(args.ca_file).expanduser()
                if args.ca_file is not None
                else config.ca_file
            )
        ),
        bearer_token_env=bearer_token_env,
        basic_username=basic_username,
        basic_password_env=basic_password_env,
    )
    validate_http_settings(settings)
    return settings


def parse_variable_overrides(values: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in values or []:
        name, separator, value = item.partition("=")
        if not separator or not VARIABLE_NAME.fullmatch(name):
            raise ValueError(
                f"--var expects NAME=VALUE with a name such as host: {item!r}"
            )
        overrides[name] = value
    return overrides


def resolve_query_settings(config: Settings, args: argparse.Namespace) -> Settings:
    titles = config.allow_empty_titles
    if args.allow_empty is not None:
        titles = _string_list(args.allow_empty, "--allow-empty")
    return replace(
        config,
        dashboards=_cli_paths(args) or config.dashboards,
        http=resolve_http_settings(config.http, args),
        variables={**config.variables, **parse_variable_overrides(args.var)},
        allow_empty_titles=titles,
        allow_empty_file=(
            Path(args.allow_empty_file).expanduser()
            if args.allow_empty_file is not None
            else config.allow_empty_file
        ),
    )


def dashboard_files(paths: Iterable[Path]) -> list[Path]:
    """Expand files and directories into dashboard files, each listed once."""

    paths = list(paths)
    if not paths:
        raise ValueError(
            "no dashboard paths given; pass files or directories, "
            "or set dashboards.paths in the config"
        )
    files: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            found = sorted(item for item in path.rglob("*.json") if item.is_file())
            if not found:
                raise ValueError(f"no dashboard JSON files under {path}")
        elif path.is_file():
            found = [path]
        else:
            raise ValueError(f"no such dashboard file or directory: {path}")
        for item in found:
            key = item.resolve()
            if key not in seen:
                seen.add(key)
                files.append(item)
    return files


def load_dashboard(path: Path) -> Dashboard:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from None
    except OSError as exc:
        raise ValueError(f"{path}: cannot read: {exc.strerror or exc}") from None
    # The Grafana HTTP API wraps a dashboard as {"dashboard": {...}, "meta": ...}.
    if (
        isinstance(data, dict)
        and "panels" not in data
        and isinstance(data.get("dashboard"), dict)
    ):
        data = data["dashboard"]
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a dashboard; the JSON root must be an object")
    if not isinstance(data.get("panels", []), list):
        raise ValueError(f"{path}: panels must be a list")
    return Dashboard(path=path, data=data)


def load_dashboards(paths: Iterable[Path]) -> tuple[list[Dashboard], list[str]]:
    dashboards: list[Dashboard] = []
    errors: list[str] = []
    for path in dashboard_files(paths):
        try:
            dashboards.append(load_dashboard(path))
        except ValueError as exc:
            errors.append(str(exc))
    return dashboards, errors


def iter_panels(panels: Any) -> Iterator[dict[str, Any]]:
    """Yield every panel, then the children a row carries."""

    if not isinstance(panels, list):
        return
    for panel in panels:
        if isinstance(panel, dict):
            yield panel
            yield from iter_panels(panel.get("panels"))


def _panel_name(panel: dict[str, Any]) -> str:
    return str(panel.get("title") or panel.get("type") or "<untitled>")


def grid_spaces(panels: list[Any]) -> list[list[Any]]:
    """Split panels into coordinate spaces that are checked independently.

    A collapsed row carries its children in its own list, and Grafana places
    them only when the row expands, so they never collide with the top level.
    """

    top: list[Any] = []
    spaces = [top]
    for panel in panels:
        top.append(panel)
        if (
            isinstance(panel, dict)
            and panel.get("type") == "row"
            and panel.get("collapsed")
        ):
            children = panel.get("panels")
            spaces.append(list(children) if isinstance(children, list) else [])
    return spaces


def _grid_value(grid: dict[str, Any], key: str) -> int | None:
    value = grid.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def check_grid(title: str, panels: list[Any]) -> list[str]:
    problems: list[str] = []
    placed: list[tuple[str, int, int, int, int]] = []
    for panel in panels:
        if not isinstance(panel, dict):
            problems.append(f"{title}: a panel entry is not a JSON object")
            continue
        name = _panel_name(panel)
        grid = panel.get("gridPos")
        grid = grid if isinstance(grid, dict) else {}
        x, y, w, h = (_grid_value(grid, key) for key in ("x", "y", "w", "h"))
        if x is None or y is None or w is None or h is None:
            problems.append(f"{title}: {name!r} has no complete integer gridPos")
            continue
        if w <= 0 or h <= 0:
            problems.append(f"{title}: {name!r} has size {w}x{h}")
            continue
        if x < 0 or x + w > GRID_COLUMNS:
            problems.append(
                f"{title}: {name!r} spans columns {x}..{x + w}, "
                f"outside 0..{GRID_COLUMNS}"
            )
            continue
        if y < 0:
            problems.append(f"{title}: {name!r} starts at row {y}, above row 0")
            continue
        for other, other_x, other_y, other_w, other_h in placed:
            left, top = max(x, other_x), max(y, other_y)
            if left < min(x + w, other_x + other_w) and top < min(
                y + h, other_y + other_h
            ):
                problems.append(
                    f"{title}: {name!r} overlaps {other!r} at column {left}, row {top}"
                )
        placed.append((name, x, y, w, h))
    return problems


def duplicate_panel_ids(title: str, data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    owners: dict[int | str, str] = {}
    for panel in iter_panels(data.get("panels")):
        panel_id = panel.get("id")
        if not isinstance(panel_id, int | str) or isinstance(panel_id, bool):
            continue
        name = _panel_name(panel)
        if panel_id in owners:
            problems.append(
                f"{title}: panel id {panel_id!r} is used by "
                f"{owners[panel_id]!r} and {name!r}"
            )
        else:
            owners[panel_id] = name
    return problems


def _add_datasource_uid(uids: set[str], datasource: Any) -> None:
    if isinstance(datasource, dict):
        uid = datasource.get("uid")
        if isinstance(uid, str) and uid:
            uids.add(uid)


def datasource_uids(data: dict[str, Any]) -> set[str]:
    uids: set[str] = set()
    templating = data.get("templating")
    variables = templating.get("list") if isinstance(templating, dict) else None
    for variable in variables if isinstance(variables, list) else []:
        if isinstance(variable, dict):
            _add_datasource_uid(uids, variable.get("datasource"))
    for panel in iter_panels(data.get("panels")):
        _add_datasource_uid(uids, panel.get("datasource"))
        targets = panel.get("targets")
        for target in targets if isinstance(targets, list) else []:
            if isinstance(target, dict):
                _add_datasource_uid(uids, target.get("datasource"))
    return uids


def check_layout(
    dashboards: list[Dashboard], require_single_datasource: bool
) -> tuple[list[str], int]:
    """Return every layout problem and the number of distinct dashboard uids."""

    problems: list[str] = []
    uid_owners: dict[str | None, str] = {}
    for dashboard in dashboards:
        title = dashboard.title
        for space in grid_spaces(dashboard.data.get("panels", [])):
            problems.extend(check_grid(title, space))
        problems.extend(duplicate_panel_ids(title, dashboard.data))
        if require_single_datasource:
            uids = datasource_uids(dashboard.data)
            if len(uids) > 1:
                problems.append(
                    f"{title}: more than one datasource uid in use: {sorted(uids)}"
                )
        uid = dashboard.data.get("uid")
        key = uid if isinstance(uid, str) and uid else None
        if key in uid_owners:
            subject = f"uid {key!r} is used by" if key else "no uid is set in"
            problems.append(f"{subject} both {uid_owners[key]} and {dashboard.path}")
        else:
            uid_owners[key] = str(dashboard.path)
    return problems, len([key for key in uid_owners if key])


def collect_queries(data: dict[str, Any]) -> list[Query]:
    """Return every visible target expression, including collapsed-row children."""

    found: list[Query] = []
    for panel in iter_panels(data.get("panels")):
        title = str(panel.get("title") or "<untitled>")
        targets = panel.get("targets")
        for target in targets if isinstance(targets, list) else []:
            if not isinstance(target, dict) or target.get("hide"):
                continue
            expr = target.get("expr")
            if isinstance(expr, str) and expr.strip():
                found.append(Query(title, str(target.get("refId") or "?"), expr))
    return found


def interpolate(expr: str, variables: dict[str, str]) -> str:
    """Replace $name and ${name}. Unknown names reach Prometheus unchanged."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return variables.get(name, match.group(0))

    return VARIABLE_REFERENCE.sub(substitute, expr)


def load_allow_empty(settings: Settings) -> frozenset[str]:
    titles = set(settings.allow_empty_titles)
    path = settings.allow_empty_file
    if path is not None:
        # A missing list is an error rather than an empty list. Falling back
        # silently turns every correct-when-empty panel into a false failure,
        # or hides a renamed list behind a stale default.
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"allow-empty file is not valid JSON: {path}: {exc}"
            ) from None
        except OSError as exc:
            raise ValueError(
                f"cannot read allow-empty file {path}: {exc.strerror or exc}"
            ) from None
        titles.update(_string_list(payload, f"allow-empty file {path}"))
    return frozenset(titles)


def authorization_header(settings: HttpSettings) -> str | None:
    if settings.bearer_token_env:
        token = os.environ.get(settings.bearer_token_env)
        if not token:
            raise ValueError(
                f"environment variable {settings.bearer_token_env!r} is empty or unset"
            )
        return f"Bearer {token}"
    if settings.basic_password_env:
        password = os.environ.get(settings.basic_password_env)
        if not password:
            variable_name = settings.basic_password_env
            raise ValueError(
                f"environment variable {variable_name!r} is empty or unset"
            )
        raw = f"{settings.basic_username}:{password}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"
    return None


def request_headers(settings: HttpSettings) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "tools-and-scripts/1"}
    auth = authorization_header(settings)
    if auth:
        headers["Authorization"] = auth
    return headers


def build_query_opener(settings: HttpSettings) -> OpenerDirector:
    context = None
    if urlparse(settings.url).scheme == "https":
        if settings.ca_file and not settings.ca_file.is_file():
            raise ValueError(f"custom CA file not found: {settings.ca_file}")
        context = ssl.create_default_context(
            cafile=str(settings.ca_file) if settings.ca_file else None
        )
    return build_opener(RejectRedirects(), HTTPSHandler(context=context))


def query_url(base_url: str, expr: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/query?{urlencode({'query': expr})}"


def _error_detail(error: HTTPError) -> str:
    try:
        payload = json.load(error)
    except (OSError, ValueError):
        payload = None
    finally:
        error.close()
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"]
    return str(error.reason)


def interpret_payload(payload: Any) -> QueryResult:
    if not isinstance(payload, dict):
        return QueryResult(False, 0, "response is not a JSON object")
    if payload.get("status") != "success":
        return QueryResult(
            False, 0, str(payload.get("error") or "query returned a non-success status")
        )
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        return QueryResult(False, 0, "response has no data.result list")
    if data.get("resultType") in {"scalar", "string"}:
        return QueryResult(True, 1)
    return QueryResult(True, len(result))


def run_query(
    opener: OpenerDirector, settings: HttpSettings, headers: dict[str, str], expr: str
) -> QueryResult:
    request = Request(query_url(settings.url, expr), headers=headers)
    try:
        with opener.open(request, timeout=settings.timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if 300 <= exc.code < 400 or exc.code in ENDPOINT_STATUSES:
            exc.close()
            raise EndpointError(
                f"Prometheus returned HTTP {exc.code}; check the base URL and "
                "credentials (redirects are rejected)"
            ) from None
        return QueryResult(False, 0, f"HTTP {exc.code}: {_error_detail(exc)}")
    except TimeoutError:
        return QueryResult(False, 0, f"timed out after {settings.timeout:g} seconds")
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return QueryResult(
                False, 0, f"timed out after {settings.timeout:g} seconds"
            )
        raise EndpointError(f"Prometheus request failed: {exc.reason}") from None
    except ValueError:
        return QueryResult(False, 0, "response is not JSON")
    except OSError as exc:
        raise EndpointError(f"Prometheus request failed: {exc}") from None
    return interpret_payload(payload)


def _report_input_errors(errors: Iterable[str]) -> int:
    for error in errors:
        print(f"input-error: {error}", file=sys.stderr)
    return 1


def command_layout(args: argparse.Namespace) -> int:
    try:
        settings = resolve_layout_settings(load_config(args.config), args)
        dashboards, errors = load_dashboards(settings.dashboards)
    except (OSError, ValueError) as exc:
        return _report_input_errors([str(exc)])
    if errors:
        return _report_input_errors(errors)

    problems, unique_uids = check_layout(dashboards, settings.require_single_datasource)
    print(f"checked: {len(dashboards)} dashboards, {unique_uids} unique uids")
    if problems:
        for problem in problems:
            print(f"layout-problem: {problem}", file=sys.stderr)
        print(f"assertion-failed: {len(problems)} layout problem(s)", file=sys.stderr)
        return 2
    datasource_note = (
        ", one datasource throughout" if settings.require_single_datasource else ""
    )
    print(
        "assertion-passed: no overlaps, nothing outside the "
        f"{GRID_COLUMNS}-column grid{datasource_note}"
    )
    return 0


def redact_credentials(message: str, settings: HttpSettings) -> str:
    # A proxy or API can echo authorization data in its error response.
    values = [
        os.environ.get(name, "")
        for name in (settings.bearer_token_env, settings.basic_password_env)
        if name
    ]
    header = authorization_header(settings)
    if header:
        values.extend([header, header.split(" ", 1)[1]])
    for value in sorted(set(values), key=len, reverse=True):
        if value:
            message = message.replace(value, "[redacted]")
    return message


def command_queries(args: argparse.Namespace) -> int:
    try:
        settings = resolve_query_settings(load_config(args.config), args)
        dashboards, errors = load_dashboards(settings.dashboards)
        if errors:
            return _report_input_errors(errors)
        allow_empty = load_allow_empty(settings)
        plans = [
            (dashboard, collect_queries(dashboard.data)) for dashboard in dashboards
        ]
        if args.dry_run:
            return print_query_plan(plans, settings.variables, allow_empty)
        # Credentials and TLS settings are checked before the first request.
        headers = request_headers(settings.http)
        opener = build_query_opener(settings.http)
    except (OSError, ValueError) as exc:
        return _report_input_errors([str(exc)])

    print(f"prometheus: {settings.http.url}")
    print(f"dashboards: {len(dashboards)}")
    total = errored = empty = allowed = without_queries = 0
    try:
        for dashboard, queries in plans:
            title = dashboard.title
            if not queries:
                print(f"query-error: {title}: no PromQL queries found", file=sys.stderr)
                print(f"dashboard: FAIL {title}: 0 queries")
                without_queries += 1
                continue
            counts = {"errored": 0, "empty": 0, "allowed": 0}
            for query in queries:
                expr = interpolate(query.expr, settings.variables)
                result = run_query(opener, settings.http, headers, expr)
                if not result.ok:
                    print(
                        f"query-error: {title} / {query.label}: "
                        f"{redact_credentials(result.message, settings.http)}",
                        file=sys.stderr,
                    )
                    counts["errored"] += 1
                elif result.series == 0 and query.panel_title in allow_empty:
                    print(f"query-allowed-empty: {title} / {query.label}")
                    counts["allowed"] += 1
                elif result.series == 0:
                    print(f"query-empty: {title} / {query.label}", file=sys.stderr)
                    counts["empty"] += 1
            status = "FAIL" if counts["errored"] or counts["empty"] else "ok"
            print(
                f"dashboard: {status} {title}: {len(queries)} queries, "
                f"{counts['allowed']} allowed empty, {counts['empty']} empty, "
                f"{counts['errored']} errored"
            )
            total += len(queries)
            errored += counts["errored"]
            empty += counts["empty"]
            allowed += counts["allowed"]
    except EndpointError as exc:
        return _report_input_errors([redact_credentials(str(exc), settings.http)])

    print(
        f"summary: {len(dashboards)} dashboards, {total} queries: "
        f"{total - errored - empty - allowed} returned data, {allowed} allowed empty, "
        f"{empty} unexpectedly empty, {errored} errored, "
        f"{without_queries} dashboards without queries"
    )
    if errored or without_queries:
        print(
            f"assertion-failed: {errored} query error(s), "
            f"{without_queries} dashboard(s) without queries",
            file=sys.stderr,
        )
        return 2
    if empty:
        print(
            f"assertion-failed: {empty} query result(s) unexpectedly empty",
            file=sys.stderr,
        )
        return 3
    print("assertion-passed: every query returned data or is allowed empty")
    return 0


def print_query_plan(
    plans: list[tuple[Dashboard, list[Query]]],
    variables: dict[str, str],
    allow_empty: frozenset[str],
) -> int:
    total = without_queries = 0
    for dashboard, queries in plans:
        if not queries:
            print(
                f"query-error: {dashboard.title}: no PromQL queries found",
                file=sys.stderr,
            )
            without_queries += 1
        for query in queries:
            marker = " (allowed empty)" if query.panel_title in allow_empty else ""
            print(
                f"planned-query: {dashboard.title} / {query.label}{marker}: "
                f"{interpolate(query.expr, variables)}"
            )
        total += len(queries)
    print(f"dry-run: {total} queries in {len(plans)} dashboards; no request was sent")
    return 2 if without_queries else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        help="local JSON configuration copied from config.example.json",
    )
    common.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="PATH",
        help=(
            "dashboard JSON file, or a directory searched recursively for *.json; "
            "overrides dashboards.paths from the config"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    layout = subparsers.add_parser(
        "layout",
        parents=[common],
        help="check panel placement offline",
        description=(
            "Check panel grids offline: overlaps, the 24-column limit, sizes, "
            "duplicate panel ids and dashboard uids, and one datasource per "
            "dashboard. Nothing is contacted."
        ),
    )
    policy = layout.add_mutually_exclusive_group()
    policy.add_argument(
        "--allow-mixed-datasources",
        dest="require_single_datasource",
        action="store_false",
        default=None,
        help="override the config and accept more than one datasource uid",
    )
    policy.add_argument(
        "--require-single-datasource",
        dest="require_single_datasource",
        action="store_true",
        default=None,
        help="override the config and fail on more than one datasource uid",
    )

    queries = subparsers.add_parser(
        "queries",
        parents=[common],
        help="run every dashboard PromQL expression against Prometheus",
        description=(
            "Run every visible PromQL expression against the Prometheus instant "
            "query API and fail when one errors or returns no data. Requests are "
            "read-only GETs."
        ),
    )
    queries.add_argument(
        "--url", help="override the configured Prometheus base URL, without /api/v1"
    )
    queries.add_argument(
        "--timeout", type=float, help="override the per-query timeout in seconds"
    )
    ca_group = queries.add_mutually_exclusive_group()
    ca_group.add_argument("--ca-file", help="override the private CA PEM path")
    ca_group.add_argument(
        "--no-ca-file", action="store_true", help="use the system trust store"
    )
    queries.add_argument(
        "--bearer-token-env",
        help="override the name of the bearer-token environment variable",
    )
    queries.add_argument(
        "--basic-username", help="override the configured basic-auth username"
    )
    queries.add_argument(
        "--basic-password-env",
        help="override the name of the basic-auth password environment variable",
    )
    queries.add_argument(
        "--no-auth", action="store_true", help="disable configured authentication"
    )
    queries.add_argument(
        "--var",
        action="append",
        metavar="NAME=VALUE",
        help=(
            "substitute VALUE for $NAME and ${NAME}; overrides the same name in "
            "queries.variables; repeat for more variables"
        ),
    )
    queries.add_argument(
        "--allow-empty",
        action="append",
        metavar="TITLE",
        help=(
            "panel title that may return no data; replaces "
            "queries.allow_empty_titles; repeat for more titles"
        ),
    )
    queries.add_argument(
        "--allow-empty-file",
        metavar="PATH",
        help="JSON list of panel titles that may return no data; overrides the config",
    )
    queries.add_argument(
        "--dry-run",
        action="store_true",
        help="print each expression after substitution and send nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "layout":
        return command_layout(args)
    return command_queries(args)


if __name__ == "__main__":
    raise SystemExit(main())
