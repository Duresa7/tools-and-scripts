from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]


def make_fake_docker(bin_dir: Path, log_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_script = bin_dir / "docker"
    docker_script.write_text(
        f"""#!/usr/bin/env bash
set -e
printf '%s\\n' "$*" >> "{log_file}"
if [[ $1 == "inspect" ]]; then
  if [[ "${{FAKE_DOCKER_INSPECT_EXIT:-0}}" != "0" ]]; then
    exit "${{FAKE_DOCKER_INSPECT_EXIT}}"
  fi
  printf '%s\\n' "${{FAKE_DOCKER_INSPECT_OUT:-}}"
  exit 0
fi
if [[ $1 == "compose" ]]; then
  if [[ "${{FAKE_DOCKER_COMPOSE_EXIT:-0}}" != "0" ]]; then
    exit "${{FAKE_DOCKER_COMPOSE_EXIT}}"
  fi
  exit 0
fi
if [[ $1 == "ps" ]]; then
  printf '%s\\n' "${{FAKE_DOCKER_PS_OUT:-}}"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    docker_script.chmod(0o755)


def test_compose_service_update_is_self_contained() -> None:
    required = {
        "README.md",
        "config.example.conf",
        "configure.sh",
        "compose-service-update.sh",
        "tests/test_compose_service_update.py",
    }
    files = {
        str(path.relative_to(TOOL)).replace("\\", "/")
        for path in TOOL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files >= required


def test_help_options() -> None:
    update_help = subprocess.run(
        ["bash", "compose-service-update.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )
    assert update_help.returncode == 0, update_help.stderr
    assert "--dry-run" in update_help.stdout
    assert "--container" in update_help.stdout
    assert "--project" in update_help.stdout
    assert "--service" in update_help.stdout
    assert "--allowed-root" in update_help.stdout
    assert "--config" in update_help.stdout

    config_help = subprocess.run(
        ["bash", "configure.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )
    assert config_help.returncode == 0, config_help.stderr
    assert "--output" in config_help.stdout
    assert "--print-discovery" in config_help.stdout


def test_config_marks_every_owned_value() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")
    assert example.count("CUSTOMIZE:") == 4
    assert "CONTAINER_NAME=" in example
    assert "PROJECT_NAME=" in example
    assert "SERVICE_NAME=" in example
    assert "ALLOWED_COMPOSE_ROOT=" in example


def test_configurator_writes_and_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "config.local.conf"
    command = ["bash", "configure.sh", "--output", str(output)]

    first = subprocess.run(
        command,
        cwd=TOOL,
        input="my-container\nmy-project\nmy-service\n/opt/docker\n",
        check=False,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    assert "configuration-written=" in first.stdout
    assert output.is_file()

    content = output.read_text(encoding="utf-8")
    assert "CONTAINER_NAME=my-container" in content
    assert "PROJECT_NAME=my-project" in content
    assert "SERVICE_NAME=my-service" in content
    assert "ALLOWED_COMPOSE_ROOT=/opt/docker" in content

    second = subprocess.run(
        command,
        cwd=TOOL,
        input="my-container\nmy-project\nmy-service\n/opt/docker\n",
        check=False,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 1
    assert "refusing to replace existing file" in second.stderr


def test_label_with_one_and_several_files(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "compose"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")
    file2 = allowed_dir / "docker-compose.override.yaml"
    file2.write_text("services:\n  app:\n    restart: always\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    # 1. Test single file in label
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)
    res_single = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res_single.returncode == 0, res_single.stderr
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert any(
        f"compose --project-name testproj -f {file1} pull app" in line for line in lines
    )
    expected_single_up = (
        f"compose --project-name testproj -f {file1} "
        "up -d --no-deps --no-build --pull never app"
    )
    assert any(expected_single_up in line for line in lines)

    # 2. Test several files in label
    log_file.write_text("", encoding="utf-8")
    env["FAKE_DOCKER_INSPECT_OUT"] = f"{file1},{file2}"
    res_multi = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res_multi.returncode == 0, res_multi.stderr
    lines_multi = log_file.read_text(encoding="utf-8").strip().splitlines()
    expected_args = f"-f {file1} -f {file2}"
    assert any(
        f"compose --project-name testproj {expected_args} pull app" in line
        for line in lines_multi
    )
    expected_multi_up = (
        f"compose --project-name testproj {expected_args} "
        "up -d --no-deps --no-build --pull never app"
    )
    assert any(expected_multi_up in line for line in lines_multi)


def test_file_outside_allowed_root_is_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "compose.yaml"
    outside_file.write_text(
        "services:\n  app:\n    image: example:latest\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(outside_file)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "outside allowed root" in result.stderr
    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert not any(line.startswith("compose") for line in log_lines)


def test_missing_file_is_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    missing_file = allowed_dir / "nonexistent-compose.yaml"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(missing_file)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr
    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert not any(line.startswith("compose") for line in log_lines)


def test_empty_label_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_LOG"] = str(log_file)

    # 1. Empty string label
    env["FAKE_DOCKER_INSPECT_OUT"] = ""
    res_empty = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(tmp_path),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res_empty.returncode != 0
    assert "has no Compose configuration files label" in res_empty.stderr

    # 2. Missing label (<no value>)
    env["FAKE_DOCKER_INSPECT_OUT"] = "<no value>"
    res_missing = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(tmp_path),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res_missing.returncode != 0
    assert "has no Compose configuration files label" in res_missing.stderr
    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert not any(line.startswith("compose") for line in log_lines)


def test_dry_run_runs_nothing(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
            "--dry-run",
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Resolved Compose files:" in result.stdout
    assert str(file1) in result.stdout
    assert "Planned commands:" in result.stdout
    assert "pull" in result.stdout
    assert "up" in result.stdout

    # Inspect was called, but compose was NOT called
    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1
    assert log_lines[0].startswith("inspect")
    assert not any(line.startswith("compose") for line in log_lines)


def test_success_path_runs_pull_then_up_with_exact_flags(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "myproject",
            "--service",
            "myservice",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Pulling service image: myservice" in result.stdout
    assert "Recreating service container: myservice" in result.stdout
    assert "Update completed successfully" in result.stdout

    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 3
    assert log_lines[0].startswith("inspect")
    assert log_lines[1] == f"compose --project-name myproject -f {file1} pull myservice"
    assert log_lines[2] == (
        f"compose --project-name myproject -f {file1} "
        "up -d --no-deps --no-build --pull never myservice"
    )


def test_config_file_and_cli_precedence(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    config = tmp_path / "config.test.conf"
    config.write_text(
        f"""CONTAINER_NAME=conf-container
PROJECT_NAME=conf-project
SERVICE_NAME=conf-service
ALLOWED_COMPOSE_ROOT={allowed_dir}
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    # CLI flags override SERVICE_NAME
    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--config",
            str(config),
            "--service",
            "cli-service",
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    log_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert (
        log_lines[1]
        == f"compose --project-name conf-project -f {file1} pull cli-service"
    )
    assert log_lines[2] == (
        f"compose --project-name conf-project -f {file1} "
        "up -d --no-deps --no-build --pull never cli-service"
    )


def test_missing_realpath_is_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for entry in os.scandir("/usr/bin"):
        if entry.name not in ("realpath", "docker"):
            with contextlib.suppress(OSError):
                os.symlink(entry.path, bin_dir / entry.name)
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
            "--dry-run",
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "realpath" in result.stderr.lower()


def test_failing_realpath_is_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    fake_realpath = bin_dir / "realpath"
    fake_realpath.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_realpath.chmod(0o755)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
            "--dry-run",
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "realpath" in result.stderr.lower()


def test_docker_compose_failure_propagates_exit_status(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)
    env["FAKE_DOCKER_COMPOSE_EXIT"] = "42"

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 42


def test_failing_realpath_on_compose_file_is_refused(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_file = tmp_path / "docker.log"
    make_fake_docker(bin_dir, log_file)

    fake_realpath = bin_dir / "realpath"
    fake_realpath.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"compose.yaml"* ]]; then
  exit 1
fi
/usr/bin/realpath "$@"
""",
        encoding="utf-8",
    )
    fake_realpath.chmod(0o755)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    file1 = allowed_dir / "compose.yaml"
    file1.write_text("services:\n  app:\n    image: example:latest\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_DOCKER_INSPECT_OUT"] = str(file1)
    env["FAKE_DOCKER_LOG"] = str(log_file)

    result = subprocess.run(
        [
            "bash",
            "compose-service-update.sh",
            "--container",
            "test-app",
            "--project",
            "testproj",
            "--service",
            "app",
            "--allowed-root",
            str(allowed_dir),
            "--dry-run",
        ],
        cwd=TOOL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "realpath" in result.stderr.lower()
