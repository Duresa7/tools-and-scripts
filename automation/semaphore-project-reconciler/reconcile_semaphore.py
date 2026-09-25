#!/usr/bin/env python3
"""Reconcile Semaphore projects from versioned Ansible project manifests."""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import math
import os
import re
import ssl
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

JsonObject = dict[str, Any]

DEFAULT_URL = "http://127.0.0.1:3000"
DEFAULT_TOKEN_ENV = "SEMAPHORE_API_TOKEN"
DEFAULT_PRIVATE_KEY_ENV = "SEMAPHORE_SSH_PRIVATE_KEY"
DEFAULT_CREDENTIAL_LOGIN = "ansible"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MANIFEST = "manifest.local.yml"

PRUNE_PHRASE = "DELETE_UNLISTED_SEMAPHORE_OBJECTS"
REFRESH_PHRASE = "REPLACE_SEMAPHORE_SSH_CREDENTIALS"

# Stand-in ID for an object that a plan would create. Real Semaphore IDs are positive.
PENDING_ID = -1

ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TEMPLATE_COMPARE_FIELDS = (
    "name",
    "playbook",
    "arguments",
    "description",
    "inventory_id",
    "repository_id",
    "view_id",
    "app",
    "git_branch",
    "survey_vars",
    "type",
    "start_version",
    "autorun",
    "task_params",
    "vaults",
    "allow_override_args_in_task",
    "allow_override_branch_in_task",
    "allow_parallel_tasks",
    "suppress_success_alerts",
)
TEMPLATE_JSON_FIELDS = ("arguments", "survey_vars", "task_params", "vaults")

# Unknown keys are rejected so a typo such as "argument:" can't silently turn a
# check-mode template into one that changes hosts.
MANIFEST_KEYS = ({"project", "views", "templates"}, set())
PROJECT_KEYS = ({"name", "repository", "inventory", "environment"}, set())
SECTION_KEYS = {
    "repository": ({"name", "path"}, {"branch"}),
    "inventory": ({"name", "path", "credential"}, set()),
    "environment": ({"name", "variables"}, {"extra_variables"}),
}
TEMPLATE_KEYS = ({"name", "view", "playbook"}, {"arguments", "description", "survey"})
SURVEY_KEYS = ({"name"}, {"title", "description", "type", "required", "values"})

CONFIG_KEYS = {
    "semaphore": {
        "url",
        "token_env",
        "timeout_seconds",
        "ca_file",
        "allow_insecure_http",
    },
    "credential": {"login", "private_key_env"},
    "manifests": {"paths"},
}

KEY_ACTIONS = {
    ("create", "project"),
    ("create", "credential"),
    ("refresh", "credential"),
}
CHANGE_VERBS = {"create", "update", "reorder", "refresh", "delete"}
PAST_TENSE = {
    "create": "created",
    "update": "updated",
    "reorder": "reordered",
    "refresh": "refreshed",
    "delete": "deleted",
}


class ManifestError(ValueError):
    """A manifest is unreadable or doesn't match the supported layout."""


@dataclass(frozen=True)
class Settings:
    """Resolved settings. Secret fields hold environment-variable names only."""

    url: str = DEFAULT_URL
    token_env: str = DEFAULT_TOKEN_ENV
    timeout: float = DEFAULT_TIMEOUT
    ca_file: Path | None = None
    allow_insecure_http: bool = False
    credential_login: str = DEFAULT_CREDENTIAL_LOGIN
    private_key_env: str = DEFAULT_PRIVATE_KEY_ENV
    manifests: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Action:
    """One planned or applied change, or one retained unmanaged object."""

    verb: str
    kind: str
    name: str
    applied: bool = False
    object_id: int | None = None

    @property
    def changes_state(self) -> bool:
        return self.verb in CHANGE_VERBS

    def __str__(self) -> str:
        if self.verb == "retain":
            return f"unmanaged {self.kind} {self.name} (id {self.object_id}) retained"
        if not self.applied:
            return f"would {self.verb} {self.kind} {self.name}"
        return f"{PAST_TENSE.get(self.verb, self.verb)} {self.kind} {self.name}"


def _check_keys(
    value: Any, where: str, keys: tuple[set[str], set[str]]
) -> dict[str, Any]:
    required, optional = keys
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a mapping")
    missing = sorted(required - value.keys())
    if missing:
        raise ManifestError(f"{where} is missing {', '.join(missing)}")
    unknown = sorted(str(key) for key in value.keys() - required - optional)
    if unknown:
        raise ManifestError(f"{where} has unknown keys: {', '.join(unknown)}")
    return value


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ManifestError(f"{where} must be a non-empty string")
    return value


