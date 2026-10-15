"""example(): download a ready-to-run example project from useful-views-data.

Tests read tests/data instead of GitHub (the offline_examples fixture), which
holds each example exactly as the data repository publishes it.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys

import pytest

import useful_views as uvs
from useful_views.examples import DATA_URL_ENV, EXAMPLES

from conftest import DATA, REFERENCE, generated

PUBLISHED = sorted(EXAMPLES["pwt"]["files"])


def test_manifest_matches_the_published_files():
    """The checksums in the package are those of the files in tests/data, and
    every file of the example (apart from the outputs it produces) is listed."""
    on_disk = sorted(
        p.relative_to(REFERENCE).as_posix()
        for p in REFERENCE.rglob("*")
        if p.is_file() and p.relative_to(REFERENCE).parts[0] not in ("useful-views",) and p.name not in ("datapackage.json", "cookbook.json")
    )
    assert on_disk == PUBLISHED
    for rel, digest in EXAMPLES["pwt"]["files"].items():
        assert hashlib.sha256((REFERENCE / rel).read_bytes()).hexdigest() == digest, rel


def test_example_creates_a_ready_project(tmp_path):
    root = uvs.example("pwt", tmp_path / "reference")
    assert root == (tmp_path / "reference").resolve()
    for rel in PUBLISHED:
        assert (root / rel).read_bytes() == (REFERENCE / rel).read_bytes()
    for rel in (".gitignore", "datapackage.json", "useful-views/README.md", "raw/.gitkeep"):
        assert (root / rel).is_file()
    assert [f.kind for f in uvs.check(root=root)] == ["missing-input", "never-run", "never-run"]
    result = uvs.sync(root=root)
    assert result.ran == ("pwt_panel", "gdp_per_capita")
    # Named "reference", the project reproduces the golden outputs exactly.
    assert generated(root) == (
        (REFERENCE / "datapackage.json").read_bytes(),
        (REFERENCE / "useful-views" / "README.md").read_bytes(),
    )


def test_default_folder_is_name_example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert uvs.example("pwt") == (tmp_path / "pwt-example").resolve()


def test_unknown_example_lists_the_available_ones(tmp_path):
    with pytest.raises(uvs.ExampleError, match="pwt"):
        uvs.example("nope", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_existing_non_empty_folder_is_refused(tmp_path):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "notes.txt").write_text("mine")
    with pytest.raises(uvs.ExampleError, match="not an empty folder"):
        uvs.example("pwt", tmp_path / "taken")
    assert [p.name for p in (tmp_path / "taken").iterdir()] == ["notes.txt"]


def test_empty_existing_folder_is_used(tmp_path):
    (tmp_path / "empty").mkdir()
    assert (uvs.example("pwt", tmp_path / "empty") / "README.md").is_file()


def test_example_inside_a_project_is_refused(project):
    with pytest.raises(uvs.ProjectError, match="cannot be nested"):
        uvs.example("pwt", project / "notebooks" / "pwt")
    assert not (project / "notebooks").exists()


def test_a_changed_file_is_rejected_and_nothing_is_written(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    shutil.copytree(DATA, mirror)
    with open(mirror / "examples" / "pwt" / "recipes" / "pwt_panel" / "recipe.toml", "a") as f:
        f.write("# tampered\n")
    monkeypatch.setenv(DATA_URL_ENV, mirror.as_uri())
    with pytest.raises(uvs.ExampleError, match="checksum"):
        uvs.example("pwt", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_failed_download_says_so_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_URL_ENV, (tmp_path / "nowhere").as_uri())
    with pytest.raises(uvs.ExampleError, match="Could not download"):
        uvs.example("pwt", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_example_on_the_command_line(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "useful_views", "example", "pwt", "demo"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "useful-views sync" in proc.stdout
    assert (tmp_path / "demo" / "raw" / "pwt110_bra_deu_esp_usa.csv").is_file()
    proc = subprocess.run([sys.executable, "-m", "useful_views", "example", "nope"],
                          cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 3 and "pwt" in proc.stderr
