"""cookbook(): cookbook.json, the ledger of the recipes a project uses."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

import useful_views as uvs

from conftest import REFERENCE, add_raw_csv, snapshot, write, write_recipe


def cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "useful_views", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8")


def synced_reference(tmp_path):
    root = uvs.example("pwt", tmp_path / "reference")
    uvs.sync(root=root)
    return root


def test_reference_cookbook_matches_the_golden_file(tmp_path):
    root = synced_reference(tmp_path)
    result = uvs.cookbook(root=root)
    assert result.written and result.up_to_date
    assert result.recipes == ("pwt_panel", "gdp_per_capita")  # upstream first
    assert (root / "cookbook.json").read_bytes() == (REFERENCE / "cookbook.json").read_bytes()


def test_cookbook_contents(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    book = json.loads((root / "cookbook.json").read_text(encoding="utf-8"))
    assert book["version"] == 1 and book["generator"] == "useful-views" and book["project"] == "reference"
    assert book["raw_inputs"] == ["raw/pwt110_bra_deu_esp_usa.csv"]
    records = json.loads((root / "datapackage.json").read_text(encoding="utf-8"))["recipes"]
    assert sorted(book["recipes"], key=lambda r: r["name"]) == records


def test_writing_twice_changes_nothing(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    stamp = (root / "cookbook.json").stat().st_mtime_ns
    again = uvs.cookbook(root=root)
    assert not again.written and again.up_to_date
    assert (root / "cookbook.json").stat().st_mtime_ns == stamp


def test_refused_when_the_project_is_not_in_sync(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    before = snapshot(project)
    with pytest.raises(uvs.CookbookError) as err:
        uvs.cookbook(root=project)
    assert [f.kind for f in err.value.findings] == ["never-run"]
    assert "useful-views sync" in str(err.value)
    assert snapshot(project) == before


def test_a_stray_file_does_not_block_the_cookbook(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    uvs.sync(root=project)
    write(project, "useful-views/notes.txt", "mine")
    assert uvs.cookbook(root=project).written


def test_check_mode_reports_and_never_writes(tmp_path):
    root = synced_reference(tmp_path)
    missing = uvs.cookbook(root=root, check=True)
    assert not missing.up_to_date and not missing.written and not (root / "cookbook.json").exists()
    uvs.cookbook(root=root)
    assert uvs.cookbook(root=root, check=True).up_to_date


def test_a_changed_recipe_makes_the_cookbook_stale(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    toml = root / "recipes" / "gdp_per_capita" / "recipe.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("GDP per capita,", "GDP per head,"), encoding="utf-8")
    uvs.sync(root=root)
    assert [f.kind for f in uvs.check(root=root)] == ["stale-cookbook"]
    assert not uvs.cookbook(root=root, check=True).up_to_date
    assert uvs.cookbook(root=root).written
    assert uvs.check(root=root) == []


def test_a_removed_recipe_makes_the_cookbook_stale(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    shutil.rmtree(root / "recipes" / "gdp_per_capita")
    uvs.sync(root=root)
    assert [f.kind for f in uvs.check(root=root)] == ["stale-cookbook"]
    assert uvs.cookbook(root=root).recipes == ("pwt_panel",)


def test_cookbook_on_the_command_line(tmp_path):
    root = synced_reference(tmp_path)
    out = cli("cookbook", "--check", cwd=root)
    assert out.returncode == 1 and "useful-views cookbook" in out.stdout
    out = cli("cookbook", cwd=root)
    assert out.returncode == 0 and "Wrote cookbook.json: 2 recipe(s)" in out.stdout
    out = cli("cookbook", "--check", "--json", cwd=root)
    data = json.loads(out.stdout)
    assert out.returncode == 0 and data["ok"] and data["recipes"] == ["pwt_panel", "gdp_per_capita"]


def test_cookbook_refusal_as_json(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    out = cli("cookbook", "--json", cwd=project)
    error = json.loads(out.stdout)["error"]
    assert out.returncode == 3 and error["type"] == "CookbookError"
    assert [f["kind"] for f in error["findings"]] == ["never-run"]
