#!/usr/bin/env python3
"""Keep a Cloudflare Zero Trust IP list equal to GitHub's published webhook ranges."""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_META_URL = "https://api.github.com/meta"
DEFAULT_API_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_LIST_NAME = "GitHub Webhooks"
DEFAULT_OUTPUT_PATH = Path(
    "/var/lib/prometheus/node-exporter/cloudflare-webhook-ranges.prom"
)
DEFAULT_METRIC_PREFIX = "cloudflare_webhook_ranges"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MINIMUM_RANGES = 3
TOKEN_VARIABLE = "CLOUDFLARE_API_TOKEN"
USER_AGENT = "cloudflare-webhook-ranges/1.0"


@dataclass(frozen=True)
class Settings:
    meta_url: str = DEFAULT_META_URL
    api_url: str = DEFAULT_API_URL
    account_id: str = ""
    list_name: str = DEFAULT_LIST_NAME
    minimum_ranges: int = DEFAULT_MINIMUM_RANGES
    timeout_seconds: float = DEFAULT_TIMEOUT
    output_path: Path = DEFAULT_OUTPUT_PATH
    metric_prefix: str = DEFAULT_METRIC_PREFIX


@dataclass(frozen=True)
class SyncResult:
    count: int
    appended: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.appended or self.removed)


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Issue one HTTPS request and decode its JSON body."""
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(5_000_000).decode("utf-8"))


def github_ranges(settings: Settings) -> set[str]:
    """Read the hooks ranges and refuse an answer too short to be real."""
    document = fetch_json(settings.meta_url, timeout=settings.timeout_seconds)
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, list):
        raise RuntimeError("GitHub meta document has no hooks list")
    ranges = {str(ipaddress.ip_network(value, strict=False)) for value in hooks}
    # A short answer is a broken response, not GitHub shrinking its fleet.
    # Emptying the list would lock every webhook out, so stop instead.
    if len(ranges) < settings.minimum_ranges:
        raise RuntimeError(
            f"GitHub returned {len(ranges)} hook ranges, "
            f"fewer than the minimum of {settings.minimum_ranges}"
        )
    return ranges


class CloudflareLists:
    """The few Zero Trust list calls the sync needs, bound to one account."""

    def __init__(self, settings: Settings, token: str) -> None:
        self.settings = settings
        self.headers = {"Authorization": f"Bearer {token}"}
        self.account_id = settings.account_id or self.only_account()

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        answer = fetch_json(
            self.settings.api_url + path,
            method=method,
            body=body,
            headers=self.headers,
            timeout=self.settings.timeout_seconds,
        )
        if not isinstance(answer, dict) or not answer.get("success"):
            errors = answer.get("errors") if isinstance(answer, dict) else answer
            raise RuntimeError(f"Cloudflare {method} {path} failed: {errors}")
        return answer.get("result")

    def only_account(self) -> str:
        # A token scoped to one account sees exactly one here, which saves
        # the configuration from carrying the account identifier.
        accounts = self.request("GET", "/accounts")
        if not isinstance(accounts, list) or len(accounts) != 1:
            seen = len(accounts) if isinstance(accounts, list) else "no"
            raise RuntimeError(
                f"token sees {seen} accounts; set account_id in the configuration"
            )
        return str(accounts[0]["id"])

    def account(self, method: str, path: str, body: dict[str, Any] | None = None):
        return self.request(method, f"/accounts/{self.account_id}{path}", body)

    def list_id(self) -> str:
        for entry in self.account("GET", "/gateway/lists?type=IP") or []:
            if entry.get("name") == self.settings.list_name:
                return str(entry["id"])
        raise RuntimeError(f"IP list {self.settings.list_name!r} not found")

    def items(self, list_id: str) -> set[str]:
        result = self.account("GET", f"/gateway/lists/{list_id}/items") or []
        return {str(item["value"]) for item in result}

    def update(self, list_id: str, append: set[str], remove: set[str]) -> None:
        self.account(
            "PATCH",
            f"/gateway/lists/{list_id}",
            {
                "append": [{"value": value} for value in sorted(append)],
                "remove": sorted(remove),
            },
        )


def sync(settings: Settings, token: str, *, dry_run: bool) -> SyncResult:
    """Compare both sides and send one PATCH for the difference."""
    wanted = github_ranges(settings)
    lists = CloudflareLists(settings, token)
    list_id = lists.list_id()
    current = lists.items(list_id)
    append, remove = wanted - current, current - wanted
    result = SyncResult(len(wanted), tuple(sorted(append)), tuple(sorted(remove)))
    if not result.changed or dry_run:
        return result
    lists.update(list_id, append, remove)
    # Read back rather than trust the PATCH answer; a partial apply would
    # otherwise look like success.
    after = lists.items(list_id)
    if after != wanted:
        raise RuntimeError(
            f"list holds {sorted(after)} after the update, wanted {sorted(wanted)}"
        )
    return result


def previous_success(path: Path, prefix: str) -> float:
    """Carry the last success time across a failed run so it can be aged."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    match = re.search(
        rf"^{re.escape(prefix)}_last_success_timestamp_seconds ([0-9.]+)$", text, re.M
    )
    return float(match[1]) if match else 0.0


