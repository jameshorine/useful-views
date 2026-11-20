"""The user guide's .qmd pages run, so a broken example fails here before it
fails the docs build.

Quarto runs these pages' {python} cells when the site is built, and shows
their real output. Here each page's cells run in order, in one namespace.
Cells marked `#| eval: false` are skipped, as Quarto skips them. The hidden
setup cell at the top of each page points `useful-views example` at
tests/data/examples and moves into a fresh folder: here, the test's own
temporary folder.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

GUIDE = Path(__file__).resolve().parent.parent / "user_guide"
PAGES = sorted(p.relative_to(GUIDE).as_posix() for p in GUIDE.rglob("*.qmd"))
CELL = re.compile(r"^```\{python\}[^\n]*\n(.*?)^```[ \t]*$", re.S | re.M)
OPTION = re.compile(r"^#\|\s*([\w-]+):\s*(.*?)\s*$", re.M)


def cells(page: Path) -> list[str]:
    """The code of each {python} cell that Quarto runs, in order."""
    found = []
    for body in CELL.findall(page.read_text(encoding="utf-8")):
        if dict(OPTION.findall(body)).get("eval") != "false":
            found.append(body)
    return found


def test_there_are_pages_to_run():
    assert PAGES, "no .qmd pages found in user_guide/"


@pytest.mark.parametrize("name", PAGES)
def test_page_runs(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *args, **kwargs: str(tmp_path))
    environ = dict(os.environ)
    namespace: dict = {"__name__": "__main__"}
    try:
        found = cells(GUIDE / name)
        assert found, f"{name} has no {{python}} cells; it could be a .md page"
        for i, code in enumerate(found, start=1):
            exec(compile(code, f"{name}, cell {i}", "exec"), namespace)
    finally:
        os.environ.clear()
        os.environ.update(environ)
