from __future__ import annotations

import os
import subprocess
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
EICAR_HASH = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
EICAR_ENTRY = f"{EICAR_HASH}:eicar-test-file"

SAMPLE_HASH_1 = "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"
SAMPLE_HASH_2 = "ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d"
SAMPLE_HASH_3 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def make_fake_environment(
    fake_bin: Path,
    *,
    curl_output: str = "",
    curl_exit_code: int = 0,
    install_exit_code: int = 0,
    install_corrupt: bool = False,
    systemctl_exit_code: int = 0,
    install_log: Path | None = None,
    systemctl_log: Path | None = None,
) -> dict[str, str]:
    fake_bin.mkdir(parents=True, exist_ok=True)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${FAKE_CURL_LOG:-}" != "" ]]; then
  printf '%s\\n' "$*" >> "$FAKE_CURL_LOG"
fi
if [[ "${FAKE_CURL_EXIT_CODE:-0}" != "0" ]]; then
  exit "${FAKE_CURL_EXIT_CODE}"
fi
if [[ -f "${FAKE_CURL_OUTPUT_FILE:-}" ]]; then
  cat "${FAKE_CURL_OUTPUT_FILE}"
else
  printf '%s' "${FAKE_CURL_OUTPUT:-}"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    fake_install = fake_bin / "install"
    fake_install.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${FAKE_INSTALL_LOG:-}" != "" ]]; then
  printf '%s\\n' "$*" >> "$FAKE_INSTALL_LOG"
fi
dest="${@: -1}"
src="${@: -2:1}"
if [[ "${FAKE_INSTALL_CORRUPT:-0}" != "0" ]]; then
  printf 'corrupted_partial_data_from_aborted_install\\n' > "$dest"
fi
if [[ "${FAKE_INSTALL_EXIT_CODE:-0}" != "0" ]]; then
  exit "${FAKE_INSTALL_EXIT_CODE}"
