import copy
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "exporter_validator", ROOT / "tests/validate_project.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def inventory():
    return VALIDATOR.read_yaml(ROOT / "inventory/hosts.yml.example")


def test_complete_project():
    assert VALIDATOR.validate_project(ROOT) == []


def test_scope_addition_requires_separate_approval():
    data = inventory()
    data["all"]["children"]["cadvisor_targets"]["hosts"]["extra-example"] = {
        "ansible_host": "192.0.2.50"
    }
    assert any("membership differs" in e for e in VALIDATOR.validate_inventory(data))


def test_inherited_host_values_and_empty_groups():
    data = inventory()
    data["all"]["children"]["cadvisor_targets"] = {"hosts": {}}
    data["all"]["vars"]["monitoring_scope"]["cadvisor_targets"] = []
    data["all"]["children"]["wud_targets"]["hosts"]["docker-example"][
        "ansible_host"
    ] = "192.0.2.12"
    assert VALIDATOR.validate_inventory(data) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("ansible_user", "root"),
        ("ansible_become", False),
        ("ansible_host", ""),
    ],
)
def test_reject_invalid_connection_fields(field, value):
    data = inventory()
    data["all"]["children"]["node_exporter_targets"]["hosts"]["linux-example"][
        field
    ] = value
    assert VALIDATOR.validate_inventory(data)


def test_reject_password_values_without_printing_them():
    data = inventory()
    data["all"]["vars"]["ansible_password"] = "private-test-value"
    with pytest.raises(ValueError, match="credential values") as error:
        VALIDATOR.validate_inventory(data)
    assert "private-test-value" not in str(error.value)


def test_reject_duplicate_yaml(tmp_path):
    path = tmp_path / "duplicate.yml"
    path.write_text("all: {}\nall: {}\n")
    with pytest.raises(ValueError, match="unique"):
        VALIDATOR.read_yaml(path)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("wud_password_env", "not a variable"),
        ("wud_host_port", 0),
        ("wud_prometheus_host", "missing-example"),
    ],
)
def test_reject_invalid_settings(setting, value):
    data = inventory()
    data["all"]["vars"]["monitoring"][setting] = value
    assert VALIDATOR.validate_inventory(data)


def test_wud_requires_staggered_schedule():
    data = inventory()
    del data["all"]["children"]["wud_targets"]["hosts"]["docker-example"]["wud_cron"]
    assert any("wud_cron" in e for e in VALIDATOR.validate_inventory(data))


def test_manifest_rejects_traversal_duplicate_and_live_default():
    manifest = VALIDATOR.read_yaml(ROOT / "semaphore/task-templates.yml")
    manifest["templates"].append(copy.deepcopy(manifest["templates"][0]))
    manifest["templates"][0]["playbook"] = "../outside.yml"
    manifest["templates"][0]["arguments"] = []
    errors = VALIDATOR.validate_manifest(ROOT, manifest)
    assert any("unique" in e for e in errors)
    assert any("unknown playbook" in e for e in errors)
    assert any("default to --check" in e for e in errors)


