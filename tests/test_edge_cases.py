"""Realistic slips: odd step names, outputs written wrongly, steps that touch
what they must not, leftovers on disk, merges, and names on other systems."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

import useful_views as uvs
from useful_views.recipe import resolve_name, step_description
from useful_views.record import write_files

from conftest import add_raw_csv, make_views_step, write, write_recipe

VIEW = "useful-views/a.parquet"


def recipe_a(project, **kwargs):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=[VIEW], **kwargs)


# --- step files ---------------------------------------------------------------------


@pytest.mark.parametrize("filename, says", [
    ("02 filter.py", "has a space"),
    ("02_filter.py.txt", "which an editor added"),
    ("02_filter", "no .py extension"),
    ("02_filter.PY", "not in lowercase"),
    ("2_filter.py", "exactly two digits"),
])
def test_a_file_named_almost_like_a_step_is_warned_about(project, filename, says):
    recipe_a(project)
    write(project, f"recipes/a/{filename}", "# Filter.\n")
    with pytest.warns(uvs.UsefulViewsWarning, match="is not run") as caught:
        uvs.run("a", root=project)
    assert any(says in str(w.message) for w in caught), [str(w.message) for w in caught]


@pytest.mark.parametrize("filename", ["02_explore.IPYNB", "02_clean.Rmd", "02_model.JL"])
def test_steps_in_other_languages_are_refused_whatever_the_case(project, filename):
    recipe_a(project)
    write(project, f"recipes/a/{filename}", "x\n")
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("a", root=project)
    assert err.value.rule == 3


def test_inline_script_metadata_is_not_the_description():
    text = (
        "# /// script\n# requires-python = \">=3.11\"\n# dependencies = [\"polars\"]\n# ///\n\n"
        "# Keep the rows from 1990 on.\nimport polars\n"
    )
    assert step_description(text) == "Keep the rows from 1990 on."


def test_decoration_lines_around_the_description():
    assert step_description("# ----------\n# Clean the data.\n# ----------\nimport x\n") == "Clean the data."
    assert step_description("# ===\n# Clean.\n# ===\n# Author: someone\n") == "Clean."
    jupytext = "# ---\n# jupyter:\n#   kernelspec:\n#     name: python3\n# ---\n\n# Clean the data.\n"
    assert step_description(jupytext) == "Clean the data."


# --- recipe names -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["con", "aux", "nul", "prn", "com1", "lpt9"])
def test_names_windows_reserves_are_refused(project, name):
    with pytest.raises(uvs.RecipeRefused, match="reserved device name on Windows"):
        uvs.new_recipe(name, root=project)
    (project / "recipes" / name).mkdir()
    with pytest.raises(uvs.RecipeRefused, match="reserved device name on Windows"):
        uvs.run(name, root=project)


@pytest.mark.parametrize("given", ["./recipes/a/", ".\\recipes\\a\\", "recipes/a", "a/", "./a"])
def test_run_accepts_the_ways_a_shell_completes_a_recipe_folder(project, given):
    recipe_a(project)
    assert resolve_name(project, given) == "a"


def test_run_accepts_the_recipe_folder_as_a_full_path(project):
    recipe_a(project)
    assert resolve_name(project, str(project / "recipes" / "a")) == "a"
    assert uvs.run(str(project / "recipes" / "a"), root=project).recipe == "a"


# --- outputs written wrongly ------------------------------------------------------------


def test_an_output_written_as_a_folder_is_explained(project):
    step = ("# Write a partitioned dataset.\nimport os, pyarrow as pa, pyarrow.parquet as pq\n"
            f"os.makedirs({VIEW!r})\npq.write_table(pa.table({{'x': [1]}}), {VIEW!r} + '/part-0.parquet')\n")
    recipe_a(project, steps={"01_make.py": step})
    with pytest.raises(uvs.OutputError, match="is a folder, but a view must be a single parquet file"):
        uvs.run("a", root=project)


def test_an_output_written_with_other_capitals_is_explained(project):
    step = ("# Write the view with other capitals.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
            "pq.write_table(pa.table({'x': [1]}), 'useful-views/tmp-A.parquet')\n"
            "import os\nos.replace('useful-views/tmp-A.parquet', 'useful-views/A.parquet')\n")
    recipe_a(project, steps={"01_make.py": step})
    with pytest.raises(uvs.OutputError) as err:
        uvs.run("a", root=project)
    assert "useful-views/A.parquet" in str(err.value) and "capital letters" in str(err.value)


def test_a_view_path_that_is_a_folder_is_missing_for_check_and_sync(project):
    recipe_a(project)
    uvs.run("a", root=project)
    (project / VIEW).unlink()
    (project / VIEW).mkdir()  # left by a failed partitioned write
    assert [f.kind for f in uvs.check(root=project)] == ["refused", "missing-view"]
    with pytest.raises(uvs.RecipeRefused, match="is a folder on disk") as err:
        uvs.sync(root=project)
    assert err.value.rule == 7


# --- steps that change what they must not ----------------------------------------------


def test_a_step_that_writes_to_raw_is_warned_about(project):
    step = make_views_step([VIEW]) + "open('raw/data.csv', 'a').write('3,4.5\\n')\n"
    recipe_a(project, steps={"01_make.py": step})
    with pytest.warns(uvs.UsefulViewsWarning, match="changed raw/data.csv while it ran"):
        uvs.run("a", root=project)


def test_a_step_that_overwrites_another_recipes_view_is_warned_about(project):
    raw = add_raw_csv(project)
    write_recipe(project, "b", inputs=[raw], outputs=["useful-views/b.parquet"])
    uvs.run("b", root=project)
    step = make_views_step([VIEW, "useful-views/b.parquet"])
    write_recipe(project, "a", inputs=[raw], outputs=[VIEW], steps={"01_make.py": step})
    with pytest.warns(uvs.UsefulViewsWarning, match="changed useful-views/b.parquet, which it does not declare"):
        uvs.run("a", root=project)


# --- leftovers ----------------------------------------------------------------------


def test_a_recipe_folder_left_with_only_hidden_files_counts_as_deleted(project):
    recipe_a(project)
    uvs.run("a", root=project)
    for name in ("recipe.toml", "01_make.py"):
        (project / "recipes" / "a" / name).unlink()
    (project / "recipes" / "a" / ".DS_Store").write_bytes(b"\0")
    (project / "recipes" / "a" / "__pycache__").mkdir()
    assert [f.kind for f in uvs.check(root=project)] == ["missing-recipe-folder"]
    result = uvs.sync(root=project)
    assert result.removed == ("a",) and VIEW in result.deleted and "work/a/" in result.deleted
    assert uvs.check(root=project) == []


def test_a_work_folder_of_no_recipe_is_warned_about(project):
    recipe_a(project)
    (project / "work" / "old_name").mkdir(parents=True)
    with pytest.warns(uvs.UsefulViewsWarning, match="work/old_name/ belongs to no recipe"):
        uvs.check(root=project)


def test_a_hand_edited_catalog_is_reported_and_sync_rewrites_it(project):
    recipe_a(project)
    uvs.run("a", root=project)
    with open(project / "useful-views" / "README.md", "a", encoding="utf-8") as f:
        f.write("my note\n")
    (finding,) = uvs.check(root=project)
    assert finding.kind == "stale-catalog" and finding.path == "useful-views/README.md"
    with pytest.warns(uvs.UsefulViewsWarning, match="sync wrote it again"):
        uvs.sync(root=project)
    assert uvs.check(root=project) == []


def test_merge_conflict_markers_in_datapackage_json_are_explained(project):
    recipe_a(project)
    uvs.run("a", root=project)
    text = (project / "datapackage.json").read_text(encoding="utf-8")
    (project / "datapackage.json").write_text("<<<<<<< HEAD\n" + text + "=======\n" + text + ">>>>>>> other\n",
                                              encoding="utf-8")
    with pytest.raises(uvs.ProjectError, match="git merge conflict markers") as err:
        uvs.check(root=project)
    assert "useful-views sync --all" in str(err.value)


def test_a_write_error_names_the_file_not_the_temporary_one(tmp_path):
    (tmp_path / "out.json").mkdir()
    with pytest.raises(uvs.ProjectError) as err:
        write_files({tmp_path / "out.json": "{}\n"})
    assert f"Could not write {tmp_path / 'out.json'}" in str(err.value) and ".tmp" not in str(err.value)


# --- the rest -----------------------------------------------------------------------


def test_steps_run_in_utf8_mode(project):
    recipe_a(project, steps={"01_make.py": make_views_step([VIEW]) +
                             "import sys\nopen('work/a/mode.txt', 'w').write(str(sys.flags.utf8_mode))\n"})
    uvs.run("a", root=project)
    assert (project / "work" / "a" / "mode.txt").read_text() == "1"


@pytest.mark.parametrize("content", [b"PAR1", b"PAR1PAR1", b"P"])
def test_a_raw_parquet_too_short_to_be_one_is_refused_as_invalid(project, content):
    (project / "raw").mkdir(exist_ok=True)
    (project / "raw" / "tiny.parquet").write_bytes(content)
    write_recipe(project, "a", inputs=["raw/tiny.parquet"], outputs=[VIEW])
    with pytest.raises(uvs.RecipeRefused, match="is not a valid parquet file") as err:
        uvs.run("a", root=project)
    assert "permissions" not in str(err.value)


def test_a_name_clash_with_an_old_record_says_to_run_that_recipe_first(project):
    write(project, "raw/data.csv", "id,value\n1,2\n")
    write(project, "raw/other.csv", "id,value\n1,2\n")
    write_recipe(project, "b", inputs=["raw/data.csv"], outputs=["useful-views/b.parquet"])
    uvs.run("b", root=project)
    write_recipe(project, "b", inputs=["raw/other.csv"], outputs=["useful-views/b.parquet"])  # no longer reads data.csv
    write_recipe(project, "a", inputs=["raw/other.csv"], outputs=["useful-views/data.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("a", root=project)
    assert err.value.rule == 8 and "run useful-views run b first" in str(err.value)


def test_check_json_after_a_merge_left_a_cookbook_conflict(project, tmp_path):
    recipe_a(project)
    uvs.sync(root=project)
    uvs.cookbook(root=project)
    text = (project / "cookbook.json").read_text(encoding="utf-8")
    (project / "cookbook.json").write_text("<<<<<<< HEAD\n" + text + ">>>>>>> b\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "useful_views", "check", "--json"], cwd=project,
                         capture_output=True, text=True)
    assert [f["kind"] for f in json.loads(out.stdout)["findings"]] == ["stale-cookbook"]
    assert uvs.cookbook(root=project).written


def test_an_input_written_with_other_capitals_says_so(project):
    write(project, "raw/Data.csv", "id,value\n1,2\n")
    write_recipe(project, "a", inputs=["raw/data.csv"], outputs=[VIEW])
    (finding,) = [f for f in uvs.check(root=project) if f.kind == "missing-input"]
    if (project / "raw" / "data.csv").exists():  # a case-insensitive file system (macOS, Windows)
        assert "differs in capital letters" in finding.message
        with pytest.raises(uvs.RecipeRefused, match="differs only in capital letters"):
            uvs.run("a", root=project)
    else:
        assert "is missing from disk" in finding.message


# --- review of the fixes ----------------------------------------------------------------

from useful_views.paths import resource_name, slug  # noqa: E402


def test_dropping_one_of_several_outputs_is_reported_and_blocks_cookbook_check(project):
    raw = add_raw_csv(project)
    write_recipe(project, "a", inputs=[raw], outputs=[VIEW, "useful-views/extra.parquet"])
    uvs.sync(root=project)
    uvs.cookbook(root=project)
    write_recipe(project, "a", inputs=[raw], outputs=[VIEW])
    (finding,) = uvs.check(root=project)
    assert (finding.kind, finding.reason) == ("out-of-date", "outputs-dropped")
    assert "useful-views/extra.parquet" in finding.message
    assert not uvs.cookbook(root=project, check=True).up_to_date
    with pytest.warns(uvs.UsefulViewsWarning, match="cookbook.json no longer matches"):
        uvs.sync(root=project)
    assert [f.kind for f in uvs.check(root=project)] == ["stale-cookbook"]


def test_names_are_the_same_however_accents_are_stored():
    assert resource_name("raw/Café.csv") == resource_name("raw/Café.csv") == "cafe"
    assert slug("Données Été") == "donnees-ete"
    assert slug("İstanbul") == "istanbul"
    assert slug("क्ष") != slug("कष")  # a Devanagari conjunct is not its letters


def test_check_points_to_sync_when_a_missing_input_is_a_view_sync_makes(project):
    raw = add_raw_csv(project)
    write_recipe(project, "a", inputs=[raw], outputs=[VIEW])
    write_recipe(project, "b", inputs=[VIEW], outputs=["useful-views/b.parquet"])
    out = subprocess.run([sys.executable, "-m", "useful_views", "check"], cwd=project, capture_output=True, text=True)
    assert "missing-input: useful-views/a.parquet" in out.stdout
    assert out.stdout.rstrip().endswith("run: useful-views sync")


def test_a_rule_8_clash_with_a_deleted_recipes_record_says_to_sync(project):
    write(project, "raw/data.csv", "id,value\n1,2\n")
    write_recipe(project, "b", inputs=["raw/data.csv"], outputs=["useful-views/b.parquet"])
    uvs.run("b", root=project)
    shutil.rmtree(project / "recipes" / "b")
    write(project, "raw/other.csv", "id,value\n1,2\n")
    write_recipe(project, "a", inputs=["raw/other.csv"], outputs=["useful-views/data.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("a", root=project)
    assert "folder no longer exists; run useful-views sync" in str(err.value)
    assert "useful-views run b" not in str(err.value)


def test_merge_advice_names_only_files_that_exist(project):
    text = (project / "datapackage.json").read_text(encoding="utf-8")
    (project / "datapackage.json").write_text("<<<<<<< HEAD\n" + text + ">>>>>>> b\n", encoding="utf-8")
    with pytest.raises(uvs.ProjectError) as err:
        uvs.check(root=project)
    assert "git checkout --ours datapackage.json useful-views/README.md)" in str(err.value)
    assert "cookbook" not in str(err.value)


def test_a_jupytext_header_with_other_keys_first():
    text = ("# ---\n# title: Panel\n# jupyter:\n#   kernelspec:\n#     name: python3\n# ---\n\n"
            "# Build the panel.\n")
    assert step_description(text) == "Build the panel."
