import copy
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module(
    "linux_access_validator", ROOT / "tests" / "validate_project.py"
)
CONFIG = VALIDATOR.CONFIG
sys.modules["linux_access_config"] = CONFIG
CONFIGURE = load_module("linux_access_configurator", ROOT / "configure.py")


def playbook(name):
    return yaml.safe_load((ROOT / "playbooks" / name).read_text())[0]


def inventory():
    return CONFIG.read_mapping(ROOT / "inventory" / "hosts.yml.example")


def test_project_passes_offline_validation():
    assert VALIDATOR.validate_project(ROOT) == []


@pytest.mark.parametrize("filename", VALIDATOR.PLAYBOOKS)
def test_serial_and_abort_guards_cannot_be_removed(filename):
    play = playbook(filename)
    play["serial"] = 2
    play["any_errors_fatal"] = False
    errors = VALIDATOR.validate_playbook(filename, play)
    assert any("one host" in error for error in errors)
    assert any("abort" in error for error in errors)


def test_root_proof_must_precede_policy_write():
    play = playbook("sudoers-rootpw.yml")
    tasks = play["tasks"]
    write = next(t for t in tasks if "ansible.builtin.copy" in t)
    tasks.remove(write)
    tasks.insert(0, write)
    errors = VALIDATOR.validate_playbook("sudoers-rootpw.yml", play)
    assert any("must precede rootpw" in error for error in errors)


@pytest.mark.parametrize("filename", ["sudoers-rootpw.yml", "sudoers-nopasswd.yml"])
def test_each_sudoers_write_requires_visudo(filename):
    original = playbook(filename)
    for index, task in enumerate(original["tasks"]):
        if "ansible.builtin.copy" not in task:
            continue
        modified = copy.deepcopy(original)
        del modified["tasks"][index]["ansible.builtin.copy"]["validate"]
        errors = VALIDATOR.validate_playbook(filename, modified)
        assert any("visudo validation" in error for error in errors)


def test_existing_automation_password_is_never_locked():
    play = playbook("automation-account.yml")
    task = next(t for t in play["tasks"] if "ansible.builtin.user" in t)
    assert "groups['automation_account_targets']" in task["when"]
    task["ansible.builtin.user"]["update_password"] = "always"
    errors = VALIDATOR.validate_playbook("automation-account.yml", play)
    assert any("never be locked" in error for error in errors)


def test_nopasswd_negative_proof_cannot_report_success():
    play = playbook("sudoers-rootpw.yml")
    task = next(
        t for t in play["tasks"] if t.get("register") == "operator_sudo_loginpw"
    )
    task["when"] = ["not ansible_check_mode"]
    errors = VALIDATOR.validate_playbook("sudoers-rootpw.yml", play)
    assert any("skip NOPASSWD" in error for error in errors)


def test_policy_read_requires_root_and_uppercase_user_option():
    play = playbook("sudoers-rootpw.yml")
    task = next(t for t in play["tasks"] if t.get("register") == "operator_sudo_list")
    task["become"] = False
    task["ansible.builtin.command"]["argv"] = ["sudo", "-l"]
    errors = VALIDATOR.validate_playbook("sudoers-rootpw.yml", play)
    assert any("as root" in error for error in errors)
    assert any("sudo -l -U" in error for error in errors)


@pytest.mark.parametrize("data", ["Zml4dHVyZQ==", "REPLACE_WITH_PUBLIC_KEY_DATAextra"])
def test_key_example_rejects_material_and_embedded_placeholder(data):
    with pytest.raises(ValueError, match="placeholder"):
        VALIDATOR.validate_key_example(
            {
                "automation_account_public_key": (
                    f"ssh-ed25519 {data} automation@example.net"
                )
            }
        )


def test_key_example_rejects_restriction_prefix():
    with pytest.raises(ValueError, match="placeholder"):
        VALIDATOR.validate_key_example(
            {
                "automation_account_public_key": (
                    "no-pty ssh-ed25519 REPLACE_WITH_PUBLIC_KEY_DATA "
                    "automation@example.net"
                )
            }
        )


def test_local_connection_requires_hostname():
    data = inventory()
    host = data["all"]["children"]["automation_account_targets"]["hosts"][
        "controller-example"
    ]
    del host["baseline_controller_hostname"]
    with pytest.raises(ValueError, match="controller hostname"):
        CONFIG.validate_inventory(data)


@pytest.mark.parametrize(
    "field", ["ansible_password", "root_password", "ansible_ssh_pass"]
)
def test_inventory_rejects_credentials(field):
    data = inventory()
    data["all"]["vars"][field] = "fixture-secret"
    with pytest.raises(ValueError, match="credentials"):
        CONFIG.validate_inventory(data)


@pytest.mark.parametrize(
    "field", ["ansible_become", "ansible_become_method", "ansible_become_user"]
)
def test_inventory_rejects_elevation_overrides(field):
    data = inventory()
    data["all"]["vars"][field] = "fixture"
    with pytest.raises(ValueError, match="elevation"):
        CONFIG.validate_inventory(data)


def test_group_overlap_and_stray_sudo_targets_are_rejected():
    data = inventory()
    children = data["all"]["children"]
    children["automation_account_key_only"]["hosts"]["server-example"] = {}
    with pytest.raises(ValueError, match="disjoint"):
        CONFIG.validate_inventory(data)
    children["automation_account_key_only"]["hosts"] = {}
    children["operator_nopasswd_targets"]["hosts"]["stray-example"] = {}
    with pytest.raises(ValueError, match="already be managed"):
        CONFIG.validate_inventory(data)


def test_non_secret_settings_precedence(tmp_path):
    config = tmp_path / "settings.yml"
    config.write_text("operator_user: local-operator\n")
    assert CONFIG.resolve_settings(config)["operator_user"] == "local-operator"
    assert (
        CONFIG.resolve_settings(config, {"operator_user": "cli-operator"})[
            "operator_user"
        ]
        == "cli-operator"
    )
    assert CONFIG.resolve_settings(None)["operator_user"] == "operator"


def test_settings_reject_plaintext_password(tmp_path):
    config = tmp_path / "settings.yml"
    config.write_text("root_password: fixture-secret\n")
    with pytest.raises(ValueError, match="non-secret"):
        CONFIG.resolve_settings(config)


def test_configurator_no_overwrite_mode_and_secret_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv("BASELINE_ROOT_PASSWORD", "fixture-secret")
    paths = [tmp_path / "hosts.yml", tmp_path / "config.local.yml"]
    args = ["--inventory-output", str(paths[0]), "--config-output", str(paths[1])]
    assert CONFIGURE.main(args) == 0
    before = [path.read_bytes() for path in paths]
    assert CONFIGURE.main(args) == 1
    assert before == [path.read_bytes() for path in paths]
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o600
        assert "fixture-secret" not in path.read_text()
    CONFIG.validate_inventory(CONFIG.read_mapping(paths[0]))


def test_configurator_cleans_up_if_second_install_fails(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    original = CONFIGURE.os.link

    def failing_link(src, dest):
        if dest == second:
            raise OSError("simulated installation error")
        original(src, dest)

    monkeypatch.setattr(CONFIGURE.os, "link", failing_link)
    with pytest.raises(OSError):
        CONFIGURE.install_files(((first, "one"), (second, "two")))
    assert list(tmp_path.iterdir()) == []


def test_configurator_refuses_dangling_symlink(tmp_path):
    path = tmp_path / "hosts.yml"
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        CONFIGURE.install_files(((path, "value"),))
    assert path.is_symlink()
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("script", ["configure.py", "tests/validate_project.py"])
def test_entry_point_help(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