def render_metrics(
    *,
    ok: bool,
    count: int,
    changed: bool,
    last_success: float,
    now: float | None = None,
    prefix: str = DEFAULT_METRIC_PREFIX,
) -> str:
    """Render the five gauges in Prometheus text exposition format."""
    run_time = time.time() if now is None else now
    lines = [
        f"# HELP {prefix}_last_run_ok 1 when the last sync completed, else 0.",
        f"# TYPE {prefix}_last_run_ok gauge",
        f"{prefix}_last_run_ok {int(ok)}",
        f"# HELP {prefix}_count Ranges GitHub publishes, or -1 when unknown.",
        f"# TYPE {prefix}_count gauge",
        f"{prefix}_count {count}",
        f"# HELP {prefix}_changed 1 when the last sync altered the list.",
        f"# TYPE {prefix}_changed gauge",
        f"{prefix}_changed {int(changed)}",
        f"# HELP {prefix}_last_success_timestamp_seconds Unix time of the last "
        "completed sync.",
        f"# TYPE {prefix}_last_success_timestamp_seconds gauge",
        f"{prefix}_last_success_timestamp_seconds {last_success:.3f}",
        f"# HELP {prefix}_last_run_timestamp_seconds Unix time of the last run.",
        f"# TYPE {prefix}_last_run_timestamp_seconds gauge",
        f"{prefix}_last_run_timestamp_seconds {run_time:.3f}",
    ]
    return "\n".join(lines) + "\n"


def validate_output_path(path: Path) -> None:
    """node_exporter only reads .prom files, so refuse anything else early."""
    if path.suffix != ".prom":
        raise ValueError(f"output path must end in .prom: {path}")


