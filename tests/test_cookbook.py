"""cookbook(): cookbook.json, the ledger of the recipes a project uses."""

from __future__ import annotations

import json
import os
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
    assert book["recipes"] == records  # the same records, in the same run order


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
    with pytest.warns(uvs.UsefulViewsWarning, match="cookbook.json no longer matches"):
        result = uvs.sync(root=root)
    assert any("useful-views cookbook" in w for w in result.warnings)
    assert [f.kind for f in uvs.check(root=root)] == ["stale-cookbook"]
    assert not uvs.cookbook(root=root, check=True).up_to_date
    assert uvs.cookbook(root=root).written
    assert uvs.check(root=root) == []


def test_a_removed_recipe_makes_the_cookbook_stale(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    shutil.rmtree(root / "recipes" / "gdp_per_capita")
    with pytest.warns(uvs.UsefulViewsWarning, match="cookbook.json no longer matches"):
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


# --- cookbook --check on a clone without data (CI) -------------------------------


def committed_copy(root, dest):
    """What a fresh clone or CI checkout has: everything but the gitignored
    raw data, work files and views."""
    def ignore(folder, names):
        rel = os.path.relpath(folder, root).replace(os.sep, "/")
        if rel == "raw":
            return [n for n in names if n != ".gitkeep"]
        if rel == "useful-views":
            return [n for n in names if n.endswith(".parquet")]
        return ["work"] if rel == "." else []

    shutil.copytree(root, dest, ignore=ignore)
    return dest


def test_check_mode_works_on_a_clone_without_data(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    clone = committed_copy(root, tmp_path / "ci-checkout")
    kinds = {f.kind for f in uvs.check(root=clone)}
    assert kinds == {"missing-input", "missing-view"}  # check itself sees the missing data
    result = uvs.cookbook(root=clone, check=True)
    assert result.up_to_date and result.findings == ()
    out = cli("cookbook", "--check", "--json", cwd=clone)
    assert out.returncode == 0 and json.loads(out.stdout)["findings"] == []


def test_check_mode_on_a_clone_catches_a_recipe_edited_without_sync(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    clone = committed_copy(root, tmp_path / "ci-checkout")
    toml = clone / "recipes" / "gdp_per_capita" / "recipe.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("GDP per capita,", "GDP per head,"), encoding="utf-8")
    result = uvs.cookbook(root=clone, check=True)
    assert not result.up_to_date
    assert [(f.kind, f.recipe, f.reason) for f in result.findings] == [("out-of-date", "gdp_per_capita", "changed")]
    out = cli("cookbook", "--check", cwd=clone)
    assert out.returncode == 1 and "out-of-date: recipes/gdp_per_capita" in out.stdout
    assert "Run useful-views sync, then useful-views cookbook" in out.stdout


def test_check_mode_on_a_clone_catches_a_recipe_never_run(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    clone = committed_copy(root, tmp_path / "ci-checkout")
    write_recipe(clone, "extra", inputs=["raw/pwt110_bra_deu_esp_usa.csv"], outputs=["useful-views/extra.parquet"])
    result = uvs.cookbook(root=clone, check=True)
    assert not result.up_to_date and [f.kind for f in result.findings] == ["never-run"]
    data = json.loads(cli("cookbook", "--check", "--json", cwd=clone).stdout)
    assert data["ok"] is False and data["findings"][0]["recipe"] == "extra"


def test_a_cookbook_that_is_not_utf8_is_stale_and_rewritten(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    (root / "cookbook.json").write_bytes((root / "cookbook.json").read_text(encoding="utf-8").encode("utf-16"))
    (finding,) = uvs.check(root=root)
    assert finding.kind == "stale-cookbook"
    assert uvs.cookbook(root=root).written
    assert uvs.check(root=root) == []


def test_a_cookbook_with_crlf_line_endings_is_not_stale(tmp_path):
    root = synced_reference(tmp_path)
    uvs.cookbook(root=root)
    path = root / "cookbook.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # git autocrlf on Windows
    assert uvs.check(root=root) == []
    assert uvs.cookbook(root=root, check=True).up_to_date


def test_a_cookbook_that_is_a_folder_is_explained(tmp_path):
    root = synced_reference(tmp_path)
    (root / "cookbook.json").mkdir()
    (finding,) = uvs.check(root=root)
    assert finding.kind == "stale-cookbook" and "is a folder" in finding.message
    with pytest.raises(uvs.ProjectError, match="cookbook.json is a folder"):
        uvs.cookbook(root=root)


def test_the_refusal_points_to_sync_only_when_sync_can_fix_it(project):
    write_recipe(project, "a", inputs=["raw/missing.csv"], outputs=["useful-views/a.parquet"])
    with pytest.raises(uvs.CookbookError) as err:
        uvs.cookbook(root=project)
    assert [f.kind for f in err.value.findings] == ["missing-input", "never-run"]
    assert "Fix what is listed" in str(err.value)
    write(project, "raw/missing.csv", "id,value\n1,2\n")
    with pytest.raises(uvs.CookbookError) as err:
        uvs.cookbook(root=project)
    assert str(err.value).endswith("Run useful-views sync, then try again.")
