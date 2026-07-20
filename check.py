#!/usr/bin/env python3
"""Run every required local check for this repository."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SSH_TOOL = ROOT / "identity-and-access" / "ssh-key-rotation"

INSTALL_HELP = {
    "python": "Install Python 3.11 or newer, then recreate the virtual environment.",
    "python-packages": (
        "Run: python -m pip install --requirement requirements-dev.txt"
    ),
    "linux-checkers": (
        "Install Bash and ShellCheck plus a Linux Python environment containing "
        "ansible-core and ansible-lint. On Windows, install them inside WSL. "
        "Then install the collections from the SSH tool's requirements.yml."
    ),
    "ansible-collections": (
        "Run from identity-and-access/ssh-key-rotation: "
        "ansible-galaxy collection install --requirements-file requirements.yml"
    ),
    "powershell": "Install Windows PowerShell 5.1 or PowerShell 7 and add it to PATH.",
    "gitleaks": (
        "Install Gitleaks from https://github.com/gitleaks/gitleaks/releases "
        "and add gitleaks to PATH."
    ),
}


class CheckRunner:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def fail(self, name: str, detail: str, install_key: str | None = None) -> None:
        print(f"FAIL {name}: {detail}", file=sys.stderr)
        if install_key:
            print(f"  {INSTALL_HELP[install_key]}", file=sys.stderr)
        self.failures.append(name)

    def pass_check(self, name: str) -> None:
        print(f"PASS {name}")
        self.passed += 1

    def command(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path = ROOT,
        install_key: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        executable = shutil.which(command[0])
        if executable is None:
            self.fail(name, f"required command not found: {command[0]}", install_key)
            return
        command[0] = executable
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode == 0:
            self.pass_check(name)
        else:
            self.fail(
                name,
                f"command exited with status {completed.returncode}",
                install_key,
            )


def require_python_modules(runner: CheckRunner) -> bool:
    missing = [
        module
        for module in ("pytest", "ruff", "yaml")
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        runner.fail(
            "Python development packages",
            f"missing modules: {', '.join(missing)}",
            "python-packages",
        )
        return False
    runner.pass_check("Python development packages")
    return True


def parse_examples(runner: CheckRunner) -> None:
    try:
        conf_files = sorted(ROOT.rglob("config.example.conf"))
        json_files = sorted(ROOT.rglob("config.example.json"))
        toml_files = sorted(ROOT.rglob("config.example.toml"))
        yaml_files = sorted(ROOT.rglob("*.yml.example"))
        for path in conf_files:
            seen: set[str] = set()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line or line.startswith("#"):
                    continue
                match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
                if match is None:
                    raise ValueError(
                        f"{path}:{line_number}: expected strict KEY=value syntax"
                    )
                key = match.group(1)
                if key in seen:
                    raise ValueError(f"{path}:{line_number}: duplicate key: {key}")
                seen.add(key)
        for path in json_files:
            json.loads(path.read_text(encoding="utf-8"))
        for path in toml_files:
            with path.open("rb") as handle:
                tomllib.load(handle)
        import yaml

        for path in yaml_files:
            yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        runner.fail("Configuration example parsing", str(exc))
        return
    runner.pass_check("Configuration example parsing")


def check_python_help(runner: CheckRunner) -> None:
    cases = (
        (
            "Prometheus validator help",
            "monitoring/prometheus-target-check/check_targets.py",
            ["--help"],
        ),
        (
            "Prometheus configurator help",
            "monitoring/prometheus-target-check/configure.py",
            ["--help"],
        ),
        (
            "Semaphore backup help",
            "backup-and-recovery/semaphore-sqlite-guard/semaphore_sqlite.py",
            ["backup", "--help"],
        ),
        (
            "Semaphore configurator help",
            "backup-and-recovery/semaphore-sqlite-guard/configure.py",
            ["--help"],
        ),
        (
            "Semaphore comparison help",
            "backup-and-recovery/semaphore-sqlite-guard/semaphore_sqlite.py",
            ["compare", "--help"],
        ),
        (
            "TeamSpeak export help",
            "migrations/teamspeak-channel-migration/teamspeak_channels.py",
            ["export", "--help"],
        ),
        (
            "TeamSpeak import help",
            "migrations/teamspeak-channel-migration/teamspeak_channels.py",
            ["import", "--help"],
        ),
        (
            "TeamSpeak configurator help",
            "migrations/teamspeak-channel-migration/configure.py",
            ["--help"],
        ),
        (
            "SSH configurator help",
            "identity-and-access/ssh-key-rotation/configure.py",
            ["--help"],
        ),
        (
            "SSH validator help",
            "identity-and-access/ssh-key-rotation/tests/validate_project.py",
            ["--help"],
        ),
    )
    for name, relative_path, arguments in cases:
        runner.command(name, [sys.executable, str(ROOT / relative_path), *arguments])


def linux_check_script(root: str) -> str:
    bash_files = [
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in sorted(ROOT.rglob("*.sh"))
        if not {".git", ".venv"}.intersection(path.relative_to(ROOT).parts)
    ]
    quoted_bash_files = " ".join(shlex.quote(path) for path in bash_files)
    playbooks = (
        "playbooks/ssh-identity-onboard.yml",
        "playbooks/ssh-key-audit.yml",
        "playbooks/ssh-key-stage.yml",
        "playbooks/ssh-key-verify.yml",
        "playbooks/ssh-key-retire.yml",
    )
    quoted_playbooks = " ".join(shlex.quote(path) for path in playbooks)
    extra_bin = os.environ.get("TOOLS_AND_SCRIPTS_WSL_BIN", "")
    collections = os.environ.get("TOOLS_AND_SCRIPTS_ANSIBLE_COLLECTIONS", "")
    lines = ["set -eu"]
    if extra_bin:
        lines.append(f"export PATH={shlex.quote(extra_bin)}:$PATH")
    if collections:
        lines.append(f"export ANSIBLE_COLLECTIONS_PATH={shlex.quote(collections)}")
    lines.extend(
        (
            "for tool in bash shellcheck ansible-playbook ansible-lint; do "
            'command -v "$tool" >/dev/null || { '
            'echo "missing-checker: $tool" >&2; exit 127; }; done',
            f"cd {shlex.quote(root)}",
            f"bash -n {quoted_bash_files}",
            f"shellcheck {quoted_bash_files}",
            "bash networking/networkmanager-cutover/networkmanager-cutover.sh "
            "--help >/dev/null",
            "bash networking/networkmanager-cutover/configure.sh --help >/dev/null",
            "ssh_tool=identity-and-access/ssh-key-rotation",
            "inventory=$(mktemp --suffix=.yml)",
            "trap 'rm -f \"$inventory\"' EXIT",
            'cp "$ssh_tool/inventory/hosts.yml.example" "$inventory"',
            'cd "$ssh_tool"',
            "ansible-lint --offline",
            f"for playbook in {quoted_playbooks}; do "
            'ansible-playbook -i "$inventory" --syntax-check "$playbook"; done',
        )
    )
    return "\n".join(lines)


def run_linux_checks(runner: CheckRunner) -> None:
    if os.name == "nt":
        wsl = shutil.which("wsl.exe")
        if wsl is None:
            runner.fail(
                "Linux controller checks", "wsl.exe is missing", "linux-checkers"
            )
            return
        translated = subprocess.run(
            [wsl, "-e", "wslpath", "-a", str(ROOT)],
            check=False,
            capture_output=True,
            text=True,
        )
        if translated.returncode != 0 or not translated.stdout.strip():
            runner.fail(
                "Linux controller checks",
                "WSL could not translate the repository path",
                "linux-checkers",
            )
            return
        command = [
            wsl,
            "-e",
            "bash",
            "-lc",
            linux_check_script(translated.stdout.strip()),
        ]
    else:
        command = ["bash", "-lc", linux_check_script(str(ROOT))]
    runner.command(
        "Bash, ShellCheck, Ansible lint, and Ansible syntax",
        command,
        install_key="linux-checkers",
    )


def powershell_parser_command(files: list[Path]) -> str:
    quoted = ",".join("'" + str(path).replace("'", "''") + "'" for path in files)
    common = str(SSH_TOOL / "playbooks" / "files" / "AuthorizedKey.Common.ps1").replace(
        "'", "''"
    )
    templates = ",".join(
        "'" + str(SSH_TOOL / "playbooks" / "files" / filename).replace("'", "''") + "'"
        for filename in ("Manage-AuthorizedKey.ps1", "Read-AuthorizedKeyState.ps1")
    )
    return (
        f"$files=@({quoted});$failed=$false;foreach($file in $files){{"
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$file,[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count -gt 0){$errors|ForEach-Object{Write-Error $_.Message};"
        "$failed=$true}};"
        f"$common=Get-Content -Raw -LiteralPath '{common}';"
        f"$templates=@({templates});foreach($file in $templates){{"
        "$payload=(Get-Content -Raw -LiteralPath $file).Replace("
        "'# AUTHORIZED_KEY_COMMON_FUNCTIONS',$common);"
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseInput("
        "$payload,[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count -gt 0){$errors|ForEach-Object{Write-Error $_.Message};"
        "$failed=$true}};if($failed){exit 1}"
    )


def run_powershell_checks(runner: CheckRunner) -> None:
    files = sorted(ROOT.rglob("*.ps1"))
    executable = "powershell.exe" if os.name == "nt" else "pwsh"
    runner.command(
        "PowerShell parsing",
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            powershell_parser_command(files),
        ],
        install_key="powershell",
    )


def run_gitleaks(runner: CheckRunner) -> None:
    runner.command(
        "Complete-history secret scan",
        [
            "gitleaks",
            "git",
            "--redact",
            "--no-banner",
            "--log-opts=--all",
            ".",
        ],
        install_key="gitleaks",
    )


def main() -> int:
    runner = CheckRunner()
    if sys.version_info < (3, 11):  # noqa: UP036
        runner.fail("Python version", sys.version.split()[0], "python")
    else:
        runner.pass_check(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    if require_python_modules(runner):
        runner.command("Ruff lint", [sys.executable, "-m", "ruff", "check", "."])
        runner.command(
            "Ruff formatting",
            [sys.executable, "-m", "ruff", "format", "--check", "."],
        )
        runner.command("Pytest", [sys.executable, "-m", "pytest"])
        runner.command(
            "Python compilation",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                str(ROOT / "check.py"),
                str(ROOT / "tests"),
                str(ROOT / "networking"),
                str(ROOT / "monitoring"),
                str(ROOT / "backup-and-recovery"),
                str(ROOT / "migrations"),
                str(ROOT / "identity-and-access"),
            ],
        )
        parse_examples(runner)
        check_python_help(runner)
        runner.command(
            "SSH project validator",
            [
                sys.executable,
                str(SSH_TOOL / "tests" / "validate_project.py"),
                "--inventory",
                "hosts.yml.example",
            ],
        )

    run_linux_checks(runner)
    run_powershell_checks(runner)
    run_gitleaks(runner)

    if runner.failures:
        print(
            f"\nLocal verification failed: {len(runner.failures)} check(s): "
            + ", ".join(runner.failures),
            file=sys.stderr,
        )
        return 1
    print(f"\nLocal verification passed: {runner.passed} checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
