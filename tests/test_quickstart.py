"""SPEC section 14: docs/quickstart.md exists and its commands work as written.

The setup block (clone and install) is skipped: the test stands in for it by
placing the repository's reference/ folder at useful-views/reference and
putting this environment's scripts on PATH. Every other python block runs in
one namespace, and every sh block runs in one shell session, each starting
from that folder.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REFERENCE

QUICKSTART = Path(__file__).resolve().parent.parent / "docs" / "quickstart.md"
SKIP = "<!-- quickstart-test: skip -->"


def blocks(language: str) -> list[str]:
    text = QUICKSTART.read_text(encoding="utf-8")
    found = []
    for match in re.finditer(r"(<!-- quickstart-test: skip -->\n)?```(\w+)\n(.*?)```", text, re.S):
        if match.group(1) or match.group(2) != language:
            continue
        found.append(match.group(3))
    return found


@pytest.fixture
def workspace(tmp_path):
    shutil.copytree(REFERENCE, tmp_path / "useful-views" / "reference")
    return tmp_path


def test_quickstart_exists_with_both_walkthroughs():
    assert QUICKSTART.is_file()
    assert blocks("python") and blocks("sh")


def test_quickstart_python(workspace, monkeypatch):
    monkeypatch.chdir(workspace)
    namespace: dict = {}
    for code in blocks("python"):
        exec(compile(code, str(QUICKSTART), "exec"), namespace)
    root = workspace / "pwt-notebook"
    assert (root / "useful-views" / "gdp-per-capita.parquet").is_file()
    assert (root / "datapackage.json").read_bytes() == (REFERENCE / "datapackage.json").read_bytes().replace(
        b'"name": "reference"', b'"name": "pwt-notebook"'
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the shell walkthrough uses POSIX commands")
def test_quickstart_shell(workspace):
    script = "set -e\n" + "\n".join(blocks("sh"))
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    proc = subprocess.run(["bash", "-c", script], cwd=workspace, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No problems found." in proc.stdout
    assert "Ran, in order: recipes/gdp_per_capita." in proc.stdout
    root = workspace / "pwt-cli"
    assert (root / "useful-views" / "README.md").read_bytes() == (REFERENCE / "useful-views" / "README.md").read_bytes()