def read_manifest_document(path: Path) -> Any:
    """Read JSON with the standard library, or YAML through PyYAML."""

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"{path}: invalid JSON: {exc}") from None
    try:
        import yaml
    except ModuleNotFoundError:
        raise ManifestError(
            f"{path}: PyYAML is required for YAML manifests; run "
            "python -m pip install PyYAML or use a .json manifest"
        ) from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from None


def validate_manifest(data: Any, source: str) -> JsonObject:
    """Validate one manifest and return it unchanged."""

    _check_keys(data, f"{source}: manifest root", MANIFEST_KEYS)
    project = _check_keys(data["project"], f"{source}: project", PROJECT_KEYS)
    _text(project["name"], f"{source}: project.name")

    for section, keys in SECTION_KEYS.items():
        value = _check_keys(project[section], f"{source}: project.{section}", keys)
        for field in keys[0] - {"variables"}:
            _text(value[field], f"{source}: project.{section}.{field}")
    _text(
        project["repository"].get("branch", ""),
        f"{source}: project.repository.branch",
        allow_empty=True,
    )

    environment = project["environment"]
    variables = environment["variables"]
    if variables is None:
        variables = environment["variables"] = {}
    if not isinstance(variables, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in variables.items()
    ):
        raise ManifestError(
            f"{source}: project.environment.variables must map names to strings"
        )
    extra_variables = environment.get("extra_variables", {})
    if extra_variables is None:
        extra_variables = environment["extra_variables"] = {}
    if not isinstance(extra_variables, dict):
        raise ManifestError(
            f"{source}: project.environment.extra_variables must be a mapping"
        )
    try:
        json.dumps(extra_variables)
    except (TypeError, ValueError):
        raise ManifestError(
            f"{source}: project.environment.extra_variables must be JSON-compatible"
        ) from None

    views = data["views"]
    if (
        not isinstance(views, list)
        or not views
        or views[0] != "All"
        or not all(isinstance(view, str) and view.strip() for view in views)
    ):
        raise ManifestError(
            f"{source}: views must be a list of names starting with All"
        )
    if len(views) != len(set(views)):
        raise ManifestError(f"{source}: duplicate view name")

    templates = data["templates"]
    if not isinstance(templates, list) or not templates:
        raise ManifestError(f"{source}: templates must be a non-empty list")

    template_names: set[str] = set()
    for index, template in enumerate(templates):
        _check_keys(template, f"{source}: templates[{index}]", TEMPLATE_KEYS)
        name = _text(template["name"], f"{source}: templates[{index}].name")
        if name in template_names:
            raise ManifestError(f"{source}: duplicate template name {name!r}")
        template_names.add(name)
        if template["view"] not in views:
            raise ManifestError(
                f"{source}: {name!r} references unknown view {template['view']!r}"
            )
        _text(template["playbook"], f"{source}: {name!r} playbook")
        _text(
            template.get("description", ""),
            f"{source}: {name!r} description",
            allow_empty=True,
        )
        arguments = template.get("arguments", [])
        if not isinstance(arguments, list) or any(
            not isinstance(argument, str) for argument in arguments
        ):
            raise ManifestError(f"{source}: {name!r} arguments must be a string list")
        survey = template.get("survey", [])
        if not isinstance(survey, list):
            raise ManifestError(f"{source}: {name!r} survey must be a list")
        for position, variable in enumerate(survey):
            where = f"{source}: {name!r} survey[{position}]"
            _check_keys(variable, where, SURVEY_KEYS)
            _text(variable["name"], f"{where}.name")
            if "required" in variable and not isinstance(variable["required"], bool):
                raise ManifestError(f"{where}.required must be true or false")
            if "values" in variable and not isinstance(variable["values"], list):
                raise ManifestError(f"{where}.values must be a list")
    return data


def load_manifest(path: Path) -> JsonObject:
    """Load and validate one Semaphore project manifest."""

    return validate_manifest(read_manifest_document(path), str(path))


def load_manifests(paths: Iterable[Path]) -> list[JsonObject]:
    manifests = [load_manifest(path) for path in paths]
    if not manifests:
        raise ManifestError(
            "no manifest given; pass a manifest path or set manifests.paths in config"
        )
    names = [manifest["project"]["name"] for manifest in manifests]
    if len(names) != len(set(names)):
        raise ManifestError("duplicate project name across manifests")
    return manifests


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def environment_payload(project_id: int, environment: JsonObject) -> JsonObject:
    """Build the API payload for one variable group."""

    variables = environment.get("variables") or {}
    if not isinstance(variables, dict):
        raise ValueError("environment.variables must be a mapping")
    return {
        "project_id": project_id,
        "name": environment["name"],
        "password": None,
        "json": _compact_json(environment.get("extra_variables") or {}),
        "env": _compact_json(variables),
        # An empty list leaves stored environment secrets untouched.
        "secrets": [],
    }


