"""Keep the public documentation navigation consistent with current files."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_markdown_links_resolve_after_consolidation():
    files = [ROOT / name for name in ("README.md", "PROGRESS.md", "PLATFORM_PLAN.md")]
    files += list((ROOT / "docs").glob("*.md"))
    files += list((ROOT / "examples").rglob("*.md"))
    missing = []
    for file in files:
        for reference in re.findall(r"\]\(([^)]+?\.md)(?:#[^)]*)?\)", file.read_text(encoding="utf-8")):
            if "://" not in reference and not (file.parent / reference).is_file():
                missing.append((file.relative_to(ROOT).as_posix(), reference))
    assert not missing, missing


def test_readme_indexes_each_active_document_once():
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    table = content.split("## 文档入口", 1)[1]
    for file in (ROOT / "docs").glob("*.md"):
        assert table.count("](docs/" + file.name + ")") == 1, file.name
