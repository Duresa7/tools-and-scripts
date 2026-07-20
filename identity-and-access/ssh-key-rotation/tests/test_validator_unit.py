import base64
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

VALIDATOR_PATH = Path(__file__).with_name("validate_project.py")
SPEC = importlib.util.spec_from_file_location("ssh_key_validator", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def test_fingerprint_matches_openssh_sha256_shape() -> None:
    raw = b"public-key-fixture"
    public_key = f"ssh-ed25519 {base64.b64encode(raw).decode()} fixture"
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")

    assert VALIDATOR.fingerprint(public_key) == f"SHA256:{digest}"


def test_collect_hosts_applies_inherited_variables() -> None:
    group = {
        "vars": {"ssh_key_platform": "posix"},
        "children": {
            "linux": {
                "vars": {"ssh_key_write_enabled": True},
                "hosts": {"server-01": {"ansible_user": "admin"}},
            }
        },
    }

    assert VALIDATOR.collect_hosts(group) == {
        "server-01": {
            "ansible_user": "admin",
            "ssh_key_platform": "posix",
            "ssh_key_write_enabled": True,
        }
    }


def test_checked_in_project_passes_validation() -> None:
    assert (
        VALIDATOR.validate_project(VALIDATOR_PATH.parents[1], "hosts.yml.example") == []
    )


def test_identity_requires_the_writer_for_a_shared_key_store(tmp_path: Path) -> None:
    raw = b"shared-key-fixture"
    encoded = base64.b64encode(raw).decode()
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    identity = tmp_path / "admin-laptop.yml"
    identity.write_text(
        "\n".join(
            (
                "ssh_identity:",
                "  id: admin-laptop",
                "  display_name: Admin Laptop",
                f"  fingerprint: SHA256:{digest}",
                f"  current_public_key: ssh-ed25519 {encoded} fixture",
                "  target_hosts:",
                "    - cluster-reader",
                "  rotation:",
                "    replacement_public_key: ''",
                "    operator_verified: false",
            )
        ),
        encoding="utf-8",
    )
    supported = {
        "cluster-writer": {
            "ssh_key_write_enabled": True,
            "ssh_authorized_keys_path": "/srv/cluster/authorized_keys",
        },
        "cluster-reader": {
            "ssh_key_write_enabled": False,
            "ssh_key_shared_writer": "cluster-writer",
            "ssh_authorized_keys_path": "/srv/cluster/authorized_keys",
        },
    }
    errors: list[str] = []

    VALIDATOR.validate_identity(identity, supported, errors)

    assert errors == [
        "admin-laptop.yml: cluster-reader requires shared writer "
        "cluster-writer in target_hosts"
    ]


def test_inventory_separates_connection_account_owner_and_elevation() -> None:
    inventory = yaml.safe_load(
        (VALIDATOR_PATH.parents[1] / "inventory" / "hosts.yml.example").read_text(
            encoding="utf-8"
        )
    )
    errors: list[str] = []

    supported, _ = VALIDATOR.validate_inventory(inventory, errors)

    assert errors == []
    posix = supported["posix-standard-example"]
    assert posix["ansible_user"] == "replace-connection-account"
    assert posix["ssh_key_owner"] == "replace-managed-account"
    assert posix["ssh_key_become"] is True
    windows = supported["windows-standard-example"]
    assert windows["ssh_key_windows_account_type"] == "standard"
    assert windows["ssh_key_become"] is False


def test_windows_standard_user_cannot_use_administrator_key_file() -> None:
    inventory = {
        "all": {
            "children": {
                "ssh_key_supported": {
                    "hosts": {
                        "windows-target": {
                            "ansible_user": "connection-account",
                            "ssh_key_owner": "managed-account",
                            "ssh_key_platform": "windows",
                            "ssh_key_windows_account_type": "standard",
                            "ssh_key_become": False,
                            "ssh_authorized_keys_path": (
                                r"C:\ProgramData\ssh\administrators_authorized_keys"
                            ),
                        }
                    }
                }
            }
        }
    }
    errors: list[str] = []

    VALIDATOR.validate_inventory(inventory, errors)

    assert errors == [
        "windows-target: standard users cannot use the administrator shared file"
    ]


def test_configurator_creates_ignored_yaml_from_a_public_key(tmp_path: Path) -> None:
    inventory_directory = tmp_path / "inventory"
    identity_directory = tmp_path / "identities"
    inventory_directory.mkdir()
    identity_directory.mkdir()
    public_key_path = tmp_path / "workstation.pub"
    raw = b"configurator-public-key"
    public_key_path.write_text(
        f"ssh-ed25519 {base64.b64encode(raw).decode()} workstation\n",
        encoding="utf-8",
    )
    inventory_path = inventory_directory / "hosts.yml"
    identity_path = identity_directory / "workstation-key.yml"
    command = [
        sys.executable,
        str(VALIDATOR_PATH.parents[1] / "configure.py"),
        "--host-alias",
        "server-one",
        "--host",
        "192.0.2.10",
        "--connection-user",
        "connection-account",
        "--managed-owner",
        "managed-account",
        "--authorized-keys-path",
        "/home/managed-account/.ssh/authorized_keys",
        "--platform",
        "posix",
        "--become",
        "--identity-id",
        "workstation-key",
        "--display-name",
        "Workstation key",
        "--public-key-file",
        str(public_key_path),
        "--inventory-output",
        str(inventory_path),
        "--identity-output",
        str(identity_path),
    ]

    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "private-key-handling=disabled" in first.stdout
    inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    supported, _ = VALIDATOR.validate_inventory(inventory, errors)
    assert errors == []
    identity_id = VALIDATOR.validate_identity(identity_path, supported, errors)
    assert identity_id == "workstation-key"
    assert errors == []


def test_configurator_rejects_private_key_material(tmp_path: Path) -> None:
    private_key_path = tmp_path / "not-public"
    private_key_path.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nvalue\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(VALIDATOR_PATH.parents[1] / "configure.py"),
        "--host-alias",
        "server-one",
        "--host",
        "192.0.2.10",
        "--connection-user",
        "connection-account",
        "--managed-owner",
        "managed-account",
        "--authorized-keys-path",
        "/home/managed-account/.ssh/authorized_keys",
        "--platform",
        "posix",
        "--identity-id",
        "workstation-key",
        "--display-name",
        "Workstation key",
        "--public-key-file",
        str(private_key_path),
        "--inventory-output",
        str(tmp_path / "hosts.yml"),
        "--identity-output",
        str(tmp_path / "identity.yml"),
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)

    assert completed.returncode == 1
    assert "public-key line" in completed.stderr
    assert not (tmp_path / "hosts.yml").exists()
