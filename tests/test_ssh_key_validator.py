import base64
import hashlib
import importlib.util
import sys
from pathlib import Path

VALIDATOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "identity-and-access"
    / "ssh-key-rotation"
    / "tests"
    / "validate_project.py"
)
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
