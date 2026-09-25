import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module("fleet_validator_test", ROOT / "tests" / "validate_project.py")
CONFIGURE = load_module("fleet_configure_test", ROOT / "configure.py")


@pytest.fixture
def inventory():
    return VALIDATOR.load_yaml(ROOT / "inventory" / "hosts.yml.example")


def project(inventory):
    return inventory["all"]["children"]["docker_compose_targets"]["hosts"][
        "web-01.example.net"
    ]["compose_projects"]


def test_example_and_project_pass(inventory):
    assert VALIDATOR.validate_inventory(inventory) == []
    assert VALIDATOR.validate_project(ROOT) == []
    assert (ROOT / "config.example.yml").read_bytes() == (
        ROOT / "inventory" / "hosts.yml.example"
    ).read_bytes()


@pytest.mark.parametrize("value", [None, [], "bad", {"all": []}])
def test_malformed_inventory_fails(value):
    assert VALIDATOR.validate_inventory(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("project_src", "relative"),
        ("project_src", "/srv/../etc"),
        ("profiles", ["a", "a"]),
        ("profiles", [{}]),
        ("files", ["../compose.yml"]),
        ("files", ["/etc/compose.yml"]),
        ("pull", "sometimes"),
        ("remove_orphans", "false"),
    ],
)
def test_project_rejects_unsafe_shape(inventory, field, value):
    project(inventory)[0][field] = value
    assert VALIDATOR.validate_inventory(inventory)


def test_duplicate_project_rejected(inventory):
    project(inventory).append(project(inventory)[0].copy())
    assert any("duplicate" in e for e in VALIDATOR.validate_inventory(inventory))


def test_scope_is_configurable_but_compose_must_be_subset(inventory):
    hosts = inventory["all"]["children"]["os_update_targets"]["hosts"]
    hosts["batch-02.example.net"] = {"ansible_host": "203.0.113.20"}
    assert VALIDATOR.validate_inventory(inventory) == []
    del hosts["web-01.example.net"]
    assert any("every compose" in e for e in VALIDATOR.validate_inventory(inventory))


def test_local_connection_requires_identity_and_refuses_auto(inventory):
    host = inventory["all"]["children"]["os_update_targets"]["hosts"][
        "worker-01.example.net"
    ]
    host["ansible_connection"] = "local"
    assert VALIDATOR.validate_inventory(inventory)
    host["local_expected_hostname"] = "worker-01"
    assert VALIDATOR.validate_inventory(inventory) == []
    host["reboot"] = "auto"
    assert VALIDATOR.validate_inventory(inventory)


def test_inherited_vars():
    assert VALIDATOR.collect_hosts(
        {
            "vars": {"ansible_user": "operator"},
            "children": {"guests": {"hosts": {"web": {"ansible_host": "192.0.2.20"}}}},
        }
    ) == {"web": {"ansible_user": "operator", "ansible_host": "192.0.2.20"}}


def test_duplicate_yaml_rejected(tmp_path):
    path = tmp_path / "inventory.yml"
    path.write_text("all: {}\nall: {}\n")
    with pytest.raises(ValueError, match="unique"):
        VALIDATOR.load_yaml(path)


def test_secrets_rejected_without_echoing(inventory):
    inventory["all"]["vars"]["ansible_password"] = "fixture-sensitive-value"
    errors = VALIDATOR.validate_inventory(inventory)
    assert errors
    assert "fixture-sensitive-value" not in str(errors)


def test_configure_exclusive_and_private(tmp_path):
    output = tmp_path / "hosts.yml"
    assert CONFIGURE.main(["--output", str(output)]) == 0
    assert VALIDATOR.validate_inventory(VALIDATOR.load_yaml(output)) == []
    before = output.read_bytes()
    assert CONFIGURE.main(["--output", str(output)]) == 1
    assert output.read_bytes() == before
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [output]


def test_configure_dangling_symlink(tmp_path):
    output = tmp_path / "hosts.yml"
    output.symlink_to(tmp_path / "missing.yml")
    assert CONFIGURE.main(["--output", str(output)]) == 1
    assert output.is_symlink()
    assert not output.exists()


def test_configure_explicit_config(tmp_path, inventory):
    inventory["all"]["vars"]["ansible_user"] = "maintenance"
    source = tmp_path / "config.local.yml"
    source.write_text(yaml.safe_dump(inventory))
    output = tmp_path / "hosts.yml"
    assert CONFIGURE.main(["--config", str(source), "--output", str(output)]) == 0
    assert VALIDATOR.load_yaml(output)["all"]["vars"]["ansible_user"] == "maintenance"