fi
cp "$src" "$dest"
exit 0
""",
        encoding="utf-8",
    )
    fake_install.chmod(0o755)

    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${FAKE_SYSTEMCTL_LOG:-}" != "" ]]; then
  printf '%s\\n' "$*" >> "$FAKE_SYSTEMCTL_LOG"
fi
if [[ "${FAKE_SYSTEMCTL_EXIT_CODE:-0}" != "0" ]]; then
  exit "${FAKE_SYSTEMCTL_EXIT_CODE}"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["FAKE_CURL_OUTPUT"] = curl_output
    env["FAKE_CURL_EXIT_CODE"] = str(curl_exit_code)
    env["FAKE_INSTALL_EXIT_CODE"] = str(install_exit_code)
    env["FAKE_INSTALL_CORRUPT"] = "1" if install_corrupt else "0"
    env["FAKE_SYSTEMCTL_EXIT_CODE"] = str(systemctl_exit_code)
    if install_log:
        env["FAKE_INSTALL_LOG"] = str(install_log)
    if systemctl_log:
        env["FAKE_SYSTEMCTL_LOG"] = str(systemctl_log)
    return env


def test_tool_is_self_contained() -> None:
    required = {
        "README.md",
        "config.example.conf",
        "configure.sh",
        "wazuh-hash-list-refresh.sh",
        "systemd/wazuh-hash-list.service",
        "systemd/wazuh-hash-list.timer",
        "tests/test_wazuh_hash_list_refresh.py",
    }
    files = {
        str(path.relative_to(TOOL)).replace("\\", "/")
        for path in TOOL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files >= required


def test_help_options() -> None:
    refresh_help = subprocess.run(
        ["bash", "wazuh-hash-list-refresh.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )
    assert refresh_help.returncode == 0, refresh_help.stderr
    assert "--dry-run" in refresh_help.stdout
    assert "--output" in refresh_help.stdout
    assert "--feed-url" in refresh_help.stdout
    assert "--list-path" in refresh_help.stdout
    assert "--owner" in refresh_help.stdout
    assert "--group" in refresh_help.stdout
    assert "--mode" in refresh_help.stdout
    assert "--min-entries" in refresh_help.stdout
    assert "--curl-timeout" in refresh_help.stdout
    assert "--restart-unit" in refresh_help.stdout
    assert "--restart-command" in refresh_help.stdout
    assert "--verify-command" in refresh_help.stdout
    assert "--config" in refresh_help.stdout

    config_help = subprocess.run(
        ["bash", "configure.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )
    assert config_help.returncode == 0, config_help.stderr
    assert "--output" in config_help.stdout
    assert "--overwrite" in config_help.stdout
    assert "--print-discovery" in config_help.stdout


def test_config_marks_every_owned_value() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")
    assert example.count("CUSTOMIZE:") == 10
    assert "FEED_URL=" in example
    assert "LIST_PATH=" in example
    assert "OWNER=" in example
    assert "GROUP=" in example
    assert "MODE=" in example
    assert "MIN_ENTRIES=" in example
    assert "CURL_TIMEOUT=" in example
    assert "RESTART_UNIT=" in example
    assert "RESTART_COMMAND=" in example
    assert "VERIFY_COMMAND=" in example


def test_configurator_discovery_and_non_overwrite(tmp_path: Path) -> None:
    discovery = subprocess.run(
        ["bash", "configure.sh", "--print-discovery"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
    )
    assert discovery.returncode == 0, discovery.stderr
    assert "feed-url=" in discovery.stdout
    assert "list-path=" in discovery.stdout
    assert "detected-owner=" in discovery.stdout
    assert "detected-group=" in discovery.stdout

    output_config = tmp_path / "config.local.conf"
    first = subprocess.run(
        ["bash", "configure.sh", "--output", str(output_config)],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        input="\n" * 10,
    )
    assert first.returncode == 0, first.stderr
    assert output_config.is_file()
    assert (output_config.stat().st_mode & 0o777) == 0o600

    content = output_config.read_text(encoding="utf-8")
    assert "FEED_URL=" in content
    assert "LIST_PATH=" in content

    # Refuse overwrite without flag
    second = subprocess.run(
        ["bash", "configure.sh", "--output", str(output_config)],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        input="\n" * 10,
    )
    assert second.returncode == 1
    assert "refusing to replace existing file" in second.stderr

    # Overwrite with --overwrite
    third = subprocess.run(
        ["bash", "configure.sh", "--output", str(output_config), "--overwrite"],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        input="\n" * 10,
    )
    assert third.returncode == 0, third.stderr


def test_normal_feed_and_eicar_pinned_first(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n{SAMPLE_HASH_3}\n"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        install_log=install_log,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
            "--owner",
            "wazuh",
            "--group",
            "wazuh",
            "--mode",
            "660",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "known-bad hash list rebuilt: 4 entries, manager restarted" in proc.stdout

    lines = target_list.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    # EICAR pinned as first entry
    assert lines[0] == EICAR_ENTRY
    assert lines[1] == f"{SAMPLE_HASH_1}:malwarebazaar"
    assert lines[2] == f"{SAMPLE_HASH_3}:malwarebazaar"
    assert lines[3] == f"{SAMPLE_HASH_2}:malwarebazaar"

    install_records = install_log.read_text(encoding="utf-8").strip()
    assert "-o wazuh -g wazuh -m 660" in install_records
    assert str(target_list.parent) in install_records
    assert str(target_list) not in install_records

    systemctl_records = systemctl_log.read_text(encoding="utf-8").splitlines()
    assert "restart wazuh-manager.service" in systemctl_records
    assert "is-active --quiet wazuh-manager.service" in systemctl_records


def test_quoted_and_mixed_case_junk_ignored_and_duplicate_removal(
    tmp_path: Path,
) -> None:
    feed_text = f"""# Header comment line
