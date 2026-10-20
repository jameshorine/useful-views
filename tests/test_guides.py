"""The guides that show code run as written, and print what they say.

Each python block runs in one namespace, in an empty folder; where a text
block follows it, that is the output the block must print.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

GUIDES = Path(__file__).resolve().parent.parent / "user_guide" / "guides"


def blocks(page: Path) -> list[tuple[str, str]]:
    return re.findall(r"```(\w+)\n(.*?)```", page.read_text(encoding="utf-8"), re.S)


@pytest.mark.parametrize("name", ["fingerprints.md"])
def test_guide_runs_and_prints_what_it_shows(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    found = blocks(GUIDES / name)
    namespace: dict = {}
    checked = 0
    for i, (language, code) in enumerate(found):
        if language != "python":
            continue
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            exec(compile(code, f"{name}, block {i}", "exec"), namespace)
        if i + 1 < len(found) and found[i + 1][0] == "text":
            assert printed.getvalue() == found[i + 1][1], f"{name}, block {i} prints something else"
            checked += 1
    assert checked >= 5
