"""Regression tests for problems found in review (live step output, stray
processes, CSV edge cases, messages)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

import useful_views as uvs
from useful_views.paths import path_problem
from useful_views.recipe import Step, step_description
from useful_views.record import describe_raw, render_catalog
from useful_views.runner import run_step

from conftest import add_raw_csv, write, write_recipe


def validate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "frictionless", "validate", "datapackage.json"],
        cwd=root, capture_output=True, text=True,
    )


# --- running steps ----------------------------------------------------------


def test_step_output_is_relayed_while_the_step_runs(project, monkeypatch):
    write(project, "recipes/a/01_slow.py", """
        # Print, then wait.
        import time
        print("first", flush=False)
        time.sleep(6)
    """)
    seen = []

    class Sink:
        encoding = "utf-8"

        def write(self, text):
            seen.append((time.monotonic(), text))

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Sink())
    start = time.monotonic()
    code, _ = run_step(project, Step("slow", "recipes/a/01_slow.py", "Print, then wait."))
    assert code == 0
    first = next(t for t, text in seen if "first" in text)
    # Generous, for loaded CI machines: what matters is well before the step's 6 s.
    assert first - start < 4.0, "output should arrive before the step ends"


def test_background_process_holding_pipes_does_not_block_run(project):
    write(project, "recipes/a/01_spawn.py", """
        # Start a background process that outlives the step.
        import subprocess, sys
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        print("step done")
    """)
    start = time.monotonic()
    code, _ = run_step(project, Step("spawn", "recipes/a/01_spawn.py", "x"))
    assert code == 0
    assert time.monotonic() - start < 20  # not the background process's 30 s


def test_unencodable_step_output_is_not_dropped(project, monkeypatch, capsys):
    import io

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    write(project, "recipes/a/01_print.py", '# Print.\nprint("one")\nprint("12 rows \\u2192 kept")\nprint("three")\n')
    run_step(project, Step("print", "recipes/a/01_print.py", "Print."))
    text = buffer.getvalue().decode("ascii")
    assert "one" in text and "kept" in text and "three" in text


def test_missing_module_hint_names_interpreter_and_package(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"],
                 steps={"01_x.py": "# Use scikit-learn.\nimport sklearn_not_installed_xyz\n"})
    with pytest.raises(uvs.StepFailed) as err:
        uvs.run("a", root=project)
    assert sys.executable in str(err.value)
    assert "pip install" in str(err.value) or "uv add" in str(err.value)


def test_cli_refusal_exits_3_without_a_traceback(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    (project / "work").mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-m", "useful_views", "run", "nope"], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 3
    assert "Traceback" not in proc.stderr


# --- raw CSV description ---------------------------------------------------


@pytest.mark.parametrize("name, data", [
    ("na_tokens", b"id,value\n1,NA\n2,3.5\n3,N/A\n"),
    ("cp1252", "id,name\n1,José\n2,Müller\n".encode("cp1252")),
    ("blank_header", b",a\n1,2\n"),
    ("duplicate_header", b"x,x\n1,2\n"),
])
def test_awkward_raw_csv_still_validates(project, name, data):
    rel = f"raw/{name}.csv"
    (project / rel).write_bytes(data)
    write_recipe(project, "a", inputs=[rel], outputs=["useful-views/a.parquet"])
    uvs.run("a", root=project)
    proc = validate(project)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_na_tokens_make_a_string_column(project):
    (project / "raw").mkdir(exist_ok=True)
    (project / "raw/na.csv").write_bytes(b"id,value\n1,NA\n2,3.5\n")
    fields = describe_raw(project, "raw/na.csv")["schema"]["fields"]
    assert fields == [{"name": "id", "type": "integer"}, {"name": "value", "type": "string"}]


def test_non_utf8_csv_is_recorded_as_a_plain_file(project):
    (project / "raw/latin.csv").write_bytes("a\nJosé\n".encode("latin-1"))
    assert describe_raw(project, "raw/latin.csv") == {
        "name": "latin", "type": "file", "path": "raw/latin.csv", "scheme": "file", "format": "csv",
    }


# --- parsing and messages ---------------------------------------------------


def test_cell_marker_ends_the_description():
    assert step_description("# Reshape the data.\n# %%\nimport x\n") == "Reshape the data."


@pytest.mark.parametrize("given, corrected", [
    ("raw/sub/../data.csv", "raw/data.csv"),
    ("./raw//a.csv", "raw/a.csv"),
    (" ./raw/a.csv", "raw/a.csv"),
    ("raw\\sub//a.csv", "raw/sub/a.csv"),
])
def test_path_messages_give_a_fully_corrected_string(given, corrected):
    assert f"write {corrected!r}" in path_problem(given)


def test_absolute_path_inside_project_is_corrected(project):
    message = path_problem(str(project / "raw" / "prices.csv"), project)
    assert "write 'raw/prices.csv'" in message


@pytest.mark.parametrize("rel", ["useful-views/a:b.parquet", "raw/CON.csv", "raw/x<1>.csv", "raw/dir./x.csv"])
def test_names_impossible_on_windows_are_refused(rel):
    assert "Windows" in path_problem(rel)


@pytest.mark.parametrize("rel", ["raw/*.csv", "raw/2024-??.csv", "raw/*/data.csv"])
def test_wildcards_are_explained(rel):
    message = path_problem(rel)
    assert "wildcard" in message and "list each file" in message and "Windows" not in message


def test_new_recipe_rejects_trailing_newline(project):
    with pytest.raises(uvs.RecipeRefused):
        uvs.new_recipe("foo\n", root=project)


def test_catalog_escapes_newlines_in_column_names():
    data = {
        "resources": [{
            "name": "v", "path": "useful-views/v.parquet",
            "schema": {"fields": [{"name": "a\r\nb", "type": "string"}]},
            "producedBy": {"recipe": "r", "inputs": ["raw/x.csv"]},
        }],
        "recipes": [{"name": "r", "description": "d", "path": "recipes/r", "steps": []}],
    }
    text = render_catalog(data)
    assert "\r" not in text and "| a b | string |" in text


def test_raw_path_as_input_and_output_is_rule_7(project):
    rel = add_raw_csv(project)
    write_recipe(project, "a", inputs=[rel], outputs=[rel])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("a", root=project)
    assert err.value.rule == 7


def test_rule_7_names_an_unreadable_holder(project):
    rel = add_raw_csv(project)
    write_recipe(project, "a", inputs=[rel], outputs=["useful-views/v.parquet"])
    uvs.run("a", root=project)
    write(project, "recipes/a/recipe.toml", "this is = = not toml\n")
    write_recipe(project, "b", inputs=[rel], outputs=["useful-views/v.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err, pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("b", root=project)
    assert "recipes/a/recipe.toml cannot be read" in str(err.value)


def test_rule_5_explains_a_view_only_a_deleted_recipe_recorded(project):
    import shutil

    rel = add_raw_csv(project)
    write_recipe(project, "up", inputs=[rel], outputs=["useful-views/up.parquet"])
    uvs.run("up", root=project)
    shutil.rmtree(project / "recipes" / "up")
    write_recipe(project, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("down", root=project)
    assert err.value.rule == 5 and "useful-views sync" in str(err.value)


def test_unrecorded_two_recipe_cycle_is_reported_as_a_cycle(project):
    write_recipe(project, "a", inputs=["useful-views/b.parquet"], outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"])
    for name in ("a", "b"):
        with pytest.raises(uvs.RecipeRefused) as err:
            uvs.run(name, root=project)
        assert err.value.rule == 6


def test_gitignore_with_bom_is_not_reported_as_incomplete(tmp_path):
    import warnings

    folder = tmp_path / "p"
    folder.mkdir()
    (folder / ".gitignore").write_bytes(b"\xef\xbb\xbfraw/*\r\n!raw/.gitkeep\r\nwork/\r\nuseful-views/*.parquet\r\n")  # old lines, BOM, CRLF
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        uvs.init(folder)
    assert not [w for w in caught if issubclass(w.category, uvs.UsefulViewsWarning)]


def test_damaged_parent_project_refuses_init(project):
    (project / "datapackage.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(uvs.ProjectError):
        uvs.init(project / "notebooks")


def test_failure_after_steps_removes_outputs(project, monkeypatch):
    rel = add_raw_csv(project)
    write_recipe(project, "a", inputs=[rel], outputs=["useful-views/a.parquet"])
    import useful_views.runner as runner

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "describe_view", boom)
    with pytest.raises(KeyboardInterrupt):
        uvs.run("a", root=project)
    assert not (project / "useful-views" / "a.parquet").exists()


CSV_SHAPES = {
    "hex_integers": b"id,code\n1,0x1F\n2,12\n",
    "padded_integers": b"id,n\n1, 12\n2,13 \n",
    "year_zero_date": b"id,d\n1,0000-01-01\n2,2020-01-01\n",
    "padded_date": b"id,d\n1,2020-01-01 \n2,2021-02-03\n",
    "plain": b"a,b\n1,2.5\n",
    "bom": b"\xef\xbb\xbfa,b\n1,2\n",
    "na": b"a,b\n1,NA\n2,3\n",
    "trailing_blank": b"a,b\n1,2\n\n",
    "blank_mid": b"a,b\n1,2\n\n3,4\n",
    "excel_blank_row": b"a,b\n1,2\n,\n3,4\n",
    "header_space": b"year, gdp\n1,2\n",
    "dup_header_space": b"a,a \n1,2\n",
    "semicolon": b"a;b\n1;2\n",
    "tab": b"a\tb\n1\t2\n",
    "hhmm": b"t\n08:30\n09:15\n",
    "hhmmss": b"t\n08:30:00\n",
    "dt_short": b"d\n2020-01-01 10:00\n",
    "dt_full": b"d\n2020-01-01 10:00:00\n",
    "dt_t": b"d\n2020-01-01T10:00:00\n",
    "dt_z": b"d\n2020-01-01T10:00:00Z\n",
    "dt_offset": b"d\n2020-01-01T10:00:00+02:00\n",
    "date_and_datetime": b"d\n2020-01-01\n2020-01-02 10:00:00\n",
    "date": b"d\n2020-01-01\n",
    "bool": b"x\ntrue\nfalse\n",
    "quoted_comma": b'a,b\n"x, y",2\n',
    "crlf": b"a,b\r\n1,2\r\n",
    "single_col_blank": b"a\n1\n\n2\n",
    "header_only": b"a,b\n",
    "big_int": b"a\n99999999999999999999\n",
    "nan_inf": b"a\n1.5\nNaN\ninf\n",
    "percent": b"a\n5%\n",
    "thousands": b'a\n"1,000"\n',
}


def test_every_common_csv_shape_gets_a_schema_that_validates(tmp_path):
    """Whatever schema is recorded for a raw CSV, frictionless validate passes it."""
    import json

    (tmp_path / "raw").mkdir()
    resources = []
    for name, data in CSV_SHAPES.items():
        rel = f"raw/{name}.csv"
        (tmp_path / rel).write_bytes(data)
        resources.append(describe_raw(tmp_path, rel))
    (tmp_path / "datapackage.json").write_text(json.dumps({"name": "x", "resources": resources}))
    proc = validate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_root_argument_expands_home(project, monkeypatch):
    monkeypatch.setenv("HOME", str(project.parent))
    monkeypatch.setenv("USERPROFILE", str(project.parent))
    assert uvs.check(root=f"~/{project.name}") == []


def test_values_frictionless_rejects_are_recorded_as_strings(tmp_path):
    (tmp_path / "raw").mkdir()

    def types(name: str) -> dict:
        (tmp_path / f"raw/{name}.csv").write_bytes(CSV_SHAPES[name])
        return {f["name"]: f["type"] for f in describe_raw(tmp_path, f"raw/{name}.csv")["schema"]["fields"]}

    assert types("hex_integers") == {"id": "integer", "code": "string"}
    assert types("padded_integers") == {"id": "integer", "n": "integer"}
    assert types("year_zero_date")["d"] == "string"
    assert types("padded_date")["d"] == "string"
