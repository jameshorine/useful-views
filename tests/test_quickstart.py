"""docs/quickstart.md exists and its commands work as written.

The setup blocks (installing the package) are skipped: the test runs in this
environment, with its scripts on PATH. `example` downloads from tests/data
instead of GitHub (see the offline_examples fixture). Every other python
block runs in one namespace, and every sh block runs in one shell session,
each starting from an empty folder.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import useful_views as uvs

from conftest import REFERENCE

QUICKSTART = Path(__file__).resolve().parent.parent / "docs" / "quickstart.md"


def blocks(language: str) -> list[str]:
    text = QUICKSTART.read_text(encoding="utf-8")
    found = []
    for match in re.finditer(r"(<!-- quickstart-test: skip -->\n)?```(\w+)\n(.*?)```", text, re.S):
        if match.group(1) or match.group(2) != language:
            continue
        found.append(match.group(3))
    return found


def test_quickstart_exists_with_both_walkthroughs():
    assert QUICKSTART.is_file()
    assert blocks("python") and blocks("sh")


def test_quickstart_python(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    namespace: dict = {}
    for code in blocks("python"):
        exec(compile(code, str(QUICKSTART), "exec"), namespace)
    root = tmp_path / "pwt-notebook"
    for view in ("pwt-panel.parquet", "gdp-per-capita.parquet"):
        assert_frame_equal(
            pl.read_parquet(root / "useful-views" / view),
            pl.read_parquet(REFERENCE / "useful-views" / view),
            check_exact=True,
        )
    growth = pl.read_parquet(root / "useful-views" / "growth-since-2000.parquet")
    assert growth.columns == ["iso", "country", "avg_growth_pct"] and growth.height == 4
    assert uvs.check(root=root) == []


@pytest.mark.skipif(sys.platform == "win32", reason="the shell walkthrough uses POSIX commands")
def test_quickstart_shell(tmp_path):
    script = "set -e\n" + "\n".join(blocks("sh"))
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    proc = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No problems found." in proc.stdout
    assert "Ran, in order: recipes/gdp_per_capita." in proc.stdout
    root = tmp_path / "pwt-cli"
    assert (root / "useful-views" / "README.md").read_bytes() == (
        REFERENCE / "useful-views" / "README.md"
    ).read_bytes()