def normalize_survey(survey: Iterable[JsonObject]) -> list[JsonObject]:
    """Remove empty optional survey fields that Semaphore omits on readback."""

    normalized: list[JsonObject] = []
    for variable in survey:
        item = {
            key: variable[key]
            for key in ("name", "title", "description", "type", "required", "values")
            if key in variable
        }
        if item.get("type") == "":
            item.pop("type")
        if item.get("values") == []:
            item.pop("values")
        normalized.append(item)
    return normalized


def template_payload(
    *,
    project_id: int,
    template: JsonObject,
    repository_id: int,
    inventory_id: int,
    environment_id: int,
    view_id: int,
) -> JsonObject:
    """Build the API payload for one Ansible task template."""

    return {
        "project_id": project_id,
        "inventory_id": inventory_id,
        "repository_id": repository_id,
        "environment_ids": [environment_id],
        "view_id": view_id,
        "name": template["name"],
        "playbook": template["playbook"],
        "arguments": json.dumps(
            template.get("arguments") or [],
            separators=(",", ":"),
        ),
        "description": template.get("description", ""),
        "allow_override_args_in_task": False,
        "allow_override_branch_in_task": False,
        "allow_parallel_tasks": False,
        "suppress_success_alerts": False,
        "app": "ansible",
        "git_branch": "",
        "survey_vars": normalize_survey(template.get("survey") or []),
        "type": "",
        "start_version": "",
        "autorun": False,
        "task_params": {},
        "vaults": [],
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def resource_changed(
    current: JsonObject,
    desired: JsonObject,
    *,
    fields: Iterable[str],
    json_fields: Iterable[str] = (),
) -> bool:
    """Return whether selected API fields differ."""

    json_field_set = set(json_fields)
    for field in fields:
        current_value = current.get(field)
        desired_value = desired.get(field)
        if field in json_field_set:
            current_value = _json_value(current_value)
            desired_value = _json_value(desired_value)
        # Semaphore returns null for unset optional fields.
        if current_value is None and isinstance(desired_value, bool):
            current_value = False
        if current_value is None and isinstance(desired_value, list):
            current_value = []
        if current_value is None and isinstance(desired_value, dict):
            current_value = {}
        if current_value is None and desired_value == "":
            current_value = ""
        if current_value != desired_value:
            return True
    return False


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_settings(settings: Settings) -> None:
    parsed = urlparse(settings.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Semaphore URL must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Semaphore URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Semaphore URL must not contain a query or fragment")
    if parsed.path.rstrip("/").endswith("/api"):
        raise ValueError("Semaphore URL is the base URL; remove the trailing /api")
    # The token and any private key travel in the request, so clear text is
    # accepted only on loopback unless the network path is explicitly trusted.
    if (
        parsed.scheme == "http"
        and not is_loopback_host(parsed.hostname)
        and not settings.allow_insecure_http
    ):
        raise ValueError(
            "plain HTTP to a host that isn't loopback sends the API token in clear "
            "text; use HTTPS or set allow_insecure_http"
        )
    if settings.ca_file is not None and parsed.scheme != "https":
        raise ValueError("a custom CA file requires an HTTPS URL")
    if not math.isfinite(settings.timeout) or settings.timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    for label, name in (
        ("token_env", settings.token_env),
        ("private_key_env", settings.private_key_env),
    ):
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"{label} must be an environment-variable name")
    if not settings.credential_login or any(
        character.isspace() for character in settings.credential_login
    ):
        raise ValueError("credential login must be a non-empty name without spaces")


def _config_string(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _config_path(value: str, base_directory: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_directory / path


def parse_config(payload: Any, base_directory: Path | None = None) -> Settings:
    """Parse TOML settings. Relative paths resolve from the config directory."""

    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    base = (base_directory or Path.cwd()).resolve()
    for section_name, section in payload.items():
        if section_name not in CONFIG_KEYS or not isinstance(section, dict):
            raise ValueError(f"unknown configuration section: {section_name}")
        unknown = sorted(set(section) - CONFIG_KEYS[section_name])
        if unknown:
            raise ValueError(
                f"unknown configuration key in [{section_name}]: {', '.join(unknown)}; "
                "secrets belong in the environment variables named by token_env "
                "and private_key_env"
            )

    semaphore = payload.get("semaphore", {})
    credential = payload.get("credential", {})
    manifests = payload.get("manifests", {})

    timeout = semaphore.get("timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int | float) or isinstance(timeout, bool):
        raise ValueError("timeout_seconds must be a number")
    allow_insecure_http = semaphore.get("allow_insecure_http", False)
    if not isinstance(allow_insecure_http, bool):
        raise ValueError("allow_insecure_http must be true or false")
    ca_value = _config_string(semaphore, "ca_file", "")
    paths = manifests.get("paths", [])
    if not isinstance(paths, list) or not all(
        isinstance(path, str) and path for path in paths
    ):
        raise ValueError("manifests.paths must be a list of non-empty strings")

    settings = Settings(
        url=_config_string(semaphore, "url", DEFAULT_URL),
        token_env=_config_string(semaphore, "token_env", DEFAULT_TOKEN_ENV),
        timeout=float(timeout),
        ca_file=_config_path(ca_value, base) if ca_value else None,
        allow_insecure_http=allow_insecure_http,
        credential_login=_config_string(credential, "login", DEFAULT_CREDENTIAL_LOGIN),
        private_key_env=_config_string(
            credential, "private_key_env", DEFAULT_PRIVATE_KEY_ENV
        ),
        manifests=tuple(_config_path(path, base) for path in paths),
    )
    validate_settings(settings)
    return settings


def load_config(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    with path.open("rb") as handle:
        return parse_config(tomllib.load(handle), path.resolve().parent)


def resolve_settings(config: Settings, args: argparse.Namespace) -> Settings:
    """Apply command-line values over local config, which is over defaults."""

    ca_file = config.ca_file
    if args.no_ca_file:
        ca_file = None
    elif args.ca_file is not None:
        ca_file = Path(args.ca_file).expanduser()
    settings = replace(
        config,
        url=args.url if args.url is not None else config.url,
        token_env=args.token_env if args.token_env is not None else config.token_env,
        timeout=args.timeout if args.timeout is not None else config.timeout,
        ca_file=ca_file,
        allow_insecure_http=args.allow_insecure_http or config.allow_insecure_http,
        credential_login=(
            args.credential_login
            if args.credential_login is not None
            else config.credential_login
        ),
        private_key_env=(
            args.private_key_env
            if args.private_key_env is not None
            else config.private_key_env
        ),
        manifests=tuple(args.manifests) if args.manifests else config.manifests,
    )
    validate_settings(settings)
    return settings


def validate_gate(
    *, apply: bool, prune: bool, refresh_credential: bool, confirmations: list[str]
) -> None:
    """Require an exact phrase before a live run deletes or replaces objects."""

    phrases = set(confirmations)
    if phrases - {PRUNE_PHRASE, REFRESH_PHRASE}:
        # The rejected value isn't echoed in case a secret was pasted by mistake.
        raise ValueError("unrecognized --confirm value")
    if phrases and not apply:
        raise ValueError("--confirm is used only with --apply")
    if PRUNE_PHRASE in phrases and not prune:
        raise ValueError(f"{PRUNE_PHRASE} was given without --prune")
    if REFRESH_PHRASE in phrases and not refresh_credential:
        raise ValueError(f"{REFRESH_PHRASE} was given without --refresh-credential")
    if apply and prune and PRUNE_PHRASE not in phrases:
        raise ValueError(
            "--apply --prune deletes templates and views; review the plan, then "
            f"add --confirm {PRUNE_PHRASE}"
        )
    if apply and refresh_credential and REFRESH_PHRASE not in phrases:
        raise ValueError(
            "--apply --refresh-credential replaces stored SSH keys; add "
            f"--confirm {REFRESH_PHRASE}"
        )


def read_token(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ValueError(f"environment variable {env_name!r} is empty or unset")
    return value


def read_private_key(env_name: str) -> str | None:
    """Return the key from the named variable, or None when it's unset."""

    value = os.environ.get(env_name, "").replace("\r\n", "\n").strip()
    if not value:
        return None
    # A file path or public key in the variable would otherwise be stored in
    # Semaphore as a broken credential.
    if not value.startswith("-----BEGIN ") or "PRIVATE KEY-----" not in value:
        raise ValueError(
            f"environment variable {env_name!r} doesn't contain a private key block"
        )
    return value + "\n"


class RejectRedirects(HTTPRedirectHandler):
    """Keep the authorization header from being forwarded to another endpoint."""

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


class SemaphoreClient:
    """Small authenticated client for the Semaphore 2.18 project API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        ca_file: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        context = None
        if urlparse(self.base_url).scheme == "https":
            if ca_file is not None and not ca_file.is_file():
                raise ValueError(f"custom CA file not found: {ca_file}")
            context = ssl.create_default_context(
                cafile=str(ca_file) if ca_file else None
            )
        self._opener = build_opener(RejectRedirects(), HTTPSHandler(context=context))

    def _error_detail(self, exc: HTTPError) -> str:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        detail = " ".join(raw.replace(self._token, "[redacted]").split())[:300]
        return f": {detail}" if detail else ""

    def request(
        self,
        method: str,
        path: str,
        payload: JsonObject | None = None,
        *,
        query: JsonObject | None = None,
        sensitive: bool = False,
        shown_path: str | None = None,
    ) -> Any:
        shown = shown_path or path
        url = f"{self.base_url}/api{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "tools-and-scripts/1",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content = response.read()
        except HTTPError as exc:
            message = f"Semaphore API {method} {shown} returned HTTP {exc.code}"
            if 300 <= exc.code < 400:
                message += "; redirects are rejected, so set the final Semaphore URL"
            elif sensitive:
                # The request carried a secret that an error body could echo.
                message += "; response detail omitted"
            else:
                message += self._error_detail(exc)
            raise RuntimeError(message) from None
        except URLError as exc:
            raise RuntimeError(
                f"Semaphore API {method} {shown} failed: {exc.reason}"
            ) from None
        except OSError as exc:
            raise RuntimeError(
                f"Semaphore API {method} {shown} failed: {exc}"
            ) from None
        if not content:
            return None
        try:
            return json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError(
                f"Semaphore API {method} {shown} returned invalid JSON"
            ) from None

    def get(self, path: str, *, query: JsonObject | None = None) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, payload: JsonObject, *, sensitive: bool = False) -> Any:
        return self.request("POST", path, payload, sensitive=sensitive)

    def put(self, path: str, payload: JsonObject, *, sensitive: bool = False) -> Any:
        return self.request("PUT", path, payload, sensitive=sensitive)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def expire_current_token(self) -> None:
        # Semaphore identifies an API token by its value, so it has to be in the
        # path. The printed path is redacted.
        self.request(
            "DELETE",
            f"/user/tokens/{quote(self._token, safe='')}",
            sensitive=True,
            shown_path="/user/tokens/[redacted]",
        )


class Reconciler:
    """Plan or apply one or more versioned project manifests."""

    def __init__(
        self,
        client: Any,
        *,
        apply: bool,
        private_key: str | None,
        credential_login: str,
        refresh_credential: bool,
        prune: bool,
        out: TextIO | None = None,
    ) -> None:
        self.client = client
        self.apply = apply
        self.private_key = private_key
        self.credential_login = credential_login
        self.refresh_credential = refresh_credential
        self.prune = prune
        self.out = out
        self.actions: list[Action] = []

    @property
    def changes(self) -> list[Action]:
        return [action for action in self.actions if action.changes_state]

    def _record(self, action: Action) -> None:
        self.actions.append(action)
        # Printing as each write completes leaves an accurate list after a
        # failure part-way through an apply run.
        print(action, file=self.out if self.out is not None else sys.stdout, flush=True)

    def _change(
        self,
        verb: str,
        kind: str,
        name: str,
        write: Callable[[], Any] | None = None,
    ) -> Any:
        result = write() if self.apply and write is not None else None
        self._record(Action(verb, kind, name, applied=self.apply))
        return result

    def _list(self, path: str, *, query: JsonObject | None = None) -> list[JsonObject]:
        items = self.client.get(path, query=query) if query else self.client.get(path)
        if not isinstance(items, list) or not all(
            isinstance(item, dict) and "id" in item for item in items
        ):
            raise RuntimeError(
                f"unexpected Semaphore response for {path}; expected a list of objects"
            )
        return items

    @staticmethod
    def _created(result: Any, kind: str, name: str) -> int:
        if not isinstance(result, dict) or "id" not in result:
            raise RuntimeError(f"Semaphore returned no id for created {kind} {name}")
        return int(result["id"])

    def _require_private_key(self, name: str) -> str:
        if self.private_key is None:
            raise ValueError(f"a private key is required to write credential {name}")
        return self.private_key

    @staticmethod
    def named(items: Iterable[JsonObject], name: str) -> JsonObject | None:
        return next((item for item in items if item.get("name") == name), None)

    def report_unmanaged(
        self,
        kind: str,
        items: Iterable[JsonObject],
        managed_ids: set[int],
        *,
        name_field: str = "name",
        ignored_ids: set[int] | None = None,
    ) -> None:
        ignored = ignored_ids or set()
        for item in items:
            item_id = int(item["id"])
            if item_id in managed_ids or item_id in ignored:
                continue
            name = str(item.get(name_field, item["id"]))
            self._record(Action("retain", kind, name, object_id=item_id))

    def reconcile_all(self, manifests: list[JsonObject]) -> None:
        """Reconcile every supplied manifest and report extra live projects."""

        names = [manifest["project"]["name"] for manifest in manifests]
        if len(names) != len(set(names)):
            raise ValueError("duplicate project name across manifests")

        managed_project_ids: set[int] = set()
        for manifest in manifests:
            project_id = self.reconcile(manifest)
            if project_id is not None and project_id >= 0:
                managed_project_ids.add(project_id)
        self.report_unmanaged("project", self._list("/projects"), managed_project_ids)

    def reconcile(self, manifest: JsonObject) -> int | None:
        project_spec = manifest["project"]
        project_name = project_spec["name"]
        project = self.named(self._list("/projects"), project_name)
        if project is None:
            if not self.apply:
                self._change("create", "project", project_name)
                count = len(manifest["templates"])
                self._record(
                    Action(
                        "populate", "project", f"{project_name} with {count} templates"
                    )
                )
                return None
            created = self._change(
                "create",
                "project",
                project_name,
                partial(
                    self.client.post,
                    "/projects",
                    {
                        "name": project_name,
                        "alert": False,
                        "max_parallel_tasks": 0,
                        "type": "",
                    },
                ),
            )
            project_id = self._created(created, "project", project_name)
        else:
            project_id = int(project["id"])

        key_id = self.ensure_key(project_id, project_spec["inventory"]["credential"])
        repository_id = self.ensure_repository(project_id, project_spec, key_id)
        inventory_id = self.ensure_inventory(project_id, project_spec, key_id)
        environment_id = self.ensure_environment(project_id, project_spec)
        view_ids = self.ensure_views(project_id, manifest["views"])
        self.ensure_templates(
            project_id,
            manifest["templates"],
            repository_id=repository_id,
            inventory_id=inventory_id,
            environment_id=environment_id,
            view_ids=view_ids,
        )
        if self.prune:
            self.prune_views(project_id, set(view_ids.values()))
        return project_id

    def _key_payload(self, project_id: int, name: str) -> JsonObject:
        return {
            "name": name,
            "type": "ssh",
            "project_id": project_id,
            "override_secret": True,
            "ssh": {
                "login": self.credential_login,
                "passphrase": "",
                "private_key": self._require_private_key(name),
            },
        }

    def ensure_key(self, project_id: int, name: str) -> int:
        keys = self._list(
            f"/project/{project_id}/keys",
            query={"sort": "name", "order": "asc"},
        )
        key = self.named(keys, name)
        # Semaphore creates a "None" key in every project; it isn't drift.
        system_key_ids = {
            int(item["id"])
            for item in keys
            if item.get("name") == "None" and item.get("type") == "none"
        }
        self.report_unmanaged(
            "credential",
            keys,
            {int(key["id"])} if key is not None else set(),
            ignored_ids=system_key_ids,
        )
        if key is None:
            if not self.apply:
                self._change("create", "credential", name)
                return PENDING_ID
            created = self._change(
                "create",
                "credential",
                name,
                partial(
                    self.client.post,
                    f"/project/{project_id}/keys",
                    self._key_payload(project_id, name),
                    sensitive=True,
                ),
            )
            return self._created(created, "credential", name)
        if self.refresh_credential:
            write = None
            if self.apply:
                write = partial(
                    self.client.put,
                    f"/project/{project_id}/keys/{key['id']}",
                    {"id": key["id"], **self._key_payload(project_id, name)},
                    sensitive=True,
                )
            self._change("refresh", "credential", name, write)
        return int(key["id"])

    def ensure_repository(
        self, project_id: int, project_spec: JsonObject, key_id: int
    ) -> int:
        spec = project_spec["repository"]
        desired = {
            "project_id": project_id,
            "name": spec["name"],
            "git_url": spec["path"],
            "git_branch": spec.get("branch", ""),
            "ssh_key_id": key_id,
        }
        return self.ensure_named_resource(
            project_id=project_id,
            kind="repository",
            collection="repositories",
            desired=desired,
            fields=("name", "git_url", "git_branch", "ssh_key_id"),
        )

    def ensure_inventory(
        self, project_id: int, project_spec: JsonObject, key_id: int
    ) -> int:
        spec = project_spec["inventory"]
        desired = {
            "project_id": project_id,
            "name": spec["name"],
            "inventory": spec["path"],
            "ssh_key_id": key_id,
            "become_key_id": None,
            "repository_id": None,
            "type": "file",
        }
        return self.ensure_named_resource(
            project_id=project_id,
            kind="inventory",
            collection="inventory",
            desired=desired,
            fields=(
                "name",
                "inventory",
                "ssh_key_id",
                "become_key_id",
                "repository_id",
                "type",
            ),
        )

    def ensure_environment(self, project_id: int, project_spec: JsonObject) -> int:
        desired = environment_payload(project_id, project_spec["environment"])
        return self.ensure_named_resource(
            project_id=project_id,
            kind="environment",
            collection="environment",
            desired=desired,
            fields=("name", "json", "env"),
            json_fields=("json", "env"),
        )

    def ensure_named_resource(
        self,
        *,
        project_id: int,
        kind: str,
        collection: str,
        desired: JsonObject,
        fields: Iterable[str],
        json_fields: Iterable[str] = (),
    ) -> int:
        """Create or update one named project resource and report extras."""

        name = str(desired["name"])
        path = f"/project/{project_id}/{collection}"
        items = self._list(path, query={"sort": "name", "order": "asc"})
        current = self.named(items, name)
        self.report_unmanaged(
            kind,
            items,
            {int(current["id"])} if current is not None else set(),
        )
        if current is None:
            created = self._change(
                "create", kind, name, partial(self.client.post, path, desired)
            )
            return self._created(created, kind, name) if self.apply else PENDING_ID
        if resource_changed(current, desired, fields=fields, json_fields=json_fields):
            self._change(
                "update",
                kind,
                name,
                partial(
                    self.client.put,
                    f"{path}/{current['id']}",
                    {**desired, "id": current["id"]},
                ),
            )
        return int(current["id"])

    def ensure_views(self, project_id: int, names: list[str]) -> dict[str, int]:
        path = f"/project/{project_id}/views"
        items = self._list(path)
        by_title = {item.get("title"): item for item in items}
        result: dict[str, int] = {}
        managed_ids: set[int] = set()
        for position, name in enumerate(names):
            current = by_title.get(name)
            if current is None:
                # Semaphore creates All with every project. Its absence means an
                # unexpected layout, so the run stops instead of guessing.
                if name == "All":
                    raise RuntimeError(f"project {project_id} is missing its All view")
                created = self._change(
                    "create",
                    "view",
                    name,
                    partial(
                        self.client.post,
                        path,
                        {"project_id": project_id, "title": name, "position": position},
                    ),
                )
                if not self.apply:
                    result[name] = PENDING_ID
                    continue
                view_id = self._created(created, "view", name)
            else:
                view_id = int(current["id"])
                if int(current.get("position", -1)) != position:
                    self._change(
                        "reorder",
                        "view",
                        name,
                        partial(
                            self.client.put,
                            f"{path}/{view_id}",
                            {
                                "id": view_id,
                                "project_id": project_id,
                                "title": name,
                                "position": position,
                            },
                        ),
                    )
            result[name] = view_id
            managed_ids.add(view_id)
        if not self.prune:
            self.report_unmanaged("view", items, managed_ids, name_field="title")
        return result

    def ensure_templates(
        self,
        project_id: int,
        templates: list[JsonObject],
        *,
        repository_id: int,
        inventory_id: int,
        environment_id: int,
        view_ids: dict[str, int],
    ) -> None:
        path = f"/project/{project_id}/templates"
        items = self._list(path, query={"sort": "name", "order": "asc"})
        by_name: dict[Any, JsonObject] = {}
        for item in items:
            by_name.setdefault(item.get("name"), item)
        managed_ids: set[int] = set()
        for template in templates:
            name = template["name"]
            desired = template_payload(
                project_id=project_id,
                template=template,
                repository_id=repository_id,
                inventory_id=inventory_id,
                environment_id=environment_id,
                view_id=view_ids[template["view"]],
            )
            current = by_name.get(name)
            if current is None:
                self._change(
                    "create", "template", name, partial(self.client.post, path, desired)
                )
                continue
            managed_ids.add(int(current["id"]))
            environments_differ = sorted(current.get("environment_ids") or []) != [
                environment_id
            ]
            if environments_differ or resource_changed(
                current,
                desired,
                fields=TEMPLATE_COMPARE_FIELDS,
                json_fields=TEMPLATE_JSON_FIELDS,
            ):
                self._change(
                    "update",
                    "template",
                    name,
                    partial(
                        self.client.put,
                        f"{path}/{current['id']}",
                        {**desired, "id": current["id"]},
                    ),
                )

        if not self.prune:
            self.report_unmanaged("template", items, managed_ids)
            return
        for current in items:
            if int(current["id"]) not in managed_ids:
                self._change(
                    "delete",
                    "template",
                    str(current.get("name", current["id"])),
                    partial(self.client.delete, f"{path}/{current['id']}"),
                )

    def prune_views(self, project_id: int, managed_ids: set[int]) -> None:
        path = f"/project/{project_id}/views"
        for item in self._list(path):
            if int(item["id"]) not in managed_ids:
                self._change(
                    "delete",
                    "view",
                    str(item.get("title", item["id"])),
                    partial(self.client.delete, f"{path}/{item['id']}"),
                )


def preflight_private_key(
    client: Any, manifests: list[JsonObject], settings: Settings, args: Any
) -> None:
    """Refuse an apply run that would need a missing key before any write."""

    planner = Reconciler(
        client,
        apply=False,
        private_key=None,
        credential_login=settings.credential_login,
        refresh_credential=args.refresh_credential,
        prune=args.prune,
        out=io.StringIO(),
    )
    planner.reconcile_all(manifests)
    needs_key = [
        f"{action.verb} {action.kind} {action.name}"
        for action in planner.actions
        if (action.verb, action.kind) in KEY_ACTIONS
    ]
    if needs_key:
        raise ValueError(
            f"{'; '.join(needs_key)} needs an SSH private key in environment "
            f"variable {settings.private_key_env!r}; nothing was written"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile Semaphore projects, credentials, repositories, inventories, "
            "environments, views, and task templates from project manifests. "
            "The default mode reads Semaphore and prints a plan without writing."
        )
    )
    parser.add_argument(
        "manifests",
        nargs="*",
        type=Path,
        metavar="MANIFEST",
        help=(
            "project manifest copied from manifest.yml.example; overrides "
            "manifests.paths from config"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="local TOML configuration copied from config.example.toml",
    )
    parser.add_argument(
        "--url", help="Semaphore base URL without /api; overrides semaphore.url"
    )
    parser.add_argument(
        "--token-env",
        help="name of the environment variable holding the Semaphore API token",
    )
    parser.add_argument(
        "--timeout", type=float, help="override the HTTP timeout in seconds"
    )
    ca_group = parser.add_mutually_exclusive_group()
    ca_group.add_argument("--ca-file", help="override the private CA PEM path")
    ca_group.add_argument(
        "--no-ca-file", action="store_true", help="use the system trust store"
    )
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="allow plain HTTP to a host that isn't loopback",
    )
    parser.add_argument(
        "--credential-login",
        help="SSH login stored with a created or refreshed credential",
    )
    parser.add_argument(
        "--private-key-env",
        help=(
            "name of the environment variable holding the SSH private key used to "
            "create or refresh a credential"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-manifests",
        action="store_true",
        help="validate manifests and exit without contacting Semaphore",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="create and update Semaphore objects; without it nothing is written",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "also delete templates and views absent from the manifests; with "
            f"--apply this needs --confirm {PRUNE_PHRASE}"
        ),
    )
    parser.add_argument(
        "--refresh-credential",
        action="store_true",
        help=(
            "replace each managed SSH credential with the configured private key; "
            f"with --apply this needs --confirm {REFRESH_PHRASE}"
        ),
    )
    parser.add_argument(
        "--confirm",
        action="append",
        default=[],
        metavar="PHRASE",
        help="exact phrase for --prune or --refresh-credential; repeat for both",
    )
    parser.add_argument(
        "--expire-token",
        action="store_true",
        help="expire the API token after the run, including after a failure",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(load_config(args.config), args)
        validate_gate(
            apply=args.apply,
            prune=args.prune,
            refresh_credential=args.refresh_credential,
            confirmations=args.confirm,
        )
        if args.check_manifests:
            manifests = load_manifests(settings.manifests)
            for path, manifest in zip(settings.manifests, manifests, strict=True):
                print(
                    f"manifest-valid: {path} project={manifest['project']['name']} "
                    f"views={len(manifest['views'])} "
                    f"templates={len(manifest['templates'])}"
                )
            print(f"check-complete: manifests={len(manifests)}")
            return 0
        token = read_token(settings.token_env)
        private_key = None
        if args.apply:
            private_key = read_private_key(settings.private_key_env)
            if args.refresh_credential and private_key is None:
                raise ValueError(
                    "--refresh-credential needs an SSH private key in environment "
                    f"variable {settings.private_key_env!r}"
                )
        client = SemaphoreClient(
            settings.url, token, timeout=settings.timeout, ca_file=settings.ca_file
        )
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"input-error: {exc}", file=sys.stderr)
        return 1

    mode = "apply" if args.apply else "plan"
    reconciler = Reconciler(
        client,
        apply=args.apply,
        private_key=private_key,
        credential_login=settings.credential_login,
        refresh_credential=args.refresh_credential,
        prune=args.prune,
    )
    exit_code = 0
    try:
        manifests = load_manifests(settings.manifests)
        if args.apply and private_key is None:
            preflight_private_key(client, manifests, settings, args)
        print(f"mode={mode}")
        reconciler.reconcile_all(manifests)
        changes = len(reconciler.changes)
        unmanaged = sum(action.verb == "retain" for action in reconciler.actions)
        print(
            f"{mode}-complete: projects={len(manifests)} changes={changes} "
            f"unmanaged-retained={unmanaged}"
        )
        if not args.apply and changes:
            print("next-step: review the plan, then rerun with --apply")
            exit_code = 2
    except Exception as exc:
        label = "input-error" if isinstance(exc, ManifestError) else "reconcile-error"
        print(f"{label}: {exc}", file=sys.stderr)
        if reconciler.apply and reconciler.changes:
            print(
                "partial-apply: the changes printed above were written before the "
                "failure",
                file=sys.stderr,
            )
        exit_code = 1
    finally:
        if args.expire_token:
            try:
                client.expire_current_token()
                print("token-expired: true")
            except Exception as exc:
                print(f"token-expire-error: {exc}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
