"""The README's Usage section shows a real run.

The README is static Markdown, since it is also the PyPI page, so this test
runs it. Each `$ ` line of its console block runs with sh() from the docs'
setup cell, in a fresh folder, against the offline example, and the rebuilt
transcript must equal the README's. Then its Python block runs as a doctest
in the example project.
"""

from __future__ import annotations

import contextlib
import doctest
import io
import re
import sys
from pathlib import Path

import pytest

from test_docs_pages import GUIDE, cells, run_cells

README = Path(__file__).resolve().parent.parent / "README.md"


def block(language: str) -> str:
    found = re.findall(rf"^```{language}\n(.*?)^```", README.read_text(encoding="utf-8"), re.S | re.M)
    assert len(found) == 1, f"README.md should have one ```{language} block"
    return found[0]


@pytest.mark.skipif(sys.platform == "win32", reason="the transcript shows POSIX paths")
def test_readme_usage_is_a_real_run(tmp_path, monkeypatch):
    setup = cells(GUIDE / "quickstart.qmd")[0]
    shown = block("console")
    replayed = []

    def replay(sh):
        for line in shown.splitlines():
            if line.startswith("$ "):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    sh(line[2:])
                replayed.append(line + "\n" + out.getvalue())

    namespace = run_cells([setup], "setup cell", tmp_path, monkeypatch)
    replay(namespace["sh"])
    assert "".join(replayed) == shown

    test = doctest.DocTestParser().get_doctest(block("python"), {}, "README.md", str(README), 0)
    report: list[str] = []
    result = doctest.DocTestRunner(optionflags=doctest.NORMALIZE_WHITESPACE).run(test, out=report.append)
    assert result.failed == 0, "".join(report)