# Recent samples
"{SAMPLE_HASH_1.upper()}"
{SAMPLE_HASH_1}
"{EICAR_HASH}"
invalid_hash_string
1234567890
""
# Middle comment
{SAMPLE_HASH_2.capitalize()}
"""
    fake_bin = tmp_path / "bin"
    target_list = tmp_path / "known-bad-hashes"

    env = make_fake_environment(fake_bin, curl_output=feed_text)

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    lines = target_list.read_text(encoding="utf-8").splitlines()
    # EICAR + SAMPLE_HASH_1 + SAMPLE_HASH_2 = 3 entries
    assert len(lines) == 3
    assert lines[0] == EICAR_ENTRY
    assert lines[1] == f"{SAMPLE_HASH_1.lower()}:malwarebazaar"
    assert lines[2] == f"{SAMPLE_HASH_2.lower()}:malwarebazaar"
    # Ensure EICAR is not duplicated
    assert sum(1 for line in lines if EICAR_HASH in line) == 1


def test_empty_feed_refused_old_list_untouched(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"
    old_content = "PREEXISTING_VALID_HASH_LIST\n"
    target_list.write_text(old_content, encoding="utf-8")

    env = make_fake_environment(
        fake_bin,
        curl_output="# Only comments\n# No hashes\n",
        install_log=install_log,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 1
    assert "refusing to install a list with 1 entries" in proc.stderr
    assert target_list.read_text(encoding="utf-8") == old_content
    assert not install_log.exists()
    assert not systemctl_log.exists()


def test_curl_failure_leaves_old_list_untouched(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"
    old_content = "PREEXISTING_VALID_HASH_LIST\n"
    target_list.write_text(old_content, encoding="utf-8")

    env = make_fake_environment(
        fake_bin,
        curl_exit_code=28,
        install_log=install_log,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 1
    assert "failed to download feed" in proc.stderr
    assert target_list.read_text(encoding="utf-8") == old_content
    assert not install_log.exists()
    assert not systemctl_log.exists()


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"
    old_content = "ORIGINAL_CONTENT\n"
    target_list.write_text(old_content, encoding="utf-8")

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        install_log=install_log,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert (
        f"dry run: built 3 entries for target {target_list} (no changes made)"
        in proc.stdout
    )
    assert target_list.read_text(encoding="utf-8") == old_content
    assert not install_log.exists()
    assert not systemctl_log.exists()


def test_output_option_writes_without_restarting(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    systemctl_log = tmp_path / "systemctl.log"
    output_file = tmp_path / "custom_output_list"

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--output",
            str(output_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "(service restart skipped)" in proc.stdout
    assert output_file.is_file()
    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0] == EICAR_ENTRY
    assert not systemctl_log.exists()


def test_config_file_and_cli_flag_precedence(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    target_list = tmp_path / "known-bad-hashes"
    config_file = tmp_path / "custom.conf"

    # Config sets high MIN_ENTRIES threshold of 10
    config_file.write_text(
        f"""FEED_URL=https://example.com/feed
LIST_PATH={target_list}
OWNER=config_owner
GROUP=config_group
MODE=640
MIN_ENTRIES=10
CURL_TIMEOUT=60
RESTART_UNIT=wazuh-manager.service
RESTART_COMMAND=
""",
        encoding="utf-8",
    )

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        install_log=install_log,
    )

    # With only config, 3 entries < 10 entries -> fails
    fail_proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--config",
            str(config_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert fail_proc.returncode == 1
    assert "refusing to install a list with 3 entries" in fail_proc.stderr

    # CLI flag overrides MIN_ENTRIES and OWNER
    success_proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--config",
            str(config_file),
            "--min-entries",
            "2",
            "--owner",
            "cli_owner",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert success_proc.returncode == 0, success_proc.stderr
    install_records = install_log.read_text(encoding="utf-8")
    assert "-o cli_owner" in install_records
    assert "-g config_group" in install_records


def test_custom_restart_command(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"
    restart_marker = tmp_path / "restarted.txt"

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
            "--restart-command",
            f"echo restarted > '{restart_marker}'",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert restart_marker.is_file()
    assert restart_marker.read_text(encoding="utf-8").strip() == "restarted"
    assert not systemctl_log.exists()


def test_atomic_installation_failure_before_rename_leaves_old_list_byte_identical(
    tmp_path: Path,
) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    systemctl_log = tmp_path / "systemctl.log"
    target_list = tmp_path / "known-bad-hashes"
    original_bytes = b"PREEXISTING_VALID_HASH_LIST_CONTENT\n"
    target_list.write_bytes(original_bytes)

    # Simulate an install command that fails midway and corrupts its target
    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        install_exit_code=1,
        install_corrupt=True,
        install_log=install_log,
        systemctl_log=systemctl_log,
    )

    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 1
    # Old list must remain byte-identical because install targeted a
    # temporary file, not target_list.
    assert target_list.read_bytes() == original_bytes
    assert not systemctl_log.exists()

    # Verify that install was never called with target_list as direct destination
    if install_log.exists():
        install_records = install_log.read_text(encoding="utf-8").strip().splitlines()
        for record in install_records:
            dest_arg = record.split()[-1]
            assert dest_arg != str(target_list)


def test_output_skips_ownership_for_standard_user(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    output_file = tmp_path / "custom_output_list"

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
        install_log=install_log,
    )

    # Standard user execution with --output: must skip -o and -g
    proc = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--output",
            str(output_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert output_file.is_file()
    install_records = install_log.read_text(encoding="utf-8").strip()
    assert "-o" not in install_records.split()
    assert "-g" not in install_records.split()

    # Also verify that empty OWNER and GROUP in config skips -o and -g
    config_file = tmp_path / "empty_owner.conf"
    config_file.write_text(
        f"""FEED_URL=https://example.com/feed
