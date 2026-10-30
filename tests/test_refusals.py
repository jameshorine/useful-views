"""Rules 1 to 8 (under "Rules" in the README) and the refusals of the recipe name.

Every refusal is checked the same way, through both the API and the CLI:

- the run fails (exit code 3 on the command line);
- through the API it raises `RecipeRefused` with the rule number (or None for
  a refusal without a rule number) and `.path` naming the file to fix;
- the message names the file to fix;
- nothing on disk changed, not even work/ or a leftover view ("before a run,
  and before anything is changed on disk").
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import useful_views as uvs

from conftest import add_raw_csv, make_views_step, snapshot, write

RAW = "raw/data.csv"
VIEW = "useful-views/a-view.parquet"
TOML_A = "recipes/a/recipe.toml"
STEP_A = "recipes/a/01_make.py"


# --- helpers ----------------------------------------------------------------


def tstr(value: str) -> str:
    """A TOML basic string. JSON escaping is valid TOML for these strings."""
    return json.dumps(value, ensure_ascii=False)


def write_toml(root: Path, name: str, *, description="Test recipe.", inputs=(RAW,), outputs=(VIEW,),
               extra: str = "") -> Path:
    lines = []
    if description is not None:
        lines.append(f"description = {tstr(description)}")
    if inputs is not None:
        lines.append("inputs = [" + ", ".join(tstr(p) for p in inputs) + "]")
    if outputs is not None:
        lines.append("outputs = [" + ", ".join(tstr(p) for p in outputs) + "]")
    if extra:
        lines.append(extra)
    return write(root, f"recipes/{name}/recipe.toml", "\n".join(lines) + "\n")


def make_recipe(root: Path, name: str = "a", *, inputs=(RAW,), outputs=(VIEW,), steps=None,
                description="Test recipe.", raw=True) -> Path:
    """A recipe folder with a valid recipe.toml and one valid step (unless
    `steps` says otherwise). Creates raw/data.csv when `raw` is true."""
    if raw and not (root / RAW).exists():
        add_raw_csv(root, RAW)
    write_toml(root, name, description=description, inputs=inputs, outputs=outputs)
    if steps is None:
        steps = {"01_make.py": make_views_step([o for o in outputs if isinstance(o, str)])}
    for filename, code in steps.items():
        write(root, f"recipes/{name}/{filename}", code)
    return root / "recipes" / name


def seed_leftovers(root: Path, name: str = "a", outputs=(VIEW,)) -> None:
    """Old work files and old views: a refused run must not delete them (they
    are cleared only after rules 1 to 8 pass)."""
    write(root, f"work/{name}/leftover.txt", "old work file\n")
    for rel in outputs:
        target = root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"old view bytes")


def tiny_parquet(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"id": pa.array([1, 2], pa.int64())}), target)


def _paths(path) -> set[str]:
    if path is None:
        return set()
    if isinstance(path, str):
        path = (path,)
    return {p.rstrip("/") for p in path}


def assert_refused(driver, root: Path, recipe: str, *, rule, path, mentions=(), rules=None,
                   path_in_message=True):
    """Run `recipe` and check it was refused cleanly.

    `rule`: expected RecipeRefused.rule (API only). `rules`: a set of
    acceptable values, where the spec does not pin the number down.
    `path`: the file to fix (a string or a tuple of acceptable strings). It
    must appear in the message, and through the API equal `.path`.
    """
    before = snapshot(root)
    out = driver.run(recipe)
    after = snapshot(root)

    assert not out.ok, f"expected a refusal, but the run succeeded: {out}"
    assert out.exit_code == 3, f"exit code {out.exit_code}; stderr:\n{out.stderr}"
    if driver.name == "api":
        assert isinstance(out.error, uvs.RecipeRefused), f"{type(out.error).__name__}: {out.error}"
        allowed = rules if rules is not None else {rule}
        assert out.rule in allowed, f"rule {out.rule!r}, expected one of {allowed!r}: {out.message}"
        if path is not None:
            assert out.error.path is not None, f"RecipeRefused.path is None: {out.message}"
            assert out.error.path.rstrip("/") in _paths(path), (out.error.path, path)
    else:
        assert "Traceback" not in out.stderr, out.stderr
        assert out.message.strip(), "the CLI printed no message"

    if path is not None and not path_in_message:
        folder = next(iter(_paths(path))).rsplit("/", 1)[0]  # the recipe folder
        assert folder.rstrip("/") in out.message, f"message does not name {folder!r}:\n{out.message}"
    if path is not None and path_in_message:
        assert any(p in out.message for p in _paths(path)), (
            f"message does not name the file to fix ({path!r}):\n{out.message}"
        )
    for text in mentions:
        assert text in out.message, f"message lacks {text!r}:\n{out.message}"

    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not changed, f"refused run changed files on disk: {changed}"
    return out


# --- control: the helpers build a recipe that does run ------------------------


def test_control_valid_recipe_runs(driver, project):
    make_recipe(project)
    seed_leftovers(project)
    out = driver.run("a")
    assert out.ok, out.message
    assert not (project / "work" / "a" / "leftover.txt").exists()
    assert pq.read_table(project / VIEW).num_rows == 3


def test_control_step_backup_file_with_same_number_is_ignored(driver, project):
    make_recipe(project)
    write(project, "recipes/a/01_make.py.bak", "this is not a step\n")
    assert driver.run("a").ok


# --- rule 1: recipe.toml ------------------------------------------------------


def test_rule1_recipe_toml_missing(driver, project):
    make_recipe(project)
    (project / TOML_A).unlink()
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A)


def test_rule1_recipe_toml_not_valid_toml(driver, project):
    make_recipe(project)
    write(project, TOML_A, 'description = "Unclosed\ninputs = ["raw/data.csv"]\n')
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A)


def test_rule1_recipe_toml_not_utf8(driver, project):
    make_recipe(project, description="Café prices.")
    (project / TOML_A).write_bytes(
        'description = "Café prices."\ninputs = ["raw/data.csv"]\noutputs = ["useful-views/a-view.parquet"]\n'
        .encode("latin-1")
    )
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=1, path=TOML_A)
    assert "UTF-8" in out.message


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_rule1_recipe_toml_unreadable(driver, project):
    make_recipe(project)
    seed_leftovers(project)
    toml = project / TOML_A
    toml.chmod(0)
    try:
        if os.access(toml, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        out = driver.run("a")
        assert not out.ok
        assert out.exit_code == 3
        if driver.name == "api":
            assert isinstance(out.error, uvs.RecipeRefused)
            assert out.rule == 1
        assert TOML_A in out.message
    finally:
        toml.chmod(0o644)


@pytest.mark.parametrize("missing", ["description", "inputs", "outputs"])
def test_rule1_required_key_missing(driver, project, missing):
    make_recipe(project)
    write_toml(project, "a", **{missing: None})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=[missing])


@pytest.mark.parametrize("description", ["TODO: say what this recipe produces.", "  TODO describe",
                                         "TODO"])
def test_rule1_description_starts_with_todo(driver, project, description):
    make_recipe(project, description=description)
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=["TODO"])


@pytest.mark.parametrize("description", ["", "   ", "\n\t "])
def test_rule1_description_empty(driver, project, description):
    make_recipe(project, description=description)
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=["description"])


@pytest.mark.parametrize("key", ["inputs", "outputs"])
def test_rule1_empty_list(driver, project, key):
    make_recipe(project)
    write_toml(project, "a", **{key: ()})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=[key])


@pytest.mark.parametrize("key", ["inputs", "outputs"])
def test_rule1_not_a_list(driver, project, key):
    make_recipe(project)
    value = RAW if key == "inputs" else VIEW
    other = "outputs" if key == "inputs" else "inputs"
    write(project, TOML_A,
          f'description = "Test recipe."\n{key} = {tstr(value)}\n'
          f'{other} = [{tstr(VIEW if key == "inputs" else RAW)}]\n')
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=[key])


def test_rule1_input_listed_twice(driver, project):
    make_recipe(project, inputs=(RAW, RAW))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=[RAW])


def test_rule1_output_listed_twice(driver, project):
    make_recipe(project, outputs=(VIEW, VIEW))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=1, path=TOML_A, mentions=[VIEW])


# --- rule 2: step files -------------------------------------------------------


def test_rule2_no_step_file(driver, project):
    make_recipe(project, steps={})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=2, path=("recipes/a/", "recipes/a"))


def test_rule2_only_ignored_files(driver, project):
    """Near-miss names and other files are not steps, so there is no step."""
    make_recipe(project, steps={"1_make.py": make_views_step([VIEW]), "notes.txt": "notes\n",
                                "01_make.py.bak": make_views_step([VIEW])})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=2, path=("recipes/a/", "recipes/a"))


@pytest.mark.parametrize(
    "code",
    [
        "import pyarrow\n# A comment that comes too late.\n",
        '"""Docstring instead of a comment."""\nimport pyarrow\n',
        "#!/usr/bin/env python\nimport pyarrow\n",
        "# -*- coding: utf-8 -*-\nimport pyarrow\n",
        "#\n#   \n#\nimport pyarrow\n",
        "# %%\nimport pyarrow\n",
        "",
    ],
    ids=["code-first", "docstring", "shebang-only", "coding-only", "empty-hashes", "cell-marker", "empty-file"],
)
def test_rule2_step_without_top_comment(driver, project, code):
    make_recipe(project, steps={"01_make.py": code})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=2, path=STEP_A)


@pytest.mark.parametrize("comment", ["# TODO: say what this step does.", "#TODO", "#   TODO later"])
def test_rule2_step_comment_starts_with_todo(driver, project, comment):
    make_recipe(project, steps={"01_make.py": comment + "\n" + make_views_step([VIEW])})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=2, path=STEP_A, mentions=["TODO"])


def test_rule2_later_step_without_comment_refuses_before_first_step_runs(driver, project):
    marker = "work/a/first_step_ran.txt"
    first = f"# Write a marker.\nopen({marker!r}, 'w').write('ran')\n"
    make_recipe(project, steps={"01_first.py": first, "02_second.py": "print('no comment')\n"})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=2, path="recipes/a/02_second.py")
    assert not (project / marker).exists()


def test_rule2_two_steps_share_a_number(driver, project):
    make_recipe(project, steps={"01_make.py": make_views_step([VIEW]),
                                "01_again.py": make_views_step([VIEW])})
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=2,
                         path=("recipes/a/01_make.py", "recipes/a/01_again.py"), path_in_message=False)
    assert "recipes/a/" in out.message
    assert "01_make.py" in out.message and "01_again.py" in out.message


def test_rule2_step_not_utf8(driver, project):
    make_recipe(project)
    (project / STEP_A).write_bytes("# Écrire la vue.\nprint(1)\n".encode("latin-1"))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=2, rules={2, None}, path=STEP_A)
    assert "UTF-8" in out.message


# --- rule 3: steps in another language ----------------------------------------


@pytest.mark.parametrize("ext", ["R", "r", "Rmd", "qmd", "jl", "do"])
def test_rule3_other_language_step(driver, project, ext):
    make_recipe(project)
    bad = f"recipes/a/02_clean.{ext}"
    write(project, bad, "# Clean the data.\n")
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=3, path=bad)
    assert "must be python (.py) files" in out.message.lower(), out.message


def test_rule3_notebook_step(driver, project):
    make_recipe(project)
    bad = "recipes/a/02_explore.ipynb"
    write(project, bad, '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}\n')
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=3, path=bad)
    lower = out.message.lower()
    assert "notebook" in lower and "yet" in lower, out.message


def test_rule3_other_language_step_alone(driver, project):
    """A recipe whose only step is an R file is refused, either way."""
    make_recipe(project, steps={"01_make.R": "# Make the view.\n"})
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=3, rules={2, 3},
                   path=("recipes/a/01_make.R", "recipes/a/", "recipes/a"))


# --- rule 4: inputs -----------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["data/x.csv", "x.csv", "work/a/x.parquet", "useful-views/sub/x.parquet", "recipes/b/x.csv",
     "datapackage.json", "README.md"],
)
def test_rule4_input_outside_raw_and_views(driver, project, rel):
    if not (project / rel).exists():
        add_raw_csv(project, rel)
    make_recipe(project, inputs=(rel,))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=[rel])


def test_rule4_raw_input_missing(driver, project):
    make_recipe(project, inputs=("raw/absent.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/absent.csv"])


def test_rule4_raw_input_in_subfolder_missing(driver, project):
    make_recipe(project, inputs=("raw/sub/absent.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/sub/absent.csv"])


def test_rule4_view_input_missing(driver, project):
    """A view input whose file is absent (nobody made it) is refused before anything runs."""
    make_recipe(project, inputs=("useful-views/absent.parquet",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, rules={4, 5}, path=TOML_A,
                   mentions=["useful-views/absent.parquet"])


def test_rule4_input_letter_case_differs_from_disk(driver, project):
    add_raw_csv(project, RAW)
    make_recipe(project, inputs=("raw/Data.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/Data.csv"])


def test_rule4_input_folder_case_differs_from_disk(driver, project):
    add_raw_csv(project, "raw/Sub/data.csv")
    make_recipe(project, inputs=("raw/sub/data.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/sub/data.csv"])


def test_rule4_empty_raw_input(driver, project):
    (project / "raw" / "empty.csv").write_bytes(b"")
    make_recipe(project, inputs=("raw/empty.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/empty.csv"])


def test_rule4_raw_input_is_a_directory(driver, project):
    (project / "raw" / "folder.csv").mkdir()
    add_raw_csv(project, "raw/folder.csv/inner.csv")
    make_recipe(project, inputs=("raw/folder.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, path=TOML_A, mentions=["raw/folder.csv"])


def test_rule4_unreadable_raw_parquet(driver, project):
    (project / "raw" / "broken.parquet").write_bytes(b"this is not a parquet file at all")
    make_recipe(project, inputs=("raw/broken.parquet",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, rules={4, None}, path="raw/broken.parquet",
                   mentions=["raw/broken.parquet"])


# Path forms: refused, with the corrected string where there is one.

INPUT_FORMS = [
    ("./raw/data.csv", "raw/data.csv"),
    ("raw\\data.csv", "raw/data.csv"),
    ("raw//data.csv", "raw/data.csv"),
    (" raw/data.csv", "raw/data.csv"),
    ("raw/data.csv ", "raw/data.csv"),
    ("raw/sub/../data.csv", "raw/data.csv"),
]


@pytest.mark.parametrize("written, corrected", INPUT_FORMS,
                         ids=["dot-slash", "backslash", "double-slash", "leading-space", "trailing-space",
                              "dot-dot"])
def test_input_path_form_refused_with_corrected_string(driver, project, written, corrected):
    add_raw_csv(project, RAW)
    (project / "raw" / "sub").mkdir()
    make_recipe(project, inputs=(written,))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=4, rules={4, None}, path=TOML_A)
    assert corrected in out.message.replace(written, ""), out.message


def test_input_absolute_path_refused(driver, project):
    add_raw_csv(project, "raw/prices.csv")
    absolute = (project / "raw/prices.csv").as_posix()
    make_recipe(project, inputs=(absolute,))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=4, rules={4, None}, path=TOML_A)
    assert "raw/prices.csv" in out.message.replace(absolute, ""), (
        "the message should give the corrected string 'raw/prices.csv':\n" + out.message
    )


def test_input_home_path_refused(driver, project):
    make_recipe(project, inputs=("~/data.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=4, rules={4, None}, path=TOML_A, mentions=["~/data.csv"])


def test_input_view_path_form_refused(driver, project):
    make_recipe(project, inputs=(RAW, "./useful-views/b-view.parquet"))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=4, rules={4, None}, path=TOML_A)
    assert "useful-views/b-view.parquet" in out.message.replace("./useful-views/b-view.parquet", "")


# --- rule 5: view inputs must come from a recorded recipe ----------------------


def test_rule5_view_input_not_made_by_any_recipe(driver, project):
    tiny_parquet(project, "useful-views/handmade.parquet")
    make_recipe(project, inputs=("useful-views/handmade.parquet",))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=5, path=TOML_A,
                         mentions=["useful-views/handmade.parquet"])
    assert "not made by any recipe" in out.message, out.message


def test_rule5_producer_declared_but_never_run(driver, project):
    make_recipe(project, "b", outputs=("useful-views/b-view.parquet",))
    tiny_parquet(project, "useful-views/b-view.parquet")  # placed by hand, not by running b
    make_recipe(project, inputs=("useful-views/b-view.parquet",))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=5, path=TOML_A, mentions=["recipes/b"])
    assert "first" in out.message.lower(), out.message


def test_rule5_producer_recorded_without_that_view(driver, project):
    make_recipe(project, "b", outputs=("useful-views/b-view.parquet",))
    uvs.run("b", root=project)
    # b now declares a second view but has not run since.
    write_toml(project, "b", outputs=("useful-views/b-view.parquet", "useful-views/b-extra.parquet"))
    tiny_parquet(project, "useful-views/b-extra.parquet")
    make_recipe(project, inputs=("useful-views/b-extra.parquet",))
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=5, path=TOML_A, mentions=["recipes/b"])
    assert "first" in out.message.lower(), out.message


def test_rule5_only_a_record_claims_the_view(driver, project):
    """b made the view once, then stopped declaring it: no recipe.toml declares it."""
    make_recipe(project, "b", outputs=("useful-views/b-view.parquet",))
    uvs.run("b", root=project)
    write_toml(project, "b", outputs=("useful-views/b-new.parquet",))
    make_recipe(project, inputs=("useful-views/b-view.parquet",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=5, path=TOML_A, mentions=["useful-views/b-view.parquet"])


# --- rule 6: cycles -----------------------------------------------------------


def test_rule6_recipe_reads_its_own_output(driver, project):
    make_recipe(project)
    uvs.run("a", root=project)  # recorded, and the view exists, so rules 4 and 5 pass
    write_toml(project, "a", inputs=(RAW, VIEW))
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=6, path=TOML_A, mentions=[VIEW])


def test_rule6_two_recipe_cycle(driver, project):
    vb = "useful-views/b-view.parquet"
    make_recipe(project, "a")
    make_recipe(project, "b", inputs=(VIEW,), outputs=(vb,))
    uvs.run("a", root=project)
    uvs.run("b", root=project)
    write_toml(project, "a", inputs=(RAW, vb))
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=6, path=TOML_A)


def test_rule6_three_recipe_cycle(driver, project):
    vb, vc = "useful-views/b-view.parquet", "useful-views/c-view.parquet"
    make_recipe(project, "a")
    make_recipe(project, "b", inputs=(VIEW,), outputs=(vb,))
    make_recipe(project, "c", inputs=(vb,), outputs=(vc,))
    for name in "abc":
        uvs.run(name, root=project)
    write_toml(project, "a", inputs=(RAW, vc))
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=6, path=TOML_A)


def test_rule6_cycle_through_the_middle_recipe(driver, project):
    """The same cycle, refused when the recipe being run is not its first link."""
    vb = "useful-views/b-view.parquet"
    vb2 = "useful-views/b-second.parquet"
    make_recipe(project, "a")
    make_recipe(project, "b", inputs=(VIEW,), outputs=(vb,))
    uvs.run("a", root=project)
    uvs.run("b", root=project)
    # a now also reads b's view; b is changed to produce a second view.
    make_recipe(project, "a", inputs=(RAW, vb))
    write_toml(project, "b", inputs=(VIEW,), outputs=(vb, vb2))
    write(project, "work/b/leftover.txt", "old work file\n")
    assert_refused(driver, project, "b", rule=6, path="recipes/b/recipe.toml")


def test_rule6_cycle_through_another_recipes_record(driver, project):
    """b's recipe.toml no longer reads a's view, but its record still does
    (it has not run since). Rule 6 builds its graph from both."""
    vb = "useful-views/b-view.parquet"
    make_recipe(project, "a")
    make_recipe(project, "b", inputs=(VIEW,), outputs=(vb,))
    uvs.run("a", root=project)
    uvs.run("b", root=project)
    write_toml(project, "b", inputs=(RAW,), outputs=(vb,))
    write_toml(project, "a", inputs=(RAW, vb))
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=6, path=TOML_A)


# --- rule 7: outputs ----------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["work/a/x.parquet", "raw/x.parquet", "x.parquet", "useful-views/sub/x.parquet", "recipes/a/x.parquet",
     "useful-views"],
)
def test_rule7_output_not_directly_inside_views(driver, project, rel):
    make_recipe(project, outputs=(rel,), steps={"01_make.py": "# Write nothing.\n"})
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[rel])


def test_rule7_output_with_no_name_before_the_extension(driver, project):
    """A view named only ".parquet" would be recorded, and then every command
    would refuse the record: it is refused here instead."""
    make_recipe(project, outputs=("useful-views/.parquet",), steps={"01_make.py": "# Write nothing.\n"})
    out = assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=["useful-views/.parquet"])
    assert "a name before the extension" in out.message, out.message


def test_rule7_output_is_the_catalog(driver, project):
    make_recipe(project, outputs=("useful-views/README.md",), steps={"01_make.py": "# Write nothing.\n"})
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=["useful-views/README.md"])


@pytest.mark.parametrize("rel", ["useful-views/a-view.csv", "useful-views/a-view", "useful-views/a-view.json",
                                 "useful-views/a-view.parquet.bak"])
def test_rule7_output_with_wrong_extension(driver, project, rel):
    make_recipe(project, outputs=(rel,), steps={"01_make.py": "# Write nothing.\n"})
    seed_leftovers(project, outputs=(rel,))
    out = assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[rel])
    assert ".parquet" in out.message, out.message


def test_rule7_output_is_a_directory(driver, project):
    make_recipe(project)
    (project / VIEW).mkdir()
    write(project, f"{VIEW}/part-0.txt", "inside a folder\n")
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW])


OUTPUT_FORMS = [
    ("./useful-views/a-view.parquet", VIEW),
    ("useful-views\\a-view.parquet", VIEW),
    ("useful-views//a-view.parquet", VIEW),
    (" useful-views/a-view.parquet", VIEW),
    ("useful-views/a-view.parquet ", VIEW),
    ("useful-views/../useful-views/a-view.parquet", VIEW),
]


@pytest.mark.parametrize("written, corrected", OUTPUT_FORMS,
                         ids=["dot-slash", "backslash", "double-slash", "leading-space", "trailing-space",
                              "dot-dot"])
def test_output_path_form_refused_with_corrected_string(driver, project, written, corrected):
    make_recipe(project, outputs=(written,), steps={"01_make.py": "# Write nothing.\n"})
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=7, rules={7, None}, path=TOML_A)
    assert corrected in out.message.replace(written, ""), out.message


def test_output_absolute_path_refused(driver, project):
    absolute = (project / VIEW).as_posix()
    make_recipe(project, outputs=(absolute,), steps={"01_make.py": "# Write nothing.\n"})
    seed_leftovers(project)
    out = assert_refused(driver, project, "a", rule=7, rules={7, None}, path=TOML_A)
    assert VIEW in out.message.replace(absolute, ""), (
        f"the message should give the corrected string {VIEW!r}:\n" + out.message
    )


def test_rule7_output_declared_by_another_recipe_toml(driver, project):
    make_recipe(project, "b", outputs=(VIEW,))  # b has never run
    make_recipe(project)
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW, "recipes/b"])


def test_rule7_output_declared_by_another_recipe_that_ran(driver, project):
    make_recipe(project, "b", outputs=(VIEW,))
    uvs.run("b", root=project)
    make_recipe(project)
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW, "recipes/b"])


def test_rule7_output_claimed_by_another_record_folder_present(driver, project):
    make_recipe(project, "b", outputs=(VIEW,))
    uvs.run("b", root=project)
    write_toml(project, "b", outputs=("useful-views/b-new.parquet",))  # b no longer declares it
    make_recipe(project)
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW, "recipes/b"])


def test_rule7_output_claimed_by_record_whose_folder_is_gone(driver, project):
    import shutil

    make_recipe(project, "b", outputs=(VIEW,))
    uvs.run("b", root=project)
    shutil.rmtree(project / "recipes" / "b")
    make_recipe(project)
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW, "useful-views sync"])


# --- rule 8: resource name collisions ------------------------------------------


def test_rule8_same_file_name_in_different_raw_folders_is_fine(driver, project):
    """A raw file's resource name includes its folders below raw/, so survey
    waves stored as raw/wave1/x.csv and raw/wave2/x.csv do not clash."""
    add_raw_csv(project, "raw/a/x.csv")
    add_raw_csv(project, "raw/b/x.csv")
    make_recipe(project, inputs=("raw/a/x.csv", "raw/b/x.csv"))
    out = driver.run("a")
    assert out.ok, out.message
    names = {r["path"]: r["name"] for r in json.loads((project / "datapackage.json").read_text())["resources"]}
    assert names["raw/a/x.csv"] == "a-x" and names["raw/b/x.csv"] == "b-x"


def test_rule8_raw_path_and_file_name_that_collide(driver, project):
    add_raw_csv(project, "raw/a/x.csv")
    add_raw_csv(project, "raw/a-x.csv")
    make_recipe(project, inputs=("raw/a/x.csv", "raw/a-x.csv"))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/a/x.csv", "raw/a-x.csv"])


def test_rule8_raw_input_and_view_same_name(driver, project):
    add_raw_csv(project, "raw/x.csv")
    make_recipe(project, inputs=("raw/x.csv",), outputs=("useful-views/x.parquet",))
    seed_leftovers(project, outputs=("useful-views/x.parquet",))
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/x.csv", "useful-views/x.parquet"])


def test_rule8_raw_inputs_differing_only_in_extension(driver, project):
    add_raw_csv(project, "raw/x.csv")
    tiny_parquet(project, "raw/x.parquet")
    make_recipe(project, inputs=("raw/x.csv", "raw/x.parquet"))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/x.csv", "raw/x.parquet"])


def test_rule8_names_that_collide_after_cleaning(driver, project):
    """'My Data' and 'my-data' both become the resource name 'my-data'."""
    add_raw_csv(project, "raw/My Data.csv")
    add_raw_csv(project, "raw/my-data.csv")
    make_recipe(project, inputs=("raw/My Data.csv", "raw/my-data.csv"))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False,
                   mentions=["raw/My Data.csv", "raw/my-data.csv"])


def test_rule8_two_outputs_differing_only_in_case(driver, project):
    outs = ("useful-views/Totals.parquet", "useful-views/totals.parquet")
    make_recipe(project, outputs=outs, steps={"01_make.py": "# Write nothing.\n"})
    write(project, "work/a/leftover.txt", "old work file\n")
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=list(outs))


def test_rule8_view_collides_with_raw_file_of_another_record(driver, project):
    add_raw_csv(project, "raw/x.csv")
    make_recipe(project, "b", inputs=("raw/x.csv",), outputs=("useful-views/b-view.parquet",))
    uvs.run("b", root=project)
    make_recipe(project, outputs=("useful-views/x.parquet",))
    seed_leftovers(project, outputs=("useful-views/x.parquet",))
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/x.csv", "useful-views/x.parquet"])


def test_rule8_raw_collides_with_raw_file_of_another_record(driver, project):
    add_raw_csv(project, "raw/b-x.csv")
    add_raw_csv(project, "raw/b/x.csv")
    make_recipe(project, "b", inputs=("raw/b-x.csv",), outputs=("useful-views/b-view.parquet",))
    uvs.run("b", root=project)
    make_recipe(project, inputs=("raw/b/x.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/b-x.csv", "raw/b/x.csv"])


def test_rule8_names_in_other_scripts_stay_distinct(driver, project):
    add_raw_csv(project, "raw/人口.csv")
    add_raw_csv(project, "raw/收入.csv")
    make_recipe(project, inputs=("raw/人口.csv", "raw/收入.csv"))
    out = driver.run("a")
    assert out.ok, out.message


def test_rule8_raw_collides_with_view_of_another_record(driver, project):
    make_recipe(project, "b", outputs=("useful-views/x.parquet",))
    uvs.run("b", root=project)
    add_raw_csv(project, "raw/x.csv")
    make_recipe(project, inputs=("raw/x.csv",))
    seed_leftovers(project)
    assert_refused(driver, project, "a", rule=8, path=TOML_A, path_in_message=False, mentions=["raw/x.csv", "useful-views/x.parquet"])


def test_rule8_shared_raw_input_is_not_a_collision(driver, project):
    """Two recipes reading the same raw file record one resource: no refusal."""
    make_recipe(project, "b", outputs=("useful-views/b-view.parquet",))
    uvs.run("b", root=project)
    make_recipe(project)
    out = driver.run("a")
    assert out.ok, out.message


# --- other recipes' recipe.toml files: warn, skip, continue ---------------------


def test_unreadable_other_recipe_toml_warns_and_run_continues(driver, project):
    write(project, "recipes/b/recipe.toml", 'description = "Broken\noutputs = [\n')
    write(project, "recipes/b/01_make.py", make_views_step(["useful-views/b-view.parquet"]))
    make_recipe(project)
    out = driver.run("a")
    assert out.ok, out.message
    assert any("recipes/b" in w for w in out.warnings), out.warnings
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in data["recipes"]] == ["a"]


def test_non_utf8_other_recipe_toml_warns_and_run_continues(driver, project):
    (project / "recipes" / "b").mkdir(parents=True)
    (project / "recipes" / "b" / "recipe.toml").write_bytes(
        'description = "Café."\ninputs = ["raw/data.csv"]\noutputs = ["useful-views/b.parquet"]\n'
        .encode("latin-1")
    )
    make_recipe(project)
    out = driver.run("a")
    assert out.ok, out.message
    assert any("recipes/b" in w for w in out.warnings), out.warnings


def test_folder_without_recipe_toml_warns_and_run_continues(driver, project):
    write(project, "recipes/scratch/notes.py", "# Not a recipe.\n")
    make_recipe(project)
    out = driver.run("a")
    assert out.ok, out.message
    assert any("recipes/scratch" in w for w in out.warnings), out.warnings


def test_unreadable_other_recipe_toml_does_not_hide_its_record(driver, project):
    """b's recipe.toml is skipped, but its record still claims the view (rule 7)."""
    make_recipe(project, "b", outputs=(VIEW,))
    uvs.run("b", root=project)
    write(project, "recipes/b/recipe.toml", "this is = = not toml\n")
    make_recipe(project)
    write(project, "work/a/leftover.txt", "old work file\n")
    out = assert_refused(driver, project, "a", rule=7, path=TOML_A, mentions=[VIEW, "recipes/b"])
    assert any("recipes/b" in w for w in out.warnings), out.warnings


