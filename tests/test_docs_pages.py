"""The user guide's .qmd pages run, so a broken example fails here before it
fails the docs build.

Quarto runs these pages' {python} cells when the site is built, and shows
their real output. Here each page's cells run in order, in one namespace.
Cells marked `#| eval: false` are skipped, as Quarto skips them. Every page
starts with the same hidden setup cell. It reads the example project from
tests/data and moves into a fresh folder: here, the test's own temporary
folder. Hidden cells with asserts check what the prose says about the output.
"""

from __future__ import annotations

import os
import re
import tempfile
import warnings
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


def test_every_page_starts_with_the_same_setup_cell():
    first = {name: cells(GUIDE / name)[0] for name in PAGES}
    setup = first[PAGES[0]]
    assert "#| include: false" in setup and "def sh(" in setup
    differ = [name for name, cell in first.items() if cell != setup]
    assert not differ, f"copy the setup cell of {PAGES[0]} to the top of: {', '.join(differ)}"


@pytest.mark.parametrize("name", PAGES)
def test_page_uses_plain_python(name):
    """Quarto runs the cells with Jupyter, which would accept IPython's ! and
    % lines; this test and the reader's Python would not."""
    magic = [line for cell in cells(GUIDE / name) for line in cell.splitlines() if line.lstrip().startswith(("!", "%"))]
    assert not magic, f"{name} uses IPython-only lines: {magic}"


@pytest.mark.parametrize("name", PAGES)
def test_page_runs(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *args, **kwargs: str(tmp_path))
    monkeypatch.setattr(warnings, "formatwarning", warnings.formatwarning)  # the setup cell replaces it
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
