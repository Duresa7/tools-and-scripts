#!/usr/bin/env python3
"""Validate local inventory, approved scope, playbooks, and Semaphore references."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    "node_exporter_targets",
    "cadvisor_targets",
    "textfile_collector_targets",
    "wud_targets",
)
PLAYBOOKS = tuple(
    f"playbooks/{name}.yml"
    for name in ("node-exporter", "cadvisor", "textfile-collectors", "wud")
)


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently broadening a target scope."""


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def read_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)


def mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def collect_hosts(group: dict, inherited: dict | None = None) -> dict:
    group = mapping(group, "inventory group")
    variables = {**(inherited or {}), **mapping(group.get("vars", {}), "group vars")}
    hosts = {
        name: {**variables, **mapping(values or {}, "host vars")}
        for name, values in mapping(group.get("hosts", {}), "hosts").items()
    }
    for child in mapping(group.get("children", {}), "children").values():
        hosts.update(collect_hosts(child or {}, variables))
    return hosts


def reject_secrets(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower().endswith(("password", "passwd", "token", "private_key")):
                raise ValueError(
                    "credential values are forbidden; use environment names"
                )
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)


def validate_inventory(inventory: dict) -> list[str]:
    reject_secrets(inventory)
    all_group = mapping(mapping(inventory, "inventory").get("all"), "all")
    variables = mapping(all_group.get("vars", {}), "all.vars")
    scope = mapping(variables.get("monitoring_scope"), "monitoring_scope")
    children = mapping(all_group.get("children"), "all.children")
    # Ansible combines direct host variables across group memberships.
    host_values: dict[str, dict] = {}
    for group in children.values():
        for name, values in collect_hosts(group or {}).items():
            current = host_values.setdefault(name, {})
            for key, value in values.items():
                if key in current and current[key] != value:
                    raise ValueError(f"{name}: conflicting host variable {key}")
                current[key] = value
    errors = []
    for group_name in GROUPS:
        if group_name not in children:
            errors.append(f"missing required group {group_name}")
            continue
        hosts = collect_hosts(children[group_name] or {})
        approved = scope.get(group_name)
        if (
            not isinstance(approved, list)
            or not all(isinstance(name, str) for name in approved)
            or len(approved) != len(set(approved))
            or set(approved) != set(hosts)
        ):
            errors.append(f"{group_name}: membership differs from monitoring_scope")
        for name in hosts:
            values = {**variables, **host_values[name]}
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
                errors.append(f"{group_name}: invalid host alias")
            user = values.get("ansible_user", "")
            if (
                not isinstance(user, str)
                or not user
                or user.lower()
                in {
                    "root",
                    "administrator",
                }
            ):
                errors.append(f"{name}: ordinary ansible_user is required")
            if (
                not values.get("ansible_host")
                or values.get("ansible_become") is not True
            ):
                errors.append(
                    f"{name}: ansible_host and ansible_become=true are required"
                )
            if group_name == "wud_targets":
                cron = values.get("wud_cron", "")
                if not isinstance(cron, str) or len(cron.split()) != 5:
                    errors.append(f"{name}: five-field wud_cron is required")
            settings = mapping(values.get("monitoring", {}), "monitoring")
            for key in ("node_exporter_port", "cadvisor_host_port", "wud_host_port"):
                port = settings.get(key, 9100)
                if type(port) is not int or not 1 <= port <= 65535:
                    errors.append(f"{name}: {key} must be a TCP port")
            env_name = settings.get("wud_password_env", "WUD_PASSWORD")
            if not isinstance(env_name, str) or not re.fullmatch(
                r"[A-Z_][A-Z0-9_]*", env_name
            ):
                errors.append(
                    f"{name}: wud_password_env must name an environment variable"
                )
            delegate = settings.get("wud_prometheus_host", "")
            if delegate and delegate not in host_values:
                errors.append(f"{name}: wud_prometheus_host must be an inventory alias")
    return errors


def validate_manifest(root: Path, manifest: dict) -> list[str]:
    manifest = mapping(manifest, "Semaphore manifest")
    views = manifest.get("views", [])
    templates = manifest.get("templates", [])
    if not isinstance(views, list) or not isinstance(templates, list):
        raise ValueError("Semaphore views and templates must be lists")
    names = set()
    covered = set()
    errors = []
    for template in templates:
        template = mapping(template, "template")
        name = template.get("name")
        if not isinstance(name, str) or not name or name in names:
            errors.append("Semaphore template names must be nonempty and unique")
        names.add(name)
        playbook = template.get("playbook", "")
        if playbook not in PLAYBOOKS or not (root / playbook).is_file():
            errors.append("Semaphore references an unknown playbook")
        covered.add(playbook)
        if template.get("view") not in views:
            errors.append("Semaphore references an unknown view")
        if template.get("arguments") != ["--check"]:
            errors.append("tracked Semaphore templates must default to --check")
    if covered != set(PLAYBOOKS):
        errors.append("Semaphore must cover all four playbooks")
    return errors


def validate_project(root: Path, inventory: str = "hosts.yml.example") -> list[str]:
    try:
        path = Path(inventory)
        if not path.is_absolute():
            path = root / "inventory" / path
        errors = validate_inventory(read_yaml(path))
        for playbook in PLAYBOOKS:
            data = read_yaml(root / playbook)
            if not isinstance(data, list) or len(data) != 1:
                errors.append(f"{playbook}: expected one play")
        if not (root / "playbooks/files/dnf-updates-textfile.sh").is_file():
            errors.append("bundled DNF collector is missing")
        errors.extend(
            validate_manifest(root, read_yaml(root / "semaphore/task-templates.yml"))
        )
        return errors
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        return [f"invalid project input: {exc}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        default="hosts.yml.example",
        help="inventory filename under inventory/, or an absolute path",
    )
    args = parser.parse_args(argv)
    errors = validate_project(ROOT, args.inventory)
    if errors:
        for error in errors:
            print(f"validation-failed: {error}", file=sys.stderr)
        return 1
    print(
        "Validation passed: four target groups, four playbooks, "
        "and Semaphore templates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
