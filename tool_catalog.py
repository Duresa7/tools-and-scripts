"""Find every tool in the repository and read its tool.toml manifest.

A tool is any folder directly under one of the category folders. check.py and
the repository tests call ``tools()`` instead of keeping their own path lists,
so moving or renaming a tool folder needs no edits outside that folder.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("monitoring", "fleet", "security", "maintenance", "utilities")
MANIFEST = "tool.toml"
_KEYS = {"help", "local_files", "configurator"}
# Script types whose help commands check.py knows how to run.
HELP_RUNTIMES = (".py", ".sh", ".mjs")


class ManifestError(Exception):
    """A tool folder has a missing or malformed tool.toml."""


@dataclass(frozen=True)
class Tool:
    path: Path
    help_commands: tuple[tuple[str, ...], ...]
    local_files: tuple[str, ...]
    has_configurator: bool

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def category(self) -> str:
        return self.path.parent.name

    @property
    def relative(self) -> str:
        """Repository-relative POSIX path, for example ``fleet/fleet-updates``."""
        return f"{self.category}/{self.name}"

    @property
    def is_ansible_project(self) -> bool:
        return (self.path / "ansible.cfg").is_file()


def _string_list(value: object, field: str, manifest: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{manifest}: {field} must be a list of strings")
    return tuple(value)


def _load(folder: Path) -> Tool:
    manifest = folder / MANIFEST
    if not manifest.is_file():
        raise ManifestError(f"{manifest}: missing")
    try:
        with manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{manifest}: {exc}") from exc
    unknown = set(data) - _KEYS
    if unknown:
        raise ManifestError(f"{manifest}: unknown keys: {', '.join(sorted(unknown))}")
    raw_help = data.get("help", [])
    if not isinstance(raw_help, list):
        raise ManifestError(f"{manifest}: help must be a list of commands")
    help_commands = tuple(_string_list(c, "help", manifest) for c in raw_help)
    for command in help_commands:
        if not command or not (folder / command[0]).is_file():
            raise ManifestError(f"{manifest}: help script not found: {command}")
        if Path(command[0]).suffix not in HELP_RUNTIMES:
            raise ManifestError(
                f"{manifest}: check.py can only run help for "
                f"{', '.join(HELP_RUNTIMES)} scripts: {command[0]}"
            )
    configurator = data.get("configurator", True)
    if not isinstance(configurator, bool):
        raise ManifestError(f"{manifest}: configurator must be true or false")
    return Tool(
        path=folder,
        help_commands=help_commands,
        local_files=_string_list(data.get("local_files", []), "local_files", manifest),
        has_configurator=configurator,
    )


def tools(root: Path = ROOT) -> list[Tool]:
    """Every tool, sorted by category order then name. Raises ManifestError."""
    found: list[Tool] = []
    for category in CATEGORIES:
        for folder in sorted((root / category).iterdir()):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                found.append(_load(folder))
    return found


def tool(name: str, root: Path = ROOT) -> Tool:
    """The tool whose folder is called ``name``, in whichever category it lives."""
    for candidate in tools(root):
        if candidate.name == name:
            return candidate
    raise KeyError(name)
