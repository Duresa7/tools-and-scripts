from __future__ import annotations

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
SCRIPT = "dnf-updates-textfile.sh"
CONFIGURATOR = "configure.sh"


@cache
def wsl_executable() -> str | None:
    return shutil.which("wsl.exe")


def posix_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    wsl = wsl_executable()
    assert wsl is not None, "Install WSL with Bash to run these tests on Windows."
    translated = subprocess.run(
        [wsl, "-e", "wslpath", "-a", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return translated.stdout.strip()


def run_script(
    script: str,
    arguments: list[str],
    fake_bin: Path | None = None,
    variables: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    extra = variables or {}
    if os.name == "nt":
        wsl = wsl_executable()
        assert wsl is not None, "Install WSL with Bash to run these tests on Windows."
        bin_dir = posix_path(fake_bin) if fake_bin else ""
        path_override = f'PATH="{bin_dir}:$PATH"; ' if bin_dir else ""
        command = [
            wsl,
            "-e",
            "env",
            *(f"{key}={value}" for key, value in extra.items()),
            "bash",
            "-c",
            f'{path_override}exec bash "$@"',
            "run-script",
            posix_path(TOOL / script),
            *arguments,
        ]
        environment = None
    else:
        environment = os.environ.copy()
        environment.update(extra)
        if fake_bin:
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        command = ["bash", str(TOOL / script), *arguments]
    return subprocess.run(
        command,
        cwd=TOOL,
        env=environment,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def make_fake_environment(
    fake_bin: Path,
    *,
    all_updates: str = "",
    all_rc: int = 0,
    sec_updates: str = "",
    sec_rc: int = 0,
    running_kernel: str = "5.14.0-362.8.1.el9_3.x86_64",
    installed_kernels: list[str] | None = None,
) -> None:
    if installed_kernels is None:
        installed_kernels = [running_kernel]

    fake_bin.mkdir(parents=True, exist_ok=True)

    dnf_body = f"""case "$*" in
  "-q check-update")
    cat <<'EOF'
{all_updates}
EOF
    exit {all_rc}
    ;;
  "-q check-update --security")
    cat <<'EOF'
{sec_updates}
EOF
    exit {sec_rc}
    ;;
  *)
    exit 1
    ;;
esac
"""
    write_executable(fake_bin / "dnf", dnf_body)

    uname_body = f"""if [[ "$*" == *"-r"* ]]; then
  echo "{running_kernel}"
  exit 0
fi
echo "Linux"
exit 0
"""
    write_executable(fake_bin / "uname", uname_body)

    rpm_output = "\n".join(installed_kernels) + "\n"
    rpm_body = f"""if [[ "$*" == *"-q "* ]]; then
  cat <<'EOF'
{rpm_output}EOF
  exit 0
fi
exit 0
"""
    write_executable(fake_bin / "rpm", rpm_body)


def test_tool_is_self_contained() -> None:
    required = {
        "README.md",
        "config.example.conf",
        "configure.sh",
        "dnf-updates-textfile.sh",
        "systemd/dnf-updates-textfile.service",
        "systemd/dnf-updates-textfile.timer",
    }
    files = {
        str(path.relative_to(TOOL)).replace("\\", "/")
        for path in TOOL.rglob("*")
        if path.is_file() and "tests" not in path.parts
    }
    assert files >= required


def test_help_options() -> None:
    collector = run_script(SCRIPT, ["--help"])
    assert collector.returncode == 0, collector.stderr
    assert "--config" in collector.stdout
    assert "--output" in collector.stdout
    assert "--kernel-package" in collector.stdout
    assert "--dry-run" in collector.stdout

    configurator = run_script(CONFIGURATOR, ["--help"])
    assert configurator.returncode == 0, configurator.stderr
    assert "--output" in configurator.stdout
    assert "--print-discovery" in configurator.stdout


def test_config_example_structure() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")
    assert "CUSTOMIZE: Set the textfile path" in example
    assert "CUSTOMIZE: Set the package" in example
    assert "OUTPUT_FILE=" in example
    assert "KERNEL_PACKAGE=" in example


def test_configurator_writes_and_refuses_overwrite(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pgrep_body = (
        'if [[ "$*" == *"-a node_exporter"* ]]; then\n'
        '  echo "9999 /usr/local/bin/node_exporter '
        '--collector.textfile.directory=/var/lib/prometheus/node-exporter"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    write_executable(fake_bin / "pgrep", pgrep_body)

    discovery = run_script(
        CONFIGURATOR,
        ["--print-discovery"],
        fake_bin=fake_bin,
    )
    assert discovery.returncode == 0, discovery.stderr
    assert "textfile-directory=" in discovery.stdout
    assert "running-kernel=" in discovery.stdout
    assert "newest-installed-kernel=" in discovery.stdout

    config_file = tmp_path / "config.local.conf"
    first = run_script(
        CONFIGURATOR,
        ["--output", posix_path(config_file)],
        fake_bin=fake_bin,
        stdin="\n\n",
    )
    assert first.returncode == 0, first.stderr
    assert config_file.is_file()
    assert (config_file.stat().st_mode & 0o777) == 0o600
    content = config_file.read_text(encoding="utf-8")
    assert "OUTPUT_FILE=/var/lib/prometheus/node-exporter/dnf.prom" in content
    assert "KERNEL_PACKAGE=kernel" in content

    second = run_script(
        CONFIGURATOR,
        ["--output", posix_path(config_file)],
        fake_bin=fake_bin,
        stdin="\n\n",
    )
    assert second.returncode == 1
    assert "refusing to replace existing file" in second.stderr


def test_collector_metrics_repo_counts_and_obsoleting_ignored(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    all_updates = """NetworkManager.x86_64    1:1.44.0-1.el9_3    baseos
curl.x86_64              7.76.1-29.el9_3     baseos
vim-minimal.x86_64       2:8.2.2637-20.el9_3 appstream

Obsoleting Packages
obsolete-pkg.x86_64      1.0-1.el9           baseos
    replacement.x86_64   2.0-1.el9           baseos
"""
    sec_updates = """curl.x86_64              7.76.1-29.el9_3     baseos

Obsoleting Packages
obsolete-pkg.x86_64      1.0-1.el9           baseos
"""
    make_fake_environment(
        fake_bin,
        all_updates=all_updates,
        all_rc=100,
        sec_updates=sec_updates,
        sec_rc=100,
        running_kernel="5.14.0-362.8.1.el9_3.x86_64",
        installed_kernels=["5.14.0-362.8.1.el9_3.x86_64"],
    )

    out_file = tmp_path / "dnf.prom"
    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 0, completed.stderr
    assert out_file.is_file()

    metrics = out_file.read_text(encoding="utf-8")
    assert 'dnf_upgrades_pending{repo="appstream"} 1' in metrics
    assert 'dnf_upgrades_pending{repo="baseos"} 2' in metrics
    assert "dnf_security_upgrades_pending 1" in metrics
    assert "node_reboot_required 0" in metrics
    assert "dnf_updates_check_timestamp_seconds" in metrics
    assert 'dnf_upgrades_pending{repo="baseos"} 3' not in metrics


def test_collector_reboot_flag(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(
        fake_bin,
        all_updates="",
        all_rc=0,
        sec_updates="",
        sec_rc=0,
        running_kernel="5.14.0-362.8.1.el9_3.x86_64",
        installed_kernels=[
            "5.14.0-362.8.1.el9_3.x86_64",
            "5.14.0-427.13.1.el9_4.x86_64",
        ],
    )
    out_file = tmp_path / "dnf.prom"
    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 0, completed.stderr
    metrics = out_file.read_text(encoding="utf-8")
    assert "node_reboot_required 1" in metrics
    assert "dnf_security_upgrades_pending 0" in metrics

    make_fake_environment(
        fake_bin,
        all_updates="",
        all_rc=0,
        sec_updates="",
        sec_rc=0,
        running_kernel="5.14.0-427.13.1.el9_4.x86_64",
        installed_kernels=["5.14.0-427.13.1.el9_4.x86_64"],
    )
    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 0, completed.stderr
    metrics = out_file.read_text(encoding="utf-8")
    assert "node_reboot_required 0" in metrics


def test_collector_atomic_write_and_permissions(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(fake_bin, all_rc=0, sec_rc=0)

    out_file = tmp_path / "dnf.prom"
    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 0, completed.stderr
    assert out_file.is_file()
    assert (out_file.stat().st_mode & 0o777) == 0o644

    staged = list(tmp_path.glob("dnf.prom.*"))
    assert staged == []


def test_collector_real_dnf_failure_leaves_previous_file_untouched(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(
        fake_bin,
        all_rc=1,
        sec_rc=1,
    )

    out_file = tmp_path / "dnf.prom"
    initial_content = (
        "# HELP dnf_updates_check_timestamp_seconds When this file was written.\n"
        "# TYPE dnf_updates_check_timestamp_seconds gauge\n"
        "dnf_updates_check_timestamp_seconds 1234567890\n"
    )
    out_file.write_text(initial_content, encoding="utf-8")
    initial_mtime = out_file.stat().st_mtime_ns

    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 2
    assert "dnf check-update exited with status 1" in completed.stderr
    assert "left unchanged" in completed.stderr

    assert out_file.read_text(encoding="utf-8") == initial_content
    assert out_file.stat().st_mtime_ns == initial_mtime


def test_collector_dry_run_does_not_modify_disk(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(fake_bin, all_rc=0, sec_rc=0)

    out_file = tmp_path / "dnf.prom"
    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file), "--dry-run"],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 0, completed.stderr
    assert not out_file.exists()
    assert "# TYPE dnf_updates_check_timestamp_seconds gauge" in completed.stdout
    assert "dry-run:" in completed.stderr


def test_collector_refuses_to_replace_foreign_file(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(fake_bin, all_rc=0, sec_rc=0)

    out_file = tmp_path / "dnf.prom"
    out_file.write_text("unrelated_metric 123\n", encoding="utf-8")

    completed = run_script(
        SCRIPT,
        ["--output", posix_path(out_file)],
        fake_bin=fake_bin,
    )
    assert completed.returncode == 1
    assert "refusing to replace" in completed.stderr
    assert out_file.read_text(encoding="utf-8") == "unrelated_metric 123\n"


def test_collector_config_file_and_cli_precedence(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    make_fake_environment(fake_bin, all_rc=0, sec_rc=0)

    cfg_out_file = tmp_path / "from_config.prom"
    cli_out_file = tmp_path / "from_cli.prom"
    config_file = tmp_path / "custom.conf"
    config_file.write_text(
        f"OUTPUT_FILE={posix_path(cfg_out_file)}\nKERNEL_PACKAGE=kernel\n",
        encoding="utf-8",
    )

    run_cfg = run_script(
        SCRIPT,
        ["--config", posix_path(config_file)],
        fake_bin=fake_bin,
    )
    assert run_cfg.returncode == 0, run_cfg.stderr
    assert cfg_out_file.is_file()
    assert not cli_out_file.exists()

    run_cli = run_script(
        SCRIPT,
        ["--config", posix_path(config_file), "--output", posix_path(cli_out_file)],
        fake_bin=fake_bin,
    )
    assert run_cli.returncode == 0, run_cli.stderr
    assert cli_out_file.is_file()
