from pathlib import Path

import pytest

import tool_catalog


def make_tool(root: Path, relative: str, manifest: str | None) -> Path:
    folder = root / relative
    folder.mkdir(parents=True)
    (folder / "tool_name.py").write_text("", encoding="utf-8")
    if manifest is not None:
        (folder / "tool.toml").write_text(manifest, encoding="utf-8")
    return folder


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for category in tool_catalog.CATEGORIES:
        (tmp_path / category).mkdir()
    return tmp_path


def test_finds_tools_by_name_in_any_category(root: Path) -> None:
    make_tool(
        root,
        "security/tool-name",
        'help = [["tool_name.py", "--help"]]\nlocal_files = ["config.local.toml"]\n',
    )

    found = tool_catalog.tool("tool-name", root)

    assert found.relative == "security/tool-name"
    assert found.help_commands == (("tool_name.py", "--help"),)
    assert found.local_files == ("config.local.toml",)
    assert found.has_configurator
    assert not found.is_ansible_project


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (None, "missing"),
        ("helps = []\n", "unknown keys: helps"),
        ('help = [["missing.py", "--help"]]\n', "help script not found"),
        ('help = [["tool_name.ps1"]]\n', "can only run help"),
        ('local_files = "config.local.toml"\n', "local_files must be a list"),
        ('configurator = "no"\n', "configurator must be true or false"),
    ],
)
def test_rejects_a_bad_manifest(root: Path, manifest: str | None, message: str) -> None:
    folder = make_tool(root, "utilities/tool-name", manifest)
    if manifest and "ps1" in manifest:
        (folder / "tool_name.ps1").write_text("", encoding="utf-8")

    with pytest.raises(tool_catalog.ManifestError, match=message):
        tool_catalog.tools(root)
