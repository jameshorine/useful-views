"""Build the reference project from an empty folder using only the public
functions, the raw file and the reference recipes, and compare the result
with reference/."""

from __future__ import annotations

import shutil
import subprocess
import sys

import polars as pl
from polars.testing import assert_frame_equal

import useful_views as uvs

from conftest import REFERENCE, CliDriver, generated

VIEWS = ["pwt-panel.parquet", "gdp-per-capita.parquet"]


def assert_matches_reference(root):
    for view in VIEWS:
        assert_frame_equal(
            pl.read_parquet(root / "useful-views" / view),
            pl.read_parquet(REFERENCE / "useful-views" / view),
            check_exact=True,
        )
    # The folder is named "reference", so the package name matches too and
    # the files can be compared byte for byte.
    assert (root / "datapackage.json").read_bytes() == (REFERENCE / "datapackage.json").read_bytes()
    assert (root / "useful-views" / "README.md").read_bytes() == (
        REFERENCE / "useful-views" / "README.md"
    ).read_bytes()


def test_reference_project_from_empty_folder(tmp_path):
    root = uvs.init(tmp_path / "reference")
    shutil.copy(REFERENCE / "raw" / "pwt110_bra_deu_esp_usa.csv", root / "raw")
    shutil.copytree(REFERENCE / "recipes", root / "recipes", dirs_exist_ok=True)
    uvs.run("pwt_panel", root=root)
    uvs.run("gdp_per_capita", root=root)
    assert_matches_reference(root)
    assert uvs.check(root=root) == []


def test_reference_project_via_cli(tmp_path):
    CliDriver(tmp_path).init("reference")
    root = tmp_path / "reference"
    shutil.copy(REFERENCE / "raw" / "pwt110_bra_deu_esp_usa.csv", root / "raw")
    shutil.copytree(REFERENCE / "recipes", root / "recipes", dirs_exist_ok=True)
    cli = CliDriver(root)
    assert cli.run("pwt_panel").ok
    assert cli.run("gdp_per_capita").ok
    assert_matches_reference(root)
    assert cli.check().exit_code == 0


def test_reference_project_validates_with_frictionless(reference_project):
    uvs.run("pwt_panel", root=reference_project)
    uvs.run("gdp_per_capita", root=reference_project)
    proc = subprocess.run(
        [sys.executable, "-m", "frictionless", "validate", "datapackage.json"],
        cwd=reference_project,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_second_run_changes_nothing(reference_project):
    uvs.run("pwt_panel", root=reference_project)
    uvs.run("gdp_per_capita", root=reference_project)
    first = generated(reference_project)
    uvs.run("gdp_per_capita", root=reference_project)
    uvs.run("pwt_panel", root=reference_project)
    assert generated(reference_project) == first
