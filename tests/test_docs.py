"""The generated reference pages in docs/reference/ match the code.

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
    page = ROOT / "docs" / "reference" / name
    assert page.read_text(encoding="utf-8") == generate_docs.PAGES[name](), (
        f"docs/reference/{name} is out of date; run: uv run python scripts/generate_docs.py"
    )


def test_every_guide_page_names_its_section():
    for page in (ROOT / "docs").rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        assert text.startswith("---\n") and "\nguide-section: " in text.split("\n---\n", 1)[0], page
