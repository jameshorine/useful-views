"""The examples in the public docstrings run, and print what they show.

great-docs shows a docstring's `Examples:` section as it is written, so these
tests check it. Each example is a doctest (`>>>` and the output it prints)
inside a ```python fence, which keeps great-docs from splitting the prompt
from its output. Here the fence lines are dropped, and each docstring's
examples run with doctest inside a synced copy of the pwt example, as a reader
in that folder would run them. `uvs` is already imported.
"""

from __future__ import annotations

import doctest
import inspect
import re
import shutil

import pytest

import useful_views as uvs

from conftest import REFERENCE

FLAGS = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE
FENCE = re.compile(r"^[ \t]*```.*(?:\n|\Z)", re.M)


def _examples() -> list[doctest.DocTest]:
    parser = doctest.DocTestParser()
    found = []
    for name in uvs.__all__:
        text = inspect.getdoc(getattr(uvs, name)) or ""
        test = parser.get_doctest(FENCE.sub("", text), {}, f"useful_views.{name}", None, 0)
        if test.examples:
            found.append(test)
    return found


EXAMPLES = _examples()


def test_some_docstrings_have_examples():
    assert EXAMPLES, "no public docstring has an Examples section"


@pytest.mark.parametrize("test", EXAMPLES, ids=lambda test: test.name)
def test_docstring_example(test, tmp_path, monkeypatch, offline_examples):
    root = uvs.init(tmp_path / "pwt-example")
    shutil.copy(REFERENCE / "raw" / "pwt110_bra_deu_esp_usa.csv", root / "raw")
    shutil.copytree(REFERENCE / "recipes", root / "recipes", dirs_exist_ok=True)
    shutil.copy(REFERENCE / "sources.toml", root)
    uvs.sync(root=root)
    monkeypatch.chdir(root)
    test.globs = {"uvs": uvs}
    report: list[str] = []
    result = doctest.DocTestRunner(optionflags=FLAGS).run(test, out=report.append)
    assert result.failed == 0, "".join(report)