def test_configurator_atomic_nonoverwrite(tmp_path):
    output = tmp_path / "hosts.yml"
    command = [sys.executable, str(ROOT / "configure.py"), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    original = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 1
    assert output.read_bytes() == original
    assert list(tmp_path.iterdir()) == [output]
    assert VALIDATOR.validate_project(ROOT, str(output)) == []


def test_configurator_refuses_dangling_symlink(tmp_path):
    output = tmp_path / "hosts.yml"
    output.symlink_to(tmp_path / "missing")
    result = subprocess.run(
        [sys.executable, str(ROOT / "configure.py"), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert output.is_symlink()
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("script", ["configure.py", "tests/validate_project.py"])
def test_help(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout


def walk(tasks):
    for task in tasks:
        yield task
        yield from walk(task.get("block", []))
        yield from walk(task.get("rescue", []))
        yield from walk(task.get("always", []))


def test_source_safeguards_and_pins_survive():
    plays = {
        name: yaml.safe_load((ROOT / f"playbooks/{name}.yml").read_text())[0]
        for name in ("node-exporter", "cadvisor", "textfile-collectors", "wud")
    }
    node_tasks = list(walk(plays["node-exporter"]["tasks"]))
    downloads = [
        t["ansible.builtin.get_url"]
        for t in node_tasks
        if "ansible.builtin.get_url" in t
    ]
    assert downloads and all(
        t["checksum"].startswith("sha256:https://") for t in downloads
    )
    assert "1.9.0" in plays["node-exporter"]["vars"]["node_exporter_version"]
    assert "v0.60.5" in plays["cadvisor"]["vars"]["cadvisor_image"]
    assert "9.0.2" in plays["wud"]["vars"]["wud_image"]
    assert all(
        t["ansible.builtin.apt"].get("install_recommends") is False
        for p in plays.values()
        for t in walk(p["tasks"])
        if "ansible.builtin.apt" in t and "name" in t["ansible.builtin.apt"]
    )
    text = (ROOT / "playbooks/textfile-collectors.yml").read_text()
    assert "upstream_unit.stat.exists" in text
    assert "node_textfile_scrape_error 0" in text
    assert "(apt_package_cache|dnf_updates_check)_timestamp_seconds" in text
    assert "smartmon_device_smart_healthy" in text
    assert "NoNewPrivileges=true" in (ROOT / "playbooks/node-exporter.yml").read_text()
    for task in walk(plays["wud"]["tasks"]):
        if task["name"] in (
            "Read the administrator credential from the named environment variable",
            "Bring the project up",
            "Install this host's Prometheus scrape credential",
            "Confirm WUD exports container metrics with authentication",
        ):
            assert task["no_log"] is True


@pytest.mark.parametrize("mode", ["blocked", "check", "apply", "scope"])
def test_real_ansible_preflight_gate(tmp_path, mode):
    executable = shutil.which("ansible-playbook") or str(
        Path(sys.executable).with_name("ansible-playbook")
    )
    assert Path(executable).is_file(), "Install ansible-core in the Python environment"
    play = yaml.safe_load((ROOT / "playbooks/node-exporter.yml").read_text())[0]
    test_play = {
        "name": "Exercise consent on the local controller only",
        "hosts": "node_exporter_targets",
        "gather_facts": False,
        "pre_tasks": play["pre_tasks"],
        "tasks": [
            {
                "name": "Mark the write boundary",
                "ansible.builtin.debug": {"msg": "WRITE_BOUNDARY_REACHED"},
            }
        ],
    }
    path = tmp_path / "gate.yml"
    path.write_text(yaml.safe_dump([test_play]))
    inv = tmp_path / "inventory.yml"
    inv.write_text(
        yaml.safe_dump(
            {
                "all": {
                    "vars": {
                        "monitoring_scope": {
                            "node_exporter_targets": []
                            if mode == "scope"
                            else ["local-example"]
                        }
                    },
                    "children": {
                        "node_exporter_targets": {
                            "hosts": {"local-example": {"ansible_connection": "local"}}
                        }
                    },
                }
            }
        )
    )
    command = [executable, "-i", str(inv), str(path)]
    if mode == "check":
        command.append("--check")
    if mode in {"apply", "scope"}:
        command += ["-e", 'monitoring_confirmation="APPLY monitoring exporters"']
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert (result.returncode == 0) == (mode in {"check", "apply"}), (
        result.stdout + result.stderr
    )
    assert ("WRITE_BOUNDARY_REACHED" in result.stdout) == (mode == "apply")


def fake_command(directory, name, body):
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("failure", [False, True])
def test_bundled_dnf_collector_atomic_results(tmp_path, failure):
    fake_command(
        tmp_path,
        "dnf",
        "if [[ ${FAIL_DNF:-0} == 1 ]]; then exit 7; fi\n"
        'printf "pkg.x86_64 2.0 updates\\n'
        'Obsoleting Packages\\nold.x86_64 1.0 updates\\n"\nexit 100\n',
    )
    fake_command(tmp_path, "uname", "echo 1.0\n")
    fake_command(tmp_path, "rpm", "echo 2.0\n")
    output = tmp_path / "dnf.prom"
    previous = (
        "# TYPE dnf_updates_check_timestamp_seconds gauge\n"
        "dnf_updates_check_timestamp_seconds 1\n"
    )
    output.write_text(previous)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "FAIL_DNF": str(int(failure)),
    }
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "playbooks/files/dnf-updates-textfile.sh"),
            "--output",
            str(output),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if failure:
        assert result.returncode == 2
        assert output.read_text() == previous
    else:
        assert result.returncode == 0, result.stderr
        assert 'dnf_upgrades_pending{repo="updates"} 1' in output.read_text()
        assert "node_reboot_required 1" in output.read_text()
    assert list(tmp_path.glob("dnf.prom.*")) == []


def test_no_fixed_staging_or_unarchive_creates():
    playbook_files = sorted((ROOT / "playbooks").glob("*.yml"))
    assert playbook_files, "playbook files must exist"
    for pb_file in playbook_files:
        content = yaml.safe_load(pb_file.read_text())
        for play in content:
            for var_name, var_value in play.get("vars", {}).items():
                if isinstance(var_value, str):
                    assert "/tmp" not in var_value and "/var/tmp" not in var_value, (
                        f"{pb_file.name}: var {var_name} uses fixed temp path: "
                        f"{var_value}"
                    )
            tasks = list(walk(play.get("tasks", [])))
            for task in tasks:
                if "ansible.builtin.get_url" in task:
                    dest = str(task["ansible.builtin.get_url"].get("dest", ""))
                    assert not dest.startswith(("/tmp", "/var/tmp")), (
                        f"{pb_file.name}: get_url uses fixed temp path: {dest}"
                    )
                if "ansible.builtin.unarchive" in task:
                    unarchive = task["ansible.builtin.unarchive"]
                    assert "creates" not in unarchive, (
                        f"{pb_file.name}: unarchive uses creates"
                    )
                    dest = str(unarchive.get("dest", ""))
                    assert not dest.startswith(("/tmp", "/var/tmp")), (
                        f"{pb_file.name}: unarchive uses fixed temp path: {dest}"
                    )

    node_play = yaml.safe_load((ROOT / "playbooks/node-exporter.yml").read_text())[0]
    node_tasks = node_play.get("tasks", [])
    tempfile_tasks = [t for t in walk(node_tasks) if "ansible.builtin.tempfile" in t]
    assert tempfile_tasks, "node-exporter must use ansible.builtin.tempfile"
    assert any(
        t["ansible.builtin.tempfile"].get("state") == "directory"
        for t in tempfile_tasks
    ), "tempfile must create a directory"
    blocks_with_always = [t for t in node_tasks if "always" in t]
    assert blocks_with_always, "node-exporter must have an always block"
    always_tasks = [t for b in blocks_with_always for t in b.get("always", [])]
    assert any(
        "ansible.builtin.file" in t
        and t["ansible.builtin.file"].get("state") == "absent"
        for t in always_tasks
    ), "always block must remove temporary directory"
