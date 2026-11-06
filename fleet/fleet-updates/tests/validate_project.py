#!/usr/bin/env python3
"""Validate fleet inventory and project files without contacting hosts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOKS = ("playbooks/os-update.yml", "playbooks/docker-compose-update.yml")
REQUIRED_GROUPS = ("os_update_targets", "docker_compose_targets")


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys before YAML can silently replace approved scope."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def parse_yaml(contents: str):
    try:
        return yaml.load(contents, Loader=UniqueLoader)
    except yaml.YAMLError:
        raise ValueError("invalid YAML (contents suppressed)") from None


def load_yaml(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def collect_hosts(group: dict, inherited: dict | None = None) -> dict:
    group = mapping(group, "group")
    variables = {**(inherited or {}), **mapping(group.get("vars", {}), "vars")}
    hosts = {}
    for name, values in mapping(group.get("hosts", {}), "hosts").items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("host aliases must be literal names, not patterns")
        hosts[name] = {**variables, **mapping(values or {}, "host variables")}
    for child in mapping(group.get("children", {}), "children").values():
        for name, values in collect_hosts(child, variables).items():
            if name in hosts:
                raise ValueError("host appears more than once within a target group")
            hosts[name] = values
    return hosts


def string_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(set(value)) == len(value)
    )


def validate_projects(projects, errors: list[str], host: str) -> None:
    if not isinstance(projects, list) or not projects:
        errors.append(f"{host}: compose_projects must be a non-empty list")
        return
    names = set()
    sources = set()
    for entry in projects:
        if not isinstance(entry, dict):
            errors.append(f"{host}: project must be a mapping")
            continue
        name, source = entry.get("name"), entry.get("project_src")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            errors.append(f"{host}: invalid project name")
        elif name in names:
            errors.append(f"{host}: duplicate compose project name")
        else:
            names.add(name)
        if (
            not isinstance(source, str)
            or not source.startswith("/")
            or ".." in PurePosixPath(source).parts
            or source == "/"
        ):
            errors.append(f"{host}: project_src must be an absolute project directory")
        elif str(PurePosixPath(source)) in sources:
            errors.append(f"{host}: duplicate project directory")
        else:
            sources.add(str(PurePosixPath(source)))
        if entry.get("pull", "always") not in ("always", "missing", "never", "policy"):
            errors.append(f"{host}: invalid pull policy")
        if not isinstance(entry.get("remove_orphans", False), bool):
            errors.append(f"{host}: remove_orphans must be boolean")
        for field in ("profiles", "files"):
            if field in entry and not string_list(entry[field]):
                errors.append(f"{host}: {field} must be unique non-empty strings")
        if string_list(entry.get("files")) and any(
            PurePosixPath(item).is_absolute() or ".." in PurePosixPath(item).parts
            for item in entry["files"]
        ):
            errors.append(f"{host}: files must stay within project_src")


def reject_secrets(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in key.lower() for word in ("password", "token", "secret")):
                raise ValueError("use hidden credential prompts, not inventory secrets")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str) and "PRIVATE KEY" in value:
        raise ValueError("private key material is not inventory configuration")


def validate_inventory(inventory) -> list[str]:
    errors: list[str] = []
    try:
        reject_secrets(inventory)
        all_group = mapping(mapping(inventory, "inventory").get("all"), "all")
        children = mapping(all_group.get("children"), "children")
        variables = mapping(all_group.get("vars", {}), "vars")
        groups = {}
        for name in REQUIRED_GROUPS:
            groups[name] = collect_hosts(children.get(name, {}))
            if not groups[name]:
                errors.append(f"{name}: required group must have hosts")
        os_hosts, compose_hosts = (groups[name] for name in REQUIRED_GROUPS)
        if set(compose_hosts) - set(os_hosts):
            errors.append("every compose host must also be an OS-update host")
        for host in os_hosts:
            values = {**variables, **os_hosts[host], **compose_hosts.get(host, {})}
            user = values.get("ansible_user")
            if (
                not isinstance(user, str)
                or not user
                or user in {"root", "administrator"}
            ):
                errors.append(f"{host}: declare an ordinary ansible_user")
            connection = values.get("ansible_connection", "ssh")
            if connection not in {"ssh", "local"}:
                errors.append(f"{host}: connection must be ssh or local")
            if connection == "local" and not values.get("local_expected_hostname"):
                errors.append(f"{host}: local connection needs local_expected_hostname")
            if values.get("reboot", "report") not in {"report", "auto"}:
                errors.append(f"{host}: reboot must be report or auto")
            if connection == "local" and values.get("reboot") == "auto":
                errors.append(f"{host}: automatic local reboot is forbidden")
            if host in compose_hosts:
                validate_projects(values.get("compose_projects"), errors, host)
    except (ValueError, TypeError, RecursionError):
        errors.append("invalid inventory structure or unsafe credentials")
    return errors


def validate_project(root: Path, inventory: str = "hosts.yml.example") -> list[str]:
    errors: list[str] = []
    try:
        errors.extend(validate_inventory(load_yaml(root / "inventory" / inventory)))
        for playbook in PLAYBOOKS:
            content = load_yaml(root / playbook)
            if not isinstance(content, list) or not content:
                errors.append(f"invalid playbook {playbook}")
        path = root / "semaphore" / "task-templates.yml"
        if path.exists():
            manifest = mapping(load_yaml(path), "Semaphore manifest")
            views = manifest.get("views", [])
            names = set()
            for template in manifest.get("templates", []):
                template = mapping(template, "template")
                name = template.get("name")
                if not isinstance(name, str) or not name or name in names:
                    errors.append("Semaphore names must be unique non-empty strings")
                else:
                    names.add(name)
                if template.get("playbook") not in PLAYBOOKS:
                    errors.append("Semaphore template must use a project playbook")
                if template.get("view") not in views:
                    errors.append("Semaphore template has an unknown view")
    except (OSError, ValueError, TypeError, RecursionError):
        errors.append("project file is missing, unreadable, or malformed")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default="hosts.yml.example")
    args = parser.parse_args(argv)
    errors = validate_project(ROOT, args.inventory)
    if errors:
        print("Validation failed:\n" + "\n".join(f"- {error}" for error in errors))
        return 1
    print("Validation passed: inventory, playbooks, and optional Semaphore templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