def write_textfile(path: Path, content: str) -> None:
    """Write through a temporary file so a scrape never sees a partial file."""
    validate_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with open(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


def parse_settings(payload: Any, base_directory: Path | None = None) -> Settings:
    """Validate a TOML document and fill in documented defaults."""
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a TOML table")
    github = payload.get("github", {})
    cloudflare = payload.get("cloudflare", {})
    metrics = payload.get("metrics", {})
    for name, table in (
        ("github", github),
        ("cloudflare", cloudflare),
        ("metrics", metrics),
    ):
        if not isinstance(table, dict):
            raise ValueError(f"[{name}] must be a TOML table")

    def text(table: dict[str, Any], key: str, default: str, *, empty: bool = False):
        value = table.get(key, default)
        if not isinstance(value, str) or (not empty and not value):
            raise ValueError(f"{key} must be a non-empty string")
        return value

    minimum = github.get("minimum_ranges", DEFAULT_MINIMUM_RANGES)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("github.minimum_ranges must be a positive integer")
    timeout = cloudflare.get("timeout_seconds", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("cloudflare.timeout_seconds must be a positive number")
    if timeout <= 0:
        raise ValueError("cloudflare.timeout_seconds must be a positive number")
    if "api_token" in cloudflare or "token" in cloudflare:
        raise ValueError(
            f"the token belongs in the {TOKEN_VARIABLE} environment variable, "
            "not in the configuration file"
        )

    output = Path(text(metrics, "output_path", str(DEFAULT_OUTPUT_PATH))).expanduser()
    if not output.is_absolute() and base_directory is not None:
        output = base_directory / output
    validate_output_path(output)

    return Settings(
        meta_url=text(github, "meta_url", DEFAULT_META_URL),
        api_url=text(cloudflare, "api_url", DEFAULT_API_URL).rstrip("/"),
        account_id=text(cloudflare, "account_id", "", empty=True),
        list_name=text(cloudflare, "list_name", DEFAULT_LIST_NAME),
        minimum_ranges=minimum,
        timeout_seconds=float(timeout),
        output_path=output,
        metric_prefix=text(metrics, "metric_prefix", DEFAULT_METRIC_PREFIX),
    )


def load_settings(path: Path | None) -> Settings:
    """Load an explicit configuration, else the local one beside the script."""
    if path is None:
        local = Path(__file__).resolve().with_name("config.local.toml")
        if not local.is_file():
            return Settings()
        path = local
    with path.open("rb") as handle:
        return parse_settings(tomllib.load(handle), path.resolve().parent)


def resolve_settings(args: argparse.Namespace) -> Settings:
    """Command line beats the configuration file, which beats the defaults."""
    base = load_settings(args.config)
    output = base.output_path
    if args.output is not None:
        output = args.output.expanduser()
        validate_output_path(output)
    timeout = base.timeout_seconds if args.timeout is None else args.timeout
    if timeout <= 0:
        raise ValueError("--timeout must be a positive number")
    return Settings(
        meta_url=base.meta_url if args.meta_url is None else args.meta_url,
        api_url=base.api_url if args.api_url is None else args.api_url.rstrip("/"),
        account_id=base.account_id if args.account_id is None else args.account_id,
        list_name=base.list_name if args.list_name is None else args.list_name,
        minimum_ranges=base.minimum_ranges,
        timeout_seconds=timeout,
        output_path=output,
        metric_prefix=base.metric_prefix,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Keep a Cloudflare Zero Trust IP list equal to the webhook ranges "
            f"GitHub publishes. The token is read from {TOKEN_VARIABLE}."
        )
    )
    parser.add_argument("--config", type=Path, help="path to a TOML configuration")
    parser.add_argument("--meta-url", help="override github.meta_url")
    parser.add_argument("--api-url", help="override cloudflare.api_url")
    parser.add_argument("--account-id", help="override cloudflare.account_id")
    parser.add_argument("--list-name", help="override cloudflare.list_name")
    parser.add_argument(
        "--timeout", type=float, help="override cloudflare.timeout_seconds"
    )
    parser.add_argument("--output", type=Path, help="override metrics.output_path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the ranges that would be added or removed and change nothing",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the metrics instead of writing the textfile",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(args)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"configuration-error: {exc}", file=sys.stderr)
        return 1
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        print(f"configuration-error: {TOKEN_VARIABLE} is not set", file=sys.stderr)
        return 1

    prefix = settings.metric_prefix
    try:
        result = sync(settings, token, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"sync-error: {exc}", file=sys.stderr)
        if not args.dry_run:
            body = render_metrics(
                ok=False,
                count=-1,
                changed=False,
                last_success=previous_success(settings.output_path, prefix),
                prefix=prefix,
            )
            emit(args, settings, body)
        return 1

    verb = "would add" if args.dry_run else "added"
    if result.changed:
        print(f"{verb} {list(result.appended)}, removed {list(result.removed)}")
    else:
        print(f"{result.count} ranges, no change")
    if args.dry_run:
        return 0
    body = render_metrics(
        ok=True,
        count=result.count,
        changed=result.changed,
        last_success=time.time(),
        prefix=prefix,
    )
    return emit(args, settings, body)


def emit(args: argparse.Namespace, settings: Settings, body: str) -> int:
    if args.stdout:
        sys.stdout.write(body)
        return 0
    try:
        write_textfile(settings.output_path, body)
    except OSError as exc:
        print(f"write-error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