def test_configure_failed_install_cleans_temporary(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("fixture install failure")

    monkeypatch.setattr(os, "link", fail)
    assert CONFIGURE.main(["--output", str(tmp_path / "hosts.yml")]) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("script", ["configure.py", "tests/validate_project.py"])
def test_help(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"], capture_output=True, check=False
    )
    assert result.returncode == 0


def test_validator_missing_playbook_and_bad_manifest(tmp_path):
    for folder in ("inventory", "playbooks", "semaphore"):
        shutil.copytree(ROOT / folder, tmp_path / folder)
    (tmp_path / "playbooks" / "os-update.yml").unlink()
    assert VALIDATOR.validate_project(tmp_path)
    shutil.copy(ROOT / "playbooks" / "os-update.yml", tmp_path / "playbooks")
    path = tmp_path / "semaphore" / "task-templates.yml"
    manifest = yaml.safe_load(path.read_text())
    manifest["templates"][0]["playbook"] = "../outside.yml"
    path.write_text(yaml.safe_dump(manifest))
    assert any("project playbook" in e for e in VALIDATOR.validate_project(tmp_path))


def test_playbook_mutation_boundaries():
    os_play = yaml.safe_load((ROOT / "playbooks" / "os-update.yml").read_text())[0]
    compose = yaml.safe_load(
        (ROOT / "playbooks" / "docker-compose-update.yml").read_text()
    )[0]
    for play in (os_play, compose):
        conditions = play["pre_tasks"][0]["ansible.builtin.assert"]["that"]
        assert any(
            "ansible_check_mode" in condition and "UPDATE FLEET" in condition
            for condition in conditions
        )
    assert os_play["serial"].startswith("{{ 1 if")
    block = next(task for task in os_play["tasks"] if "block" in task)
    assert "not ansible_check_mode" in block["when"]
    names = [task["name"] for task in block["block"]]
    assert names.index("Wait for the guest to stop answering on SSH") < names.index(
        "Wait for SSH after the reboot"
    )
    health = compose["tasks"][-1]
    assert "item.containers | length > 0" in health["ansible.builtin.assert"]["that"]
    assert (
        "rejectattr('State', 'equalto', 'running')"
        in health["vars"]["non_running_names"]
    )


@pytest.mark.parametrize(
    ("helpers", "kernel", "rpm_rc", "expected"),
    [
        ({"needs-restarting": 0}, "same", 0, 0),
        ({"needs-restarting": 1}, "same", 0, 1),
        ({"needs-restarting": 2, "dnf5": 1}, "same", 0, 1),
        ({"dnf5": 2, "dnf": 0}, "same", 0, 0),
        ({"dnf": 2}, "newer", 0, 1),
        ({}, "same", 0, 255),
        ({}, "newer", 1, 255),
    ],
)
def test_reboot_probe_fallbacks(tmp_path, helpers, kernel, rpm_rc, expected):
    play = yaml.safe_load((ROOT / "playbooks/os-update.yml").read_text())[0]
    probe = next(task for task in play["tasks"] if "ansible.builtin.shell" in task)
    assert probe["check_mode"] is False
    assert probe["become"] is False
    for name, code in helpers.items():
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\nexit {code}\n")
        path.chmod(0o700)
    for name, contents in {
        "uname": "printf 'same\\n'",
        "rpm": f"printf '{kernel}\\n'; exit {rpm_rc}",
    }.items():
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{contents}\n")
        path.chmod(0o700)
    for name in ("sort", "tail"):
        (tmp_path / name).symlink_to(shutil.which(name))
    completed = subprocess.run(
        ["/bin/bash", "-c", probe["ansible.builtin.shell"]],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        check=False,
    )
    assert completed.returncode == expected


def test_host_account_overrides_inherited_default(inventory):
    inventory["all"]["vars"]["ansible_user"] = "root"
    hosts = inventory["all"]["children"]["os_update_targets"]["hosts"]
    for values in hosts.values():
        values["ansible_user"] = "operator"
    assert VALIDATOR.validate_inventory(inventory) == []


def test_reboot_auto_single_host_assertion():
    os_play = yaml.safe_load((ROOT / "playbooks" / "os-update.yml").read_text())[0]
    pre_tasks = os_play.get("pre_tasks", [])
    assertion_task = None
    for task in pre_tasks:
        if "ansible.builtin.assert" in task:
            conditions = task["ansible.builtin.assert"].get("that", [])
            if any("ansible_play_batch" in c for c in conditions):
                assertion_task = task
                break
    assert assertion_task is not None, (
        "os-update.yml must assert ansible_play_batch early in play"
    )
    assert_data = assertion_task["ansible.builtin.assert"]
    fail_msg = assert_data.get("fail_msg", "")
    assert "pass reboot=auto with -e" in fail_msg.lower()

    conditions = assert_data["that"]
    cond_text = " ".join(conditions)
    assert "reboot_policy" in cond_text or "reboot" in cond_text
    assert "ansible_play_batch" in cond_text
    assert "1" in cond_text