LIST_PATH={output_file}
OWNER=
GROUP=
MODE=644
MIN_ENTRIES=2
CURL_TIMEOUT=60
RESTART_UNIT=wazuh-manager.service
RESTART_COMMAND=
""",
        encoding="utf-8",
    )
    install_log.unlink()
    proc_conf = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--config",
            str(config_file),
            "--output",
            str(output_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert proc_conf.returncode == 0, proc_conf.stderr
    install_records_conf = install_log.read_text(encoding="utf-8").strip()
    assert "-o" not in install_records_conf.split()
    assert "-g" not in install_records_conf.split()


def test_custom_restart_verify_command(tmp_path: Path) -> None:
    feed_text = f"{SAMPLE_HASH_1}\n{SAMPLE_HASH_2}\n"
    fake_bin = tmp_path / "bin"
    target_list = tmp_path / "known-bad-hashes"
    restart_marker = tmp_path / "restarted.txt"
    verify_marker = tmp_path / "verified.txt"

    env = make_fake_environment(
        fake_bin,
        curl_output=feed_text,
    )

    # 1. Custom restart with failing verify command -> exits nonzero
    proc_fail = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
            "--restart-command",
            f"echo restarted > '{restart_marker}'",
            "--verify-command",
            "echo verify_failed && exit 3",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert proc_fail.returncode != 0
    assert "verify command failed" in proc_fail.stderr
    assert restart_marker.is_file()

    # 2. Custom restart with successful verify command -> exits 0
    proc_ok = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--list-path",
            str(target_list),
            "--restart-command",
            f"echo restarted > '{restart_marker}'",
            "--verify-command",
            f"echo verified > '{verify_marker}'",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert proc_ok.returncode == 0, proc_ok.stderr
    assert verify_marker.is_file()
    assert verify_marker.read_text(encoding="utf-8").strip() == "verified"

    # 3. VERIFY_COMMAND supported in configuration file
    config_file = tmp_path / "verify.conf"
    config_file.write_text(
        f"""FEED_URL=https://example.com/feed
LIST_PATH={target_list}
OWNER=wazuh
GROUP=wazuh
MODE=660
MIN_ENTRIES=2
CURL_TIMEOUT=60
RESTART_UNIT=wazuh-manager.service
RESTART_COMMAND=echo conf_restarted
VERIFY_COMMAND=exit 4
""",
        encoding="utf-8",
    )
    proc_conf = subprocess.run(
        [
            "bash",
            "wazuh-hash-list-refresh.sh",
            "--config",
            str(config_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=TOOL,
        env=env,
    )
    assert proc_conf.returncode != 0
    assert "verify command failed" in proc_conf.stderr