def test_unreadable_other_recipe_toml_does_not_block_rule5_via_records(driver, project):
    """b's recipe.toml is broken, so no recipe.toml declares b's view; rule 5
    still refuses a's input with a clear message rather than crashing."""
    vb = "useful-views/b-view.parquet"
    make_recipe(project, "b", outputs=(vb,))
    uvs.run("b", root=project)
    write(project, "recipes/b/recipe.toml", "broken = [\n")
    make_recipe(project, inputs=(vb,))
    seed_leftovers(project)
    out = driver.run("a")
    assert not out.ok and out.exit_code == 3
    assert "Traceback" not in out.stderr
    assert "recipes/b/recipe.toml cannot be read" in out.message
    if driver.name == "api":
        assert isinstance(out.error, uvs.RecipeRefused) and out.error.rule == 5


# --- which recipe to run ------------------------------------------------------


def test_unknown_recipe(driver, project):
    make_recipe(project)
    write(project, "work/nope/leftover.txt", "old work file\n")
    out = assert_refused(driver, project, "nope", rule=None, path=None, mentions=["nope"])
    assert "recipes/nope" in out.message, out.message


def test_unknown_recipe_with_no_recipes_at_all(driver, project):
    assert_refused(driver, project, "pwt_panel", rule=None, path=None, mentions=["pwt_panel"])


def test_recipe_name_case_must_match_folder(driver, project):
    make_recipe(project)
    seed_leftovers(project)
    assert_refused(driver, project, "A", rule=None, path=None, mentions=["A"])


def test_folder_that_is_a_file_is_not_a_recipe(driver, project):
    write(project, "recipes/a", "a plain file, not a recipe folder\n")
    assert_refused(driver, project, "a", rule=None, path=None, mentions=["recipes/a"])


@pytest.mark.parametrize("folder", ["Bad_Name", "bad-name", "bad name", "café"])
def test_recipe_folder_with_invalid_name(driver, project, folder):
    make_recipe(project, folder)
    seed_leftovers(project, name=folder)
    assert_refused(driver, project, folder, rule=None, path=(f"recipes/{folder}/", f"recipes/{folder}"))


@pytest.mark.parametrize("form", ["recipes/Bad-Name", "recipes/Bad-Name/"])
def test_recipe_folder_with_invalid_name_given_as_path(driver, project, form):
    make_recipe(project, "Bad-Name")
    seed_leftovers(project, name="Bad-Name")
    assert_refused(driver, project, form, rule=None, path=("recipes/Bad-Name/", "recipes/Bad-Name"))
