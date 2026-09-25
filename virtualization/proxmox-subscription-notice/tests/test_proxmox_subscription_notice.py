import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
SCRIPT = "proxmox-subscription-notice.sh"
STOCK = "res.data.status.toLowerCase() !== 'active'"
PATCHED = "res.data.status.toLowerCase() == 'NoMoreNagging'"
STOCK_FIXTURE = f"""if ({STOCK}) {{
    first();
}}
const subscription = !({STOCK});
"""


@cache
def wsl_executable() -> str:
    wsl = shutil.which("wsl.exe")
    assert wsl is not None, "Install WSL with Bash to run these tests on Windows."
    return wsl


def posix_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    translated = subprocess.run(
        [wsl_executable(), "-e", "wslpath", "-a", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return translated.stdout.strip()


def run_script(
    script: str,
    arguments: list[str],
    fake_bin: Path,
    variables: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    extra = variables or {}
    if os.name == "nt":
        command = [
            wsl_executable(),
            "-e",
            "env",
            *(f"{key}={value}" for key, value in extra.items()),
            "bash",
            "-c",
            'PATH="$1:$PATH"; shift; exec bash "$@"',
            "run-script",
            posix_path(fake_bin),
            posix_path(TOOL / script),
            *arguments,
        ]
        environment = None
    else:
        environment = os.environ.copy()
        environment.update(extra)
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


def fake_environment(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_executable(
        fake_bin / "systemctl",
        'printf \'%s\\n\' "$*" >>"$FAKE_SYSTEMCTL_LOG"\n'
        'if [[ $1 == restart ]]; then exit "${FAKE_RESTART_STATUS:-0}"; fi\n'
        "exit 0\n",
    )
    write_executable(
        fake_bin / "dpkg-query",
        "[[ ${!#} == proxmox-widget-toolkit ]] || exit 1\nprintf '5.2.6'\n",
    )
    log = tmp_path / "systemctl.log"
    return fake_bin, log, {"FAKE_SYSTEMCTL_LOG": posix_path(log)}


def write_fixture(tmp_path: Path, content: str = STOCK_FIXTURE) -> Path:
    path = tmp_path / "proxmoxlib.js"
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def systemctl_calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()


def leftover_files(tmp_path: Path) -> list[str]:
    return sorted(
        path.name for path in tmp_path.iterdir() if path.name.startswith("proxmoxlib.")
    )


def test_tool_is_self_contained() -> None:
    files = {path.name for path in TOOL.iterdir() if path.is_file()}

    assert files >= {"README.md", "config.example.conf", "configure.sh", SCRIPT}


def test_help_names_modes_and_exit_codes(tmp_path: Path) -> None:
    fake_bin, _, variables = fake_environment(tmp_path)

    completed = run_script(SCRIPT, ["--help"], fake_bin, variables)

    assert completed.returncode == 0, completed.stderr
    for fragment in ("--check", "--apply", "--restore", "--no-restart", "64  invalid"):
        assert fragment in completed.stdout


def test_example_marks_every_owned_value() -> None:
    example = (TOOL / "config.example.conf").read_text(encoding="utf-8")
    script = (TOOL / SCRIPT).read_text(encoding="utf-8")

    assert example.count("CUSTOMIZE:") == 4
    assert "eval" not in script


def test_default_mode_checks_without_changing_the_file(tmp_path: Path) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    fixture = write_fixture(tmp_path)

    completed = run_script(SCRIPT, ["--file", posix_path(fixture)], fake_bin, variables)

    assert completed.returncode == 1
    assert "proxmox-widget-toolkit 5.2.6: patch required" in completed.stdout
    assert fixture.read_text(encoding="utf-8") == STOCK_FIXTURE
    assert systemctl_calls(log) == []


def test_apply_patches_both_checks_restarts_once_and_is_idempotent(
    tmp_path: Path,
) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    fixture = write_fixture(tmp_path)
    fixture.chmod(0o640)
    arguments = ["--file", posix_path(fixture), "--apply"]

    first = run_script(SCRIPT, arguments, fake_bin, variables)
    second = run_script(SCRIPT, arguments, fake_bin, variables)
    check = run_script(SCRIPT, ["--file", posix_path(fixture)], fake_bin, variables)

    assert first.returncode == 0, first.stderr
    assert "patch applied" in first.stdout
    text = fixture.read_text(encoding="utf-8")
    assert text.count(PATCHED) == 2
    assert STOCK not in text
    if os.name != "nt":
        assert fixture.stat().st_mode & 0o777 == 0o640
    assert second.returncode == 0, second.stderr
    assert "patch already present" in second.stdout
    assert check.returncode == 0
    assert systemctl_calls(log) == ["restart pveproxy", "is-active --quiet pveproxy"]
    assert leftover_files(tmp_path) == ["proxmoxlib.js"]


def test_restore_reverses_the_patch_byte_for_byte(tmp_path: Path) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    original = STOCK_FIXTURE + "trailing text without a newline"
    fixture = write_fixture(tmp_path, original)
    path = posix_path(fixture)

    applied = run_script(
        SCRIPT, ["--file", path, "--apply", "--no-restart"], fake_bin, variables
    )
    restored = run_script(SCRIPT, ["--file", path, "--restore"], fake_bin, variables)
    again = run_script(SCRIPT, ["--file", path, "--restore"], fake_bin, variables)

    assert applied.returncode == 0, applied.stderr
    assert "service-restart-skipped" in applied.stdout
    assert restored.returncode == 0, restored.stderr
    assert "stock subscription checks restored" in restored.stdout
    assert fixture.read_bytes() == original.encode()
    assert again.returncode == 0
    assert "already present" in again.stdout
    assert systemctl_calls(log) == ["restart pveproxy", "is-active --quiet pveproxy"]


def test_every_mode_refuses_an_unsupported_layout(tmp_path: Path) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    layouts = (
        f"if ({STOCK}) {{\n    only_one();\n}}\n",
        f"if ({STOCK}) {{}}\nif ({PATCHED}) {{}}\n",
        f"if ({STOCK}) {{}}\nif ({STOCK}) {{}}\nif ({STOCK}) {{}}\n",
    )
    for layout in layouts:
        fixture = write_fixture(tmp_path, layout)
        for mode in ("--check", "--apply", "--restore"):
            completed = run_script(
                SCRIPT, ["--file", posix_path(fixture), mode], fake_bin, variables
            )

            assert completed.returncode == 3, (layout, mode, completed.stderr)
            assert "unsupported subscription-check layout" in completed.stderr
            assert fixture.read_text(encoding="utf-8") == layout
    assert systemctl_calls(log) == []
    assert leftover_files(tmp_path) == ["proxmoxlib.js"]


def test_missing_file_exits_two(tmp_path: Path) -> None:
    fake_bin, _, variables = fake_environment(tmp_path)

    completed = run_script(
        SCRIPT,
        ["--file", posix_path(tmp_path / "missing.js"), "--apply"],
        fake_bin,
        variables,
    )

    assert completed.returncode == 2
    assert "toolkit file not found" in completed.stderr


def test_failed_restart_exits_five_after_a_verified_change(tmp_path: Path) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    fixture = write_fixture(tmp_path)

    completed = run_script(
        SCRIPT,
        ["--file", posix_path(fixture), "--apply"],
        fake_bin,
        {**variables, "FAKE_RESTART_STATUS": "1"},
    )

    assert completed.returncode == 5
    assert "did not restart" in completed.stderr
    assert fixture.read_text(encoding="utf-8").count(PATCHED) == 2
    assert systemctl_calls(log) == ["restart pveproxy"]


def test_command_line_overrides_config_and_config_overrides_defaults(
    tmp_path: Path,
) -> None:
    fake_bin, log, variables = fake_environment(tmp_path)
    fixture = write_fixture(tmp_path)
    config = tmp_path / "config.local.conf"
    config.write_text(
        "# local test config\n"
        f"TOOLKIT_FILE={posix_path(fixture)}\n"
        "SERVICE_NAME=proxmox-backup-proxy\n"
        "RESTART_SERVICE=no\n",
        encoding="utf-8",
        newline="\n",
    )

    from_config = run_script(
        SCRIPT, ["--config", posix_path(config), "--apply"], fake_bin, variables
    )
    restored = run_script(
        SCRIPT,
        ["--config", posix_path(config), "--restore", "--restart"],
        fake_bin,
        variables,
    )

    assert from_config.returncode == 0, from_config.stderr
    assert "restart proxmox-backup-proxy before browsers" in from_config.stdout
    assert restored.returncode == 0, restored.stderr
    assert systemctl_calls(log) == [
        "restart proxmox-backup-proxy",
        "is-active --quiet proxmox-backup-proxy",
    ]


def test_config_rejects_unknown_and_duplicate_keys(tmp_path: Path) -> None:
    fake_bin, _, variables = fake_environment(tmp_path)
    for content in ("UNKNOWN_KEY=1\n", "RESTART_SERVICE=no\nRESTART_SERVICE=yes\n"):
        config = tmp_path / "config.local.conf"
        config.write_text(content, encoding="utf-8", newline="\n")

        completed = run_script(
            SCRIPT, ["--config", posix_path(config)], fake_bin, variables
        )

        assert completed.returncode == 64
        assert "error:" in completed.stderr


def test_config_values_are_literal_and_not_evaluated(tmp_path: Path) -> None:
    fake_bin, _, variables = fake_environment(tmp_path)
    config = tmp_path / "config.local.conf"
    config.write_text(
        "TOOLKIT_FILE=/nonexistent/$NOT_EVALUATED/`whoami`\n",
        encoding="utf-8",
        newline="\n",
    )

    completed = run_script(
        SCRIPT, ["--config", posix_path(config)], fake_bin, variables
    )

    assert completed.returncode == 2
    assert "$NOT_EVALUATED" in completed.stderr
    assert "`whoami`" in completed.stderr


def test_configurator_writes_config_and_refuses_overwrite(tmp_path: Path) -> None:
    fake_bin, _, variables = fake_environment(tmp_path)
    output = tmp_path / "config.local.conf"
    arguments = ["--output", posix_path(output)]

    first = run_script(
        "configure.sh",
        arguments,
        fake_bin,
        variables,
        stdin=f"{posix_path(tmp_path / 'proxmoxlib.js')}\n\nproxmox-backup-proxy\nno\n",
    )
    second = run_script("configure.sh", arguments, fake_bin, variables, stdin="")

    assert first.returncode == 0, first.stderr
    assert "toolkit-version=5.2.6" in first.stdout
    contents = output.read_text(encoding="utf-8")
    assert contents.count("CUSTOMIZE:") == 4
    assert "TOOLKIT_PACKAGE=proxmox-widget-toolkit\n" in contents
    assert "SERVICE_NAME=proxmox-backup-proxy\n" in contents
    assert "RESTART_SERVICE=no\n" in contents
    assert second.returncode == 1
    assert "refusing to replace" in second.stderr
