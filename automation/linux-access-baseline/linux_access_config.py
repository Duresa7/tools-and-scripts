"""Validate non-secret settings and static inventory without contacting hosts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
GROUPS = (
    "automation_account_targets",
    "automation_account_key_only",
    "operator_nopasswd_targets",
)
SETTINGS = {
    "automation_account_user",
    "automation_account_home",
    "automation_account_key_file",
    "automation_account_password_env",
    "operator_user",
    "operator_home",
    "operator_password_env",
    "root_password_env",
    "rootpw_dropin",
    "automation_account_sudoers_dropin",
    "operator_sudoers_dropin",
}
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
ALIAS = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def read_mapping(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("YAML must contain a mapping")
    return payload


def validate_settings(settings: dict) -> None:
    if set(settings) != SETTINGS:
        raise ValueError("settings must contain only the documented non-secret fields")
    if any(not isinstance(value, str) or not value for value in settings.values()):
        raise ValueError("every setting must be a non-empty string")
    for prefix in ("automation_account", "operator"):
        account = settings[f"{prefix}_user"]
        if not ACCOUNT.fullmatch(account) or account in {"root", "administrator"}:
            raise ValueError("managed accounts must be ordinary Linux account names")
        if not re.fullmatch(r"/home/[^/\s]+", settings[f"{prefix}_home"]):
            raise ValueError("managed homes must be dedicated directories under /home")
        if not re.fullmatch(
            r"/etc/sudoers.d/90-[a-z0-9_-]+", settings[f"{prefix}_sudoers_dropin"]
        ):
            raise ValueError("NOPASSWD paths must be dedicated 90- sudoers drop-ins")
    for suffix in ("user", "home", "sudoers_dropin"):
        if settings[f"automation_account_{suffix}"] == settings[f"operator_{suffix}"]:
            raise ValueError("managed accounts, homes and drop-ins must be distinct")
    if not re.fullmatch(r"/etc/sudoers.d/00-[a-z0-9_-]+", settings["rootpw_dropin"]):
        raise ValueError("rootpw path must be a dedicated 00- sudoers drop-in")
    for name, value in settings.items():
        if name.endswith("_env") and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
            raise ValueError("password settings contain environment-variable NAMES")


def resolve_settings(config: Path | None, overrides: dict | None = None) -> dict:
    settings = read_mapping(ROOT / "config.example.yml")
    if config is not None:
        settings.update(read_mapping(config))
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    validate_settings(settings)
    return settings


def collect_hosts(group: dict, inherited: dict | None = None) -> dict:
    if not isinstance(group, dict):
        raise ValueError("inventory groups must be mappings")
    variables = {**(inherited or {}), **(group.get("vars") or {})}
    hosts = {}
    for name, values in (group.get("hosts") or {}).items():
        if not ALIAS.fullmatch(name):
            raise ValueError("inventory aliases must be plain host names")
        hosts[name] = {**variables, **(values or {})}
    for child in (group.get("children") or {}).values():
        for host, values in collect_hosts(child or {}, variables).items():
            hosts[host] = {**hosts.get(host, {}), **values}
    return hosts


def reject_inventory_credentials(payload: object) -> None:
    if isinstance(payload, dict):
        for name, value in payload.items():
            key = str(name).lower()
            if (
                "password" in key
                or key
                in {"ansible_ssh_pass", "ansible_become_pass", "ansible_sudo_pass"}
                or key.startswith("vault_")
            ):
                raise ValueError("inventory must not contain credentials")
            if key.startswith(("ansible_become", "ansible_sudo")):
                raise ValueError(
                    "inventory must not override playbook elevation controls"
                )
            reject_inventory_credentials(value)
    elif isinstance(payload, list):
        for value in payload:
            reject_inventory_credentials(value)


def validate_inventory(payload: dict) -> dict:
    reject_inventory_credentials(payload)
    all_group = payload.get("all") or {}
    children = all_group.get("children") or {}
    if set(children) != set(GROUPS):
        raise ValueError("inventory requires the three documented target groups")
    inherited = all_group.get("vars") or {}
    groups = {name: collect_hosts(children[name] or {}, inherited) for name in GROUPS}
    create, key_only, sudo = (groups[name] for name in GROUPS)
    if not create and not key_only:
        raise ValueError("inventory has no managed hosts")
    if create.keys() & key_only.keys():
        raise ValueError("creation and key-only groups must be disjoint")
    if sudo.keys() - (create.keys() | key_only.keys()):
        raise ValueError("NOPASSWD targets must already be managed hosts")
    for host, values in collect_hosts(all_group).items():
        user = values.get("ansible_user", "")
        if not ACCOUNT.fullmatch(user) or user in {"root", "administrator"}:
            raise ValueError(f"{host}: an ordinary connection account is required")
        connection = values.get("ansible_connection", "ssh")
        if connection not in {"ssh", "local"}:
            raise ValueError(f"{host}: use ssh or guarded local transport")
        if connection == "local" and not values.get("baseline_controller_hostname"):
            raise ValueError(f"{host}: local transport requires a controller hostname")
    return groups
