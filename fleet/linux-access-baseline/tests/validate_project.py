#!/usr/bin/env python3
"""Validate the baseline project and its safeguards without contacting any host."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "linux_access_config", ROOT / "linux_access_config.py"
)
assert SPEC and SPEC.loader
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)
PLAYBOOKS = (
    "automation-account.yml",
    "account-passwords.yml",
    "sudoers-nopasswd.yml",
    "sudoers-rootpw.yml",
)


def validate_key_example(payload: dict) -> None:
    if set(payload) != {"automation_account_public_key"}:
        raise ValueError("public-key example must contain only its placeholder field")
    key = payload["automation_account_public_key"]
    if not isinstance(key, str):
        raise ValueError("public-key example must be a string")
    fields = key.split()
    if (
        len(fields) != 3
        or fields[0] not in {"ssh-ed25519", "ssh-rsa"}
        or fields[1] != "REPLACE_WITH_PUBLIC_KEY_DATA"
        or fields[2] != "automation@example.net"
    ):
        raise ValueError("public-key example must hold only the documented placeholder")


def validate_playbook(name: str, play: dict) -> list[str]:
    errors = []

    def require(condition: bool, detail: str) -> None:
        if not condition:
            errors.append(f"{name}: {detail}")

    require(play.get("serial") == 1, "must run exactly one host at a time")
    require(play.get("strategy") == "linear", "must keep linear execution")
    require(play.get("any_errors_fatal") is True, "must abort on first failure")
    require(play.get("become") is True, "must elevate account and policy operations")
    require(play.get("become_user") == "root", "policy operations must run as root")
    require(
        any(
            t.get("ansible.builtin.import_tasks") == "tasks/preflight.yml"
            for t in play.get("pre_tasks", [])
        ),
        "must import the configuration, confirmation and controller guards",
    )
    tasks = play.get("tasks", [])
    by_register = {t["register"]: t for t in tasks if "register" in t}
    by_name = {t.get("name"): t for t in tasks}
    for task in tasks:
        copy = task.get("ansible.builtin.copy", {})
        if "sudoers" in str(copy.get("dest", "")) or (
            name == "sudoers-rootpw.yml" and copy
        ):
            require(
                copy.get("validate") == "visudo -cf %s",
                "sudoers copy needs visudo validation",
            )
            require(copy.get("mode") == "0440", "sudoers files need mode 0440")
        if "password" in task.get("ansible.builtin.user", {}):
            require(task.get("no_log") is True, "password writes must suppress logs")
        command = task.get("ansible.builtin.command", {})
        argv = command.get("argv", [])
        require(
            all(isinstance(arg, str) for arg in argv),
            "command arguments must be strings",
        )
        if "stdin" in command:
            require(task.get("no_log") is True, "password probes must suppress logs")
        if task.get("ansible.builtin.raw"):
            require(
                task.get("become_method") == "ansible.builtin.su",
                "authentication must use su",
            )
            require(task.get("no_log") is True, "authentication must suppress logs")
        if "password" in task.get("ansible.builtin.user", {}):
            require(
                task.get("check_mode") is not False,
                "password writes must honor check mode",
            )
        if copy:
            require(
                task.get("check_mode") is not False, "file writes must honor check mode"
            )

    if name == "automation-account.yml":
        account = next(
            t["ansible.builtin.user"] for t in tasks if "ansible.builtin.user" in t
        )
        require(
            account.get("update_password") == "on_create",
            "existing passwords must never be locked or reset",
        )
        require(
            "Refuse any change to an existing automation password" in by_name,
            "must verify preservation of existing passwords",
        )
        key_copy = next(
            t["ansible.builtin.copy"] for t in tasks if "ansible.builtin.copy" in t
        )
        require(
            key_copy.get("content") == "{{ automation_account_public_key_line }}\n",
            "must install exactly one key",
        )
        require(
            key_copy.get("validate") == "ssh-keygen -l -f %s",
            "must validate key before replacement",
        )
        require(key_copy.get("mode") == "0600", "key file must be private to its owner")
        require(
            any(t.get("ansible.builtin.file", {}).get("mode") == "0700" for t in tasks),
            "SSH directory must have mode 0700",
        )
        require(
            "Fail when the account is missing on a key-only host" in by_name,
            "key-only targets must already have the account",
        )
        require(
            "Require SSH password authentication to remain disabled" in by_name,
            "must keep the SSH login key-only",
        )
        require(
            "Confirm sshd will actually admit the account" in by_name,
            "must check sshd account allowlists",
        )
        require(
            "automation_account_public_key" not in play.get("vars", {}),
            "playbook must not hardcode the key",
        )
    if name in {"account-passwords.yml", "sudoers-rootpw.yml"}:
        for credential in ("root_password", "operator_password"):
            expression = play.get("vars", {}).get(credential, "")
            require(
                "lookup('ansible.builtin.env', " + credential + "_env)" in expression,
                "passwords must resolve from named environment variables or Vault",
            )
            require(
                "vault_" + credential in expression,
                "encrypted variables must be supported",
            )
        require(
            any(
                t.get("become") is False
                and t.get("ansible.builtin.command", {}).get("argv")
                == ["sudo", "-k", "-n", "true"]
                for t in tasks
            ),
            "must recheck the connection account recovery grant",
        )
    if name == "account-passwords.yml":
        writes = [
            t["ansible.builtin.user"] for t in tasks if "ansible.builtin.user" in t
        ]
        require(
            len(writes) == 3
            and all(t.get("update_password") == "always" for t in writes),
            "password convergence must update existing passwords",
        )
        require(
            set(by_register) >= {"root_auth", "operator_auth"},
            "must authenticate both account passwords",
        )
    if name == "sudoers-nopasswd.yml":
        require(
            "Require separate approval for passwordless grants" in by_name,
            "NOPASSWD requires separate confirmation",
        )
        require(
            sum("ansible.builtin.copy" in t for t in tasks) == 2,
            "must validate both NOPASSWD drop-ins",
        )
    if name == "sudoers-rootpw.yml":
        proof = by_register.get("root_auth", {})
        write = by_name.get("Point sudo at the root password", {})
        confirmation = by_name.get("Confirm root authenticated", {})
        require(
            bool(proof) and bool(write) and bool(confirmation),
            "must prove root authentication before the rootpw write",
        )
        if proof and write and confirmation:
            require(
                tasks.index(proof) < tasks.index(confirmation) < tasks.index(write),
                "root-password proof and assertion must precede rootpw",
            )
        require(
            "root_passwd_status.stdout.split()[1] is match('^P')" in str(tasks),
            "must refuse a locked root password",
        )
        policy = by_register.get("operator_sudo_list", {})
        require(
            policy.get("ansible.builtin.command", {}).get("argv")
            == ["sudo", "-l", "-U", "{{ operator_user }}"],
            "must read resolved policy with sudo -l -U",
        )
        require(
            policy.get("become", play.get("become")) is True
            and policy.get("become_user", play.get("become_user")) == "root",
            "resolved policy must be read as root",
        )
        negative = by_register.get("operator_sudo_loginpw", {})
        require(
            "operator_password_gated | bool" in negative.get("when", []),
            "negative proof must skip NOPASSWD accounts",
        )
        require(
            "untestable - operator still NOPASSWD" in str(tasks),
            "NOPASSWD must report untestable",
        )
    return errors


def validate_project(root: Path, inventory: str = "hosts.yml.example") -> list[str]:
    errors = []
    try:
        settings = CONFIG.read_mapping(root / "config.example.yml")
        local = root / "config.local.yml"
        if inventory != "hosts.yml.example" and local.exists():
            settings.update(CONFIG.read_mapping(local))
        CONFIG.validate_settings(settings)
        CONFIG.validate_inventory(CONFIG.read_mapping(root / "inventory" / inventory))
        validate_key_example(
            CONFIG.read_mapping(root / "vars" / "automation-key.yml.example")
        )
        for name in PLAYBOOKS:
            payload = yaml.safe_load(
                (root / "playbooks" / name).read_text(encoding="utf-8")
            )
            if not isinstance(payload, list) or len(payload) != 1:
                raise ValueError("each playbook must have exactly one guarded play")
            errors.extend(validate_playbook(name, payload[0]))
        preflight = (root / "playbooks" / "tasks" / "preflight.yml").read_text(
            encoding="utf-8"
        )
        for marker in (
            "APPLY ACCESS BASELINE",
            "ansible_check_mode",
            "ansible_facts.hostname == baseline_controller_hostname",
            "baseline_connection_uid.stdout | trim != '0'",
            'argv: [sudo, -k, -n, "true"]',
        ):
            if marker not in preflight:
                errors.append("preflight is missing an access safeguard")
        ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        for path in (
            "config.local.yml",
            "inventory/hosts.yml",
            "vars/automation-key.yml",
            "vars/secrets.local.yml",
        ):
            if path not in ignored:
                errors.append(f"local configuration must be ignored: {path}")
        manifest = CONFIG.read_mapping(root / "semaphore" / "task-templates.yml")
        seen = set()
        for template in manifest["templates"]:
            name = template["name"]
            if name in seen or template["view"] not in manifest["views"]:
                errors.append(
                    "Semaphore templates need unique names and declared views"
                )
            seen.add(name)
            if template["playbook"] not in {f"playbooks/{name}" for name in PLAYBOOKS}:
                errors.append("Semaphore template references an unknown playbook")
            for survey in template.get("survey", []):
                if "password" in survey["name"] and survey.get("type") != "secret":
                    errors.append("password surveys must use secret fields")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        StopIteration,
        yaml.YAMLError,
    ):
        errors.append(
            "invalid or missing project file; check non-secret YAML structure"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory", default="hosts.yml.example", help="filename under inventory/"
    )
    args = parser.parse_args(argv)
    if Path(args.inventory).name != args.inventory:
        parser.error("--inventory must be a filename under inventory/")
    errors = validate_project(ROOT, args.inventory)
    for error in errors:
        print(f"validation-error: {error}")
    if errors:
        return 1
    print(
        "Validation passed: inventory, publication placeholder and access safeguards."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
