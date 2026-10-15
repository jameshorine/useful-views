"""The user guide: generated reference pages match the code, and every page
is in the sidebar.

If this fails, regenerate them: uv run python scripts/generate_docs.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("generate_docs", ROOT / "scripts" / "generate_docs.py")
generate_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate_docs)


@pytest.mark.parametrize("name", sorted(generate_docs.PAGES))
def test_generated_page_is_up_to_date(name):
    page = ROOT / "user_guide" / "reference" / name
    assert page.read_text(encoding="utf-8") == generate_docs.PAGES[name](), (
        f"user_guide/reference/{name} is out of date; run: uv run python scripts/generate_docs.py"
    )


def test_every_guide_page_is_in_the_sidebar():
    """great-docs.yml lists the user guide's pages in order; a page left out
    would be built but never linked."""
    import re

    config = (ROOT / "great-docs.yml").read_text(encoding="utf-8")
    listed = re.findall(r"^\s+- ([\w/.-]+\.md)\s*$", config.split("\nuser_guide:", 1)[1].split("\n\n", 1)[0], re.M)
    pages = sorted(p.relative_to(ROOT / "user_guide").as_posix() for p in (ROOT / "user_guide").rglob("*.md"))
    assert sorted(listed) == pages and len(listed) == len(set(listed))
