import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def test_local_markdown_links_resolve() -> None:
    broken: list[str] = []
    for document in ROOT.rglob("*.md"):
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>")
            if target.startswith(("#", "mailto:")) or "://" in target:
                continue
            path_part = unquote(target.split("#", 1)[0])
            if path_part and not (document.parent / path_part).exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")
    assert broken == []
