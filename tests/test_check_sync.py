"""check() and sync().

check() reports, as a list of findings each with `kind`, `path` and
`message`, in a fixed order:

- unreadable-recipe: a recipe folder whose recipe.toml is missing or unreadable;
- missing-input: an input declared by a current recipe.toml that is not on disk;
- never-run: a recipe folder that has never run successfully;
- missing-recipe-folder: a recorded recipe whose folder no longer exists;
- missing-view: a recorded view whose file is missing;
- stray-file: a file (or folder) in useful-views/, other than README.md, that
  no recipe.toml declares and no record lists.

sync() makes the views and their records match the recipe folders, like
`uv sync`: it removes the records and view files of deleted recipes and of
outputs a recipe no longer declares, then runs, upstream first, every recipe
that never ran, no longer matches its record or is missing a view, plus
everything downstream of those.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import useful_views as uvs

from conftest import (
    CliDriver,
    ApiDriver,
    add_raw_csv,
    generated,
    make_views_step,
    snapshot,
    write,
    write_recipe,
)

KIND_ORDER = [
    "unreadable-recipe",
    "refused",
    "missing-input",
    "never-run",
    "out-of-date",
    "missing-recipe-folder",
    "missing-view",
    "stray-file",
]

FAILING_STEP = "# Fail on purpose.\nimport sys\nsys.exit('boom')\n"


# --- helpers ----------------------------------------------------------------


def load(root: Path) -> dict:
    return json.loads((root / "datapackage.json").read_text(encoding="utf-8"))


def resource_paths(root: Path) -> list[str]:
    return [r["path"] for r in load(root)["resources"]]


def record_names(root: Path) -> list[str]:
    return [r["name"] for r in load(root)["recipes"]]


def findings(root: Path):
    return uvs.check(root=root)


def one(root: Path, kind: str):
    found = [f for f in findings(root) if f.kind == kind]
    assert len(found) == 1, found
    return found[0]


def recorded_project(root: Path, name: str = "alpha", view: str = "useful-views/alpha.parquet",
                     raw: str = "raw/alpha_src.csv") -> None:
    """Add one recipe reading one raw CSV and run it."""
    add_raw_csv(root, raw)
    write_recipe(root, name, inputs=[raw], outputs=[view])
    uvs.run(name, root=root)


def assert_clean(driver, project):
    out = driver.check()
    assert out.ok
    assert out.exit_code == 0
    assert out.kinds == []
    if isinstance(driver, CliDriver):
        assert "No problems found." in out.stdout
    else:
        assert out.value == []


# --- check: a clean project -------------------------------------------------


def test_fresh_project_has_no_findings(driver, project):
    assert_clean(driver, project)


def test_recorded_project_has_no_findings(driver, project):
    recorded_project(project)
    assert_clean(driver, project)


def test_check_returns_findings_with_kind_path_message(project):
    write(project, "useful-views/extra.parquet", "x")
    (finding,) = uvs.check(root=project)
    assert isinstance(finding, uvs.Finding)
    assert finding.kind == "stray-file"
    assert finding.path == "useful-views/extra.parquet"
    assert isinstance(finding.message, str) and finding.message


# --- check: unreadable-recipe ----------------------------------------------


@pytest.mark.filterwarnings("ignore:.*has no recipe.toml")
def test_missing_recipe_toml_is_reported(driver, project):
    write(project, "recipes/no_toml/01_step.py", "# A step without a recipe.\nprint('hi')\n")
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["unreadable-recipe"]
    f = one(project, "unreadable-recipe")
    assert "recipes/no_toml" in f.path
    assert "recipe.toml" in f.message


def test_invalid_toml_is_reported(driver, project):
    write(project, "recipes/bad_toml/recipe.toml", 'description = "unterminated\ninputs = [\n')
    write(project, "recipes/bad_toml/01_step.py", "# A step.\nprint('hi')\n")
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["unreadable-recipe"]
    f = one(project, "unreadable-recipe")
    assert "recipes/bad_toml" in f.path
    assert "recipes/bad_toml" in f.message


def test_non_utf8_toml_is_reported(driver, project):
    path = project / "recipes" / "latin" / "recipe.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes('description = "Caf\xe9 data."\ninputs = ["raw/a.csv"]\noutputs = ["useful-views/a.parquet"]\n'
                     .encode("latin-1"))
    out = driver.check()
    assert "unreadable-recipe" in out.kinds
    assert out.exit_code == 1


def test_unreadable_recipe_is_not_also_never_run(project):
    write(project, "recipes/bad_toml/recipe.toml", "this is = = not toml\n")
    assert [f.kind for f in findings(project)] == ["unreadable-recipe"]


def test_untouched_template_is_reported_as_refused(driver, project):
    """A fresh new_recipe template is readable TOML, but run and sync would
    refuse it under rule 1, so check reports that rather than "never run"."""
    assert driver.new_recipe("fresh").ok
    out = driver.check()
    assert out.kinds == ["refused"]
    if isinstance(driver, ApiDriver):
        assert out.value[0].rule == 1 and out.value[0].recipe == "fresh"


def test_hidden_and_clutter_folders_in_recipes_are_ignored(driver, project):
    (project / "recipes" / ".ipynb_checkpoints").mkdir()
    (project / "recipes" / "__pycache__").mkdir()
    write(project, "recipes/.hidden/notes.txt", "x")
    write(project, "recipes/notes.txt", "a plain file in recipes/ is ignored")
    write(project, "recipes/.DS_Store", "x")
    assert_clean(driver, project)


# --- check: missing-input ---------------------------------------------------


def test_missing_raw_input_is_reported(driver, project):
    recorded_project(project)
    (project / "raw" / "alpha_src.csv").unlink()
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["missing-input"]
    f = one(project, "missing-input")
    assert f.path == "raw/alpha_src.csv"
    assert "raw/alpha_src.csv" in f.message
    assert "recipes/alpha" in f.message


def test_missing_input_comes_from_current_recipe_toml(driver, project):
    """Declared inputs come from the current recipe.toml, not the record."""
    recorded_project(project)
    add_raw_csv(project, "raw/beta_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv", "raw/new_input.csv"],
                 outputs=["useful-views/alpha.parquet"])
    out = driver.check()
    # The edited recipe.toml no longer matches the record, so it is also out of date.
    assert out.kinds == ["missing-input", "out-of-date"]
    assert one(project, "missing-input").path == "raw/new_input.csv"


def test_missing_view_input_of_never_run_recipe(driver, project):
    add_raw_csv(project, "raw/up_src.csv")
    write_recipe(project, "up", inputs=["raw/up_src.csv"], outputs=["useful-views/up.parquet"])
    write_recipe(project, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])
    out = driver.check()
    assert out.kinds == ["missing-input", "never-run", "never-run"]
    assert one(project, "missing-input").path == "useful-views/up.parquet"


def test_input_missing_with_wrong_case_on_disk(project):
    """Inputs must match the letter case on disk."""
    add_raw_csv(project, "raw/Data.csv")
    write_recipe(project, "alpha", inputs=["raw/data.csv"], outputs=["useful-views/alpha.parquet"])
    kinds = [f.kind for f in findings(project)]
    assert "missing-input" in kinds


# --- check: never-run -------------------------------------------------------


def test_never_run_recipe_is_reported(driver, project):
    add_raw_csv(project, "raw/alpha_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/alpha.parquet"])
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["never-run"]
    f = one(project, "never-run")
    assert "recipes/alpha" in f.path
    assert "alpha" in f.message


def test_recipe_whose_only_run_failed_is_never_run(driver, project):
    add_raw_csv(project, "raw/alpha_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/alpha.parquet"],
                 steps={"01_fail.py": FAILING_STEP})
    assert not driver.run("alpha").ok
    assert driver.check().kinds == ["never-run"]


def test_declared_but_not_run_view_on_disk_is_not_stray(driver, project):
    """A view declared by a current recipe.toml is not stray, even before the
    recipe has run."""
    add_raw_csv(project, "raw/alpha_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/alpha.parquet"])
    write(project, "useful-views/alpha.parquet", "placeholder")
    assert driver.check().kinds == ["never-run"]


# --- check: missing-recipe-folder ------------------------------------------


def test_deleted_recipe_folder_is_reported(driver, project):
    recorded_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    out = driver.check()
    assert out.exit_code == 1
    # Its view is still listed by the record, so it is not stray; its raw
    # input is no longer declared by any recipe.toml, so it is not missing.
    assert out.kinds == ["missing-recipe-folder"]
    f = one(project, "missing-recipe-folder")
    assert "recipes/alpha" in f.path
    assert "sync" in f.message


@pytest.mark.filterwarnings("ignore:.*has no recipe.toml")
def test_recorded_folder_with_toml_removed_is_unreadable_not_missing(project):
    """The folder still exists, so it is not a missing recipe folder."""
    recorded_project(project)
    (project / "recipes" / "alpha" / "recipe.toml").unlink()
    kinds = [f.kind for f in findings(project)]
    assert "missing-recipe-folder" not in kinds
    assert "unreadable-recipe" in kinds


# --- check: missing-view ----------------------------------------------------


def test_deleted_view_is_reported(driver, project):
    recorded_project(project)
    (project / "useful-views" / "alpha.parquet").unlink()
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["missing-view"]
    f = one(project, "missing-view")
    assert f.path == "useful-views/alpha.parquet"
    assert "useful-views/alpha.parquet" in f.message
    assert "alpha" in f.message


def test_failed_run_leaves_views_missing_and_check_reports_them(driver, project):
    """After a failed run the old views are gone while
    datapackage.json still describes the last successful run. Intended."""
    recorded_project(project)
    before = generated(project)
    write(project, "recipes/alpha/01_make.py", FAILING_STEP)
    out = driver.run("alpha")
    assert not out.ok
    assert generated(project) == before
    assert not (project / "useful-views" / "alpha.parquet").exists()
    out = driver.check()
    assert out.exit_code == 1
    # The step changed since the last successful run, and its view is gone.
    assert out.kinds == ["out-of-date", "missing-view"]
    assert one(project, "missing-view").path == "useful-views/alpha.parquet"


def test_each_missing_view_of_a_multi_output_recipe_is_reported(project):
    add_raw_csv(project, "raw/alpha_src.csv")
    outs = ["useful-views/one.parquet", "useful-views/two.parquet"]
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=outs)
    uvs.run("alpha", root=project)
    for o in outs:
        (project / o).unlink()
    found = [f for f in findings(project) if f.kind == "missing-view"]
    assert [f.path for f in found] == outs


def test_unrecorded_declared_view_missing_is_not_missing_view(project):
    """Only recorded views can be missing views; a not-yet-run one is never-run."""
    add_raw_csv(project, "raw/alpha_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/alpha.parquet"])
    assert [f.kind for f in findings(project)] == ["never-run"]


# --- check: stray-file ------------------------------------------------------


def test_stray_file_is_reported(driver, project):
    write(project, "useful-views/extra.parquet", "x")
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == ["stray-file"]
    f = one(project, "stray-file")
    assert f.path == "useful-views/extra.parquet"
    assert "useful-views/extra.parquet" in f.message


def test_stray_non_view_file_is_reported(driver, project):
    write(project, "useful-views/notes.txt", "hand-written notes")
    assert driver.check().kinds == ["stray-file"]
    assert one(project, "stray-file").path == "useful-views/notes.txt"


def test_stray_subdirectory_is_reported(driver, project):
    (project / "useful-views" / "archive").mkdir()
    write(project, "useful-views/archive/old.parquet", "x")
    out = driver.check()
    assert out.kinds == ["stray-file"]
    f = one(project, "stray-file")
    assert f.path.rstrip("/") == "useful-views/archive"


def test_empty_stray_subdirectory_is_reported(project):
    (project / "useful-views" / "empty").mkdir()
    assert [f.kind for f in findings(project)] == ["stray-file"]


def test_catalog_and_hidden_files_are_not_stray(driver, project):
    write(project, "useful-views/.DS_Store", "x")
    write(project, "useful-views/.gitkeep", "")
    (project / "useful-views" / ".ipynb_checkpoints").mkdir()
    write(project, "useful-views/Thumbs.db", "x")
    write(project, "useful-views/desktop.ini", "x")
    (project / "useful-views" / "__pycache__").mkdir()
    assert (project / "useful-views" / "README.md").is_file()
    assert_clean(driver, project)


def test_view_listed_by_record_but_no_longer_declared_is_not_stray(driver, project):
    """A file is stray only if no recipe.toml declares it and no record lists it."""
    recorded_project(project)
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/renamed.parquet"])
    out = driver.check()
    assert "stray-file" not in out.kinds
    assert "missing-view" not in out.kinds


def test_view_of_deleted_but_recorded_recipe_is_not_stray(project):
    recorded_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    assert "stray-file" not in [f.kind for f in findings(project)]


def test_stray_file_with_case_differing_from_declared_view(project):
    """Rules compare exact strings; a file whose case differs is not the declared view."""
    add_raw_csv(project, "raw/alpha_src.csv")
    write_recipe(project, "alpha", inputs=["raw/alpha_src.csv"], outputs=["useful-views/alpha.parquet"])
    write(project, "useful-views/Alpha.parquet", "x")
    stray = [f for f in findings(project) if f.kind == "stray-file"]
    assert [f.path for f in stray] == ["useful-views/Alpha.parquet"]


# --- check: order, exit codes, no side effects ------------------------------


def build_every_kind(root: Path) -> None:
    """A project with exactly one finding of each kind."""
    # missing-recipe-folder: recorded, then its folder deleted.
    recorded_project(root, "gone", "useful-views/gone.parquet", "raw/gone_src.csv")
    # missing-view: recorded, then its view deleted.
    recorded_project(root, "lost", "useful-views/lost.parquet", "raw/lost_src.csv")
    shutil.rmtree(root / "recipes" / "gone")
    (root / "useful-views" / "lost.parquet").unlink()
    # missing-input and never-run.
    write_recipe(root, "waiting", inputs=["raw/absent.csv"], outputs=["useful-views/waiting.parquet"])
    # stray-file.
    write(root, "useful-views/extra.parquet", "x")
    # refused: an output outside useful-views/ (rule 7).
    write_recipe(root, "wrong", inputs=["raw/lost_src.csv"], outputs=["work/wrong.parquet"])
    # out-of-date: recorded, then its description edited.
    recorded_project(root, "edited", "useful-views/edited.parquet", "raw/edited_src.csv")
    write(root, "recipes/edited/recipe.toml",
          'description = "Edited since its last run."\ninputs = ["raw/edited_src.csv"]\n'
          'outputs = ["useful-views/edited.parquet"]\n')
    # unreadable-recipe (last, so the runs above do not warn about it).
    write(root, "recipes/broken/recipe.toml", "not = = toml\n")


def test_every_kind_in_fixed_order(driver, project):
    build_every_kind(project)
    out = driver.check()
    assert out.exit_code == 1
    assert out.kinds == KIND_ORDER


def test_order_does_not_depend_on_creation_order(project):
    for name in ["zz.parquet", "mm.parquet", "aa.parquet"]:
        write(project, f"useful-views/{name}", "x")
    for name in ["zeta", "alpha", "mid"]:
        write_recipe(project, name, inputs=[f"raw/{name}.csv"], outputs=[f"useful-views/{name}-v.parquet"])
    first = [(f.kind, f.path) for f in findings(project)]
    assert first == [(f.kind, f.path) for f in findings(project)]
    kinds = [k for k, _ in first]
    assert kinds == sorted(kinds, key=KIND_ORDER.index)
    for kind in set(kinds):
        paths = [p for k, p in first if k == kind]
        assert paths == sorted(paths), kind


def test_api_and_cli_report_the_same_findings_in_the_same_order(project):
    build_every_kind(project)
    write(project, "useful-views/another.parquet", "x")
    api = [f"{f.kind}: {f.message.splitlines()[0]}" for f in uvs.check(root=project)]
    cli = CliDriver(project).check()
    assert cli.exit_code == 1
    from useful_views.status import KINDS

    printed = [line for line in cli.stdout.splitlines() if line.split(":", 1)[0] in KINDS]
    assert printed == api
    # Some findings (refused, unreadable) are not for sync to fix.
    assert cli.stdout.rstrip().endswith("Fix the other findings first; then useful-views sync makes or updates "
                                        "the rest, upstream first.")


def test_check_points_to_sync_when_sync_can_fix_everything(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    out = CliDriver(project).check()
    assert out.stdout.rstrip().endswith("run: useful-views sync")


def test_cli_exit_codes(project):
    cli = CliDriver(project)
    out = cli.check()
    assert out.exit_code == 0
    assert "No problems found." in out.stdout
    write(project, "useful-views/extra.parquet", "x")
    out = cli.check()
    assert out.exit_code == 1
    assert "No problems found." not in out.stdout
    assert "useful-views/extra.parquet" in out.stdout


def test_check_changes_nothing_on_disk(driver, project):
    build_every_kind(project)
    write(project, "recipes/no_toml/01_x.py", "# x\n")
    (project / "useful-views" / "archive").mkdir()
    before = snapshot(project)
    driver.check()
    driver.check()
    assert snapshot(project) == before


def test_check_does_not_create_missing_folders(project):
    shutil.rmtree(project / "work")
    before = snapshot(project)
    uvs.check(root=project)
    assert snapshot(project) == before
    assert not (project / "work").exists()


def test_check_works_from_a_subfolder(project):
    write(project, "useful-views/extra.parquet", "x")
    (project / "notebooks").mkdir()
    for drv in (ApiDriver(project / "notebooks"), CliDriver(project / "notebooks")):
        out = drv.check()
        assert out.kinds == ["stray-file"], drv.name


def test_check_reports_what_run_would_refuse(driver, project):
    """check() applies the rules: a recipe run would refuse is reported as
    refused, with its rule number, instead of as never run."""
    write(project, "recipes/todo/recipe.toml",
          'description = "TODO: later."\ninputs = ["raw/a.csv"]\noutputs = ["useful-views/todo.parquet"]\n')
    add_raw_csv(project, "raw/a.csv")
    write(project, "recipes/todo/02_clean.R", "# R step\n")
    out = driver.check()
    assert out.kinds == ["refused"]
    assert "recipes/todo/recipe.toml" in (out.stdout or out.value[0].message)
    if isinstance(driver, ApiDriver):
        assert out.value[0].rule == 1


# --- sync -------------------------------------------------------------------


def chain_project(root: Path) -> None:
    """up reads raw/src.csv and makes up.parquet; down reads up.parquet. Neither has run."""
    add_raw_csv(root, "raw/src.csv")
    write_recipe(root, "up", inputs=["raw/src.csv"], outputs=["useful-views/up.parquet"])
    write_recipe(root, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])


def two_recipe_project(root: Path) -> None:
    """alpha reads raw/alpha_src.csv and raw/shared.csv; beta reads raw/shared.csv
    and raw/beta_src.csv. Both have run."""
    add_raw_csv(root, "raw/alpha_src.csv")
    add_raw_csv(root, "raw/beta_src.csv")
    add_raw_csv(root, "raw/shared.csv", "k,v\nx,1\ny,2\n")
    write_recipe(root, "alpha", inputs=["raw/alpha_src.csv", "raw/shared.csv"],
                 outputs=["useful-views/alpha.parquet"])
    write_recipe(root, "beta", inputs=["raw/shared.csv", "raw/beta_src.csv"],
                 outputs=["useful-views/beta.parquet", "useful-views/beta-extra.parquet"])
    uvs.run("alpha", root=root)
    uvs.run("beta", root=root)


def ran(driver, out) -> list[str]:
    """The recipes sync ran, in order, through the API or the CLI."""
    if isinstance(driver, ApiDriver):
        return list(out.value.ran)
    for line in out.stdout.splitlines():
        if line.startswith("Ran, in order: "):
            return [p.removeprefix("recipes/") for p in line[len("Ran, in order: "):].rstrip(".").split(", ")]
    return []


def test_sync_on_fresh_project_changes_nothing(driver, project):
    before = snapshot(project)
    out = driver.sync()
    assert out.ok and ran(driver, out) == []
    assert snapshot(project) == before


def test_sync_runs_never_run_recipes_upstream_first(driver, project):
    chain_project(project)
    out = driver.sync()
    assert out.ok, out.message
    assert ran(driver, out) == ["up", "down"]
    assert record_names(project) == ["up", "down"]  # recorded in run order too
    assert_clean(driver, project)


def test_sync_when_in_sync_runs_nothing_and_changes_nothing(driver, project):
    chain_project(project)
    uvs.sync(root=project)
    before = snapshot(project)
    out = driver.sync()
    assert out.ok and ran(driver, out) == []
    assert snapshot(project) == before


def test_sync_rebuilds_a_fresh_clone(driver, project):
    """raw/ is placed by hand; views and work/ are not in git."""
    chain_project(project)
    uvs.sync(root=project)
    first = generated(project)
    for view in ("up", "down"):
        (project / "useful-views" / f"{view}.parquet").unlink()
    shutil.rmtree(project / "work")
    out = driver.sync()
    assert out.ok and ran(driver, out) == ["up", "down"]
    assert generated(project) == first
    assert_clean(driver, project)


def test_sync_reruns_a_changed_recipe_and_everything_downstream(driver, project):
    chain_project(project)
    uvs.sync(root=project)
    write(project, "recipes/up/recipe.toml",
          'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["useful-views/up.parquet"]\n')
    write(project, "recipes/up/01_make.py",  # and its data changes too
          "# Write other data.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
          "pq.write_table(pa.table({'id': [7, 8, 9]}), 'useful-views/up.parquet')\n")
    out = driver.sync()
    assert out.ok and ran(driver, out) == ["up", "down"]
    assert next(r for r in load(project)["recipes"] if r["name"] == "up")["description"] == "Changed."


def test_sync_runs_downstream_even_when_a_changed_recipe_makes_the_same_data(project):
    chain_project(project)
    uvs.sync(root=project)
    write(project, "recipes/up/recipe.toml",
          'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["useful-views/up.parquet"]\n')
    result = uvs.sync(root=project)
    # up made the same data, but a step can see what a data hash leaves out, so down runs too.
    assert result.ran == ("up", "down") and result.reasons == {"up": "changed", "down": "upstream"}
    assert uvs.check(root=project) == []


def test_sync_does_not_rerun_upstream_when_only_downstream_changed(driver, project):
    chain_project(project)
    uvs.sync(root=project)
    write(project, "recipes/down/01_make.py",
          make_views_step(["useful-views/down.parquet"], description="A new description."))
    out = driver.sync()
    assert out.ok and ran(driver, out) == ["down"]


def test_sync_all_reruns_everything_in_order(project):
    chain_project(project)
    uvs.sync(root=project)
    result = uvs.sync(root=project, all=True)
    assert result.ran == ("up", "down")
    proc = subprocess.run([sys.executable, "-m", "useful_views", "sync", "--all"], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "Ran, in order: recipes/up, recipes/down." in proc.stdout


def test_sync_removes_a_deleted_recipe_its_record_and_its_view_files(driver, project):
    two_recipe_project(project)
    beta_before = next(r for r in load(project)["recipes"] if r["name"] == "beta")
    shutil.rmtree(project / "recipes" / "alpha")
    out = driver.sync()
    assert out.ok, out.message
    assert ran(driver, out) == []
    if isinstance(driver, ApiDriver):
        assert out.value.removed == ("alpha",)
        assert out.value.deleted == ("useful-views/alpha.parquet", "work/alpha/")
    else:
        assert "recipes/alpha" in out.stdout and "useful-views/alpha.parquet" in out.stdout
    data = load(project)
    assert data["recipes"] == [beta_before]
    assert [r["path"] for r in data["resources"]] == [
        "raw/beta_src.csv",
        "raw/shared.csv",
        "useful-views/beta-extra.parquet",
        "useful-views/beta.parquet",
    ]
    assert not (project / "useful-views" / "alpha.parquet").exists()
    assert "alpha" not in (project / "useful-views" / "README.md").read_text(encoding="utf-8")
    assert_clean(driver, project)


def test_sync_removes_outputs_a_recipe_no_longer_declares(driver, project):
    two_recipe_project(project)
    write(project, "recipes/beta/recipe.toml",
          'description = "Test recipe beta."\ninputs = ["raw/shared.csv", "raw/beta_src.csv"]\n'
          'outputs = ["useful-views/beta.parquet"]\n')
    write(project, "recipes/beta/01_make.py", make_views_step(["useful-views/beta.parquet"]))
    out = driver.sync()
    assert out.ok, out.message
    assert not (project / "useful-views" / "beta-extra.parquet").exists()
    beta = next(r for r in load(project)["recipes"] if r["name"] == "beta")
    assert beta["outputs"] == ["useful-views/beta.parquet"]
    assert "useful-views/beta-extra.parquet" not in resource_paths(project)
    assert_clean(driver, project)


def test_sync_result_equals_project_where_only_survivor_ran(tmp_path, project):
    two_recipe_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    uvs.sync(root=project)

    other = uvs.init(tmp_path / "elsewhere" / project.name)
    add_raw_csv(other, "raw/beta_src.csv")
    add_raw_csv(other, "raw/shared.csv", "k,v\nx,1\ny,2\n")
    shutil.copytree(project / "recipes" / "beta", other / "recipes" / "beta")
    uvs.run("beta", root=other)
    assert generated(project) == generated(other)


def test_sync_removing_every_recipe_matches_fresh_init(project):
    fresh = generated(project)
    recorded_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    assert uvs.sync(root=project).removed == ("alpha",)
    assert generated(project) == fresh


def test_sync_warns_about_recipes_reading_a_removed_view(project):
    chain_project(project)
    uvs.sync(root=project)
    shutil.rmtree(project / "recipes" / "up")
    with pytest.warns(uvs.UsefulViewsWarning, match="recipes/down reads useful-views/up.parquet"):
        result = uvs.sync(root=project)
    assert result.removed == ("up",)
    assert result.ran == ()


def test_sync_stops_at_the_first_failing_recipe(project):
    add_raw_csv(project, "raw/src.csv")
    write_recipe(project, "a_ok", inputs=["raw/src.csv"], outputs=["useful-views/ok.parquet"])
    write_recipe(project, "b_bad", inputs=["raw/src.csv"], outputs=["useful-views/bad.parquet"],
                 steps={"01_fail.py": FAILING_STEP})
    write_recipe(project, "c_after", inputs=["useful-views/bad.parquet"], outputs=["useful-views/after.parquet"])
    with pytest.raises(uvs.StepFailed):
        uvs.sync(root=project)
    assert record_names(project) == ["a_ok"]
    assert not (project / "useful-views" / "after.parquet").exists()


def test_sync_refuses_an_unfinished_recipe_before_changing_anything(driver, project):
    two_recipe_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    uvs.new_recipe("draft", root=project)
    before = snapshot(project)
    out = driver.sync()
    assert not out.ok and out.exit_code == 3
    assert "recipes/draft/recipe.toml" in out.message
    assert snapshot(project) == before


def test_sync_refuses_a_cycle_before_changing_anything(driver, project):
    write_recipe(project, "a", inputs=["useful-views/b.parquet"], outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"])
    before = snapshot(project)
    out = driver.sync()
    assert not out.ok and out.exit_code == 3
    if isinstance(driver, ApiDriver):
        assert out.rule == 6
    assert "cycle" in out.message
    assert snapshot(project) == before


def test_sync_leaves_a_folder_without_recipe_toml_alone(project):
    recorded_project(project)
    (project / "recipes" / "alpha" / "recipe.toml").unlink()
    data = load(project)
    with pytest.warns(uvs.UsefulViewsWarning, match="recipes/alpha/ has no recipe.toml"):
        result = uvs.sync(root=project)
    assert result.removed == () and result.ran == ()
    assert load(project) == data


def test_a_moved_or_cloned_project_keeps_its_name(tmp_path, project):
    """The package name is chosen once, by init; a teammate's clone in a
    folder with another name must not rewrite the committed datapackage.json."""
    recorded_project(project)
    before = (project / "datapackage.json").read_bytes()
    moved = tmp_path / "Renamed Project"
    shutil.move(str(project), moved)
    uvs.sync(root=moved)
    uvs.sync(root=moved, all=True)
    assert load(moved)["name"] == "proj"
    assert (moved / "datapackage.json").read_bytes() == before


def test_an_invalid_recorded_name_is_replaced_from_the_folder(tmp_path, project):
    recorded_project(project)
    data = load(project)
    data["name"] = "Not A Valid Name"
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    result = uvs.sync(root=project, all=True)
    assert load(project)["name"] == "proj"
    # The name changed, so sync does not say that only the layout did.
    assert not any("layout" in w for w in result.warnings)


def test_sync_output_validates_with_frictionless(project):
    two_recipe_project(project)
    shutil.rmtree(project / "recipes" / "alpha")
    uvs.sync(root=project)
    proc = subprocess.run([sys.executable, "-m", "frictionless", "validate", "datapackage.json"],
                          cwd=project, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_sync_works_from_a_subfolder(project):
    chain_project(project)
    (project / "notebooks").mkdir()
    out = ApiDriver(project / "notebooks").sync()
    assert out.ok and list(out.value.ran) == ["up", "down"]


def test_sync_outside_a_project_fails(tmp_path):
    outside = tmp_path / "nowhere"
    outside.mkdir()
    out = ApiDriver(outside).sync()
    assert not out.ok and isinstance(out.error, uvs.ProjectNotFound)


# --- the rename story --------------------------------------------------------


def test_rename_recipe_folder_then_sync(driver, project):
    add_raw_csv(project, "raw/pwt.csv")
    write_recipe(project, "pwt_panel", inputs=["raw/pwt.csv"], outputs=["useful-views/pwt-panel.parquet"])
    write_recipe(project, "gdp_per_capita", inputs=["useful-views/pwt-panel.parquet"],
                 outputs=["useful-views/gdp-per-capita.parquet"])
    assert driver.run("pwt_panel").ok
    assert driver.run("gdp_per_capita").ok

    os.rename(project / "recipes" / "pwt_panel", project / "recipes" / "panel")
    # panel has never run; gdp_per_capita will run again after it (its input is
    # made again); the old pwt_panel record has no folder.
    assert driver.check().kinds == ["never-run", "out-of-date", "missing-recipe-folder"]

    # Running the renamed recipe alone is refused, pointing to sync.
    before = snapshot(project)
    out = driver.run("panel")
    assert not out.ok and out.exit_code == 3
    assert "sync" in out.message and "useful-views/pwt-panel.parquet" in out.message
    assert snapshot(project) == before

    # sync removes the old record, keeps the view the renamed recipe declares,
    # and runs the renamed recipe and everything downstream of it.
    out = driver.sync()
    assert out.ok, out.message
    assert ran(driver, out) == ["panel", "gdp_per_capita"]
    data = load(project)
    assert [r["name"] for r in data["recipes"]] == ["panel", "gdp_per_capita"]  # in run order
    view = next(r for r in data["resources"] if r["path"] == "useful-views/pwt-panel.parquet")
    assert view["producedBy"]["recipe"] == "panel"
    assert_clean(driver, project)


# --- sync: regressions from review ---------------------------------------------


def test_sync_refuses_a_recipe_reading_its_own_output_before_changing_anything(project):
    recorded_project(project, name="gone", view="useful-views/gone.parquet", raw="raw/gone_src.csv")
    shutil.rmtree(project / "recipes" / "gone")
    add_raw_csv(project, "raw/src.csv")
    write_recipe(project, "z_self", inputs=["raw/src.csv", "useful-views/z.parquet"],
                 outputs=["useful-views/z.parquet"])
    before = snapshot(project)
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert err.value.rule == 6
    assert snapshot(project) == before


def test_sync_refreshes_stale_records_before_running_new_recipes(project):
    """b_old's record still reads raw/data.csv; its recipe.toml no longer does.
    A new recipe making useful-views/data.parquet must not be blocked by rule 8."""
    add_raw_csv(project, "raw/data.csv")
    add_raw_csv(project, "raw/other.csv")
    write_recipe(project, "b_old", inputs=["raw/data.csv"], outputs=["useful-views/b.parquet"])
    uvs.run("b_old", root=project)
    write_recipe(project, "b_old", inputs=["raw/other.csv"], outputs=["useful-views/b.parquet"])
    write_recipe(project, "a_new", inputs=["raw/other.csv"], outputs=["useful-views/data.parquet"])
    result = uvs.sync(root=project)
    assert result.ran == ("b_old", "a_new")
    assert uvs.check(root=project) == []


def test_sync_drops_a_record_left_without_outputs_and_runs_it_again(project):
    add_raw_csv(project, "raw/src.csv")
    write_recipe(project, "a", inputs=["raw/src.csv"], outputs=["useful-views/old.parquet"])
    uvs.run("a", root=project)
    write_recipe(project, "a", inputs=["raw/src.csv"], outputs=["useful-views/new.parquet"])
    result = uvs.sync(root=project)
    assert result.dropped == ("useful-views/old.parquet",)
    assert result.ran == ("a",)
    assert next(r for r in load(project)["recipes"] if r["name"] == "a")["outputs"] == ["useful-views/new.parquet"]


def test_failed_run_inside_sync_says_what_sync_already_changed(project):
    recorded_project(project, name="gone", view="useful-views/gone.parquet", raw="raw/gone_src.csv")
    shutil.rmtree(project / "recipes" / "gone")
    add_raw_csv(project, "raw/src.csv")
    write_recipe(project, "bad", inputs=["raw/src.csv"], outputs=["useful-views/bad.parquet"],
                 steps={"01_fail.py": FAILING_STEP})
    with pytest.raises(uvs.StepFailed) as err:
        uvs.sync(root=project)
    assert "sync had already removed the records" in str(err.value)
    assert err.value.sync_result is not None and err.value.sync_result.removed == ("gone",)
    assert err.value.step == "recipes/bad/01_fail.py"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a normal user")
def test_sync_refuses_an_unreadable_recipe_folder(project):
    recorded_project(project)
    folder = project / "recipes" / "locked"
    folder.mkdir()
    folder.chmod(0)
    try:
        with pytest.raises(uvs.RecipeRefused) as err:
            uvs.sync(root=project)
        assert "permission" in str(err.value)
    finally:
        folder.chmod(0o755)


def test_sync_warnings_are_not_repeated(project):
    chain_project(project)
    with open(project / "recipes" / "up" / "recipe.toml", "a", encoding="utf-8") as f:
        f.write('steps = ["01_make.py"]\n')
    with pytest.warns(uvs.UsefulViewsWarning, match="unknown key `steps`"):
        result = uvs.sync(root=project)
    assert len(result.warnings) == len(set(result.warnings))
