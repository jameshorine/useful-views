"""What a run does on disk, how steps are launched, and what a failed run
leaves behind ("What run does" in the README)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import useful_views as uvs

from conftest import add_raw_csv, generated, make_views_step, write, write_recipe

VIEW = "useful-views/out.parquet"


# --- helpers ----------------------------------------------------------------


def step(description: str, body: str = "") -> str:
    """Code for a step file: a top comment, then `body`."""
    return f"# {description}\n" + textwrap.dedent(body).lstrip("\n")


def assert_failed(out, driver, exc_type, *fragments: str) -> None:
    """The run failed: the API raised `exc_type`, the CLI exited 3, and the
    message (or the CLI's stderr) contains each fragment."""
    assert not out.ok
    assert out.exit_code == 3
    if driver.name == "api":
        assert isinstance(out.error, exc_type), repr(out.error)
    for fragment in fragments:
        assert fragment in out.message, f"{fragment!r} not in:\n{out.message}"


def datapackage(root: Path) -> dict:
    return json.loads((root / "datapackage.json").read_text(encoding="utf-8"))


def record_of(root: Path, name: str) -> dict:
    (rec,) = [r for r in datapackage(root)["recipes"] if r["name"] == name]
    return rec


def views_of(root: Path, name: str) -> list[dict]:
    return [r for r in datapackage(root)["resources"] if r.get("producedBy", {}).get("recipe") == name]


def catalog_sections(root: Path) -> dict[str, str]:
    text = (root / "useful-views" / "README.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)[1:]
    return {p.split("\n", 1)[0]: p for p in parts}


def simple_recipe(root: Path, name: str = "r", outputs=(VIEW,), **kw) -> Path:
    raw = add_raw_csv(root)
    return write_recipe(root, name, inputs=[raw], outputs=list(outputs), **kw)


# --- a failing step ------------------------------------------------------


def test_failing_step_leaves_generated_files_unchanged(driver, project):
    simple_recipe(project)
    assert driver.run("r").ok
    before = generated(project)
    write(project, "recipes/r/02_boom.py", step("Fail on purpose.", "raise SystemExit(5)\n"))
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "02_boom.py")
    assert generated(project) == before


def test_failing_step_on_first_run_leaves_init_files_unchanged(driver, project):
    before = generated(project)
    simple_recipe(project, steps={"01_boom.py": step("Fail on purpose.", "raise SystemExit(1)\n")})
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "01_boom.py")
    assert generated(project) == before
    assert datapackage(project)["recipes"] == []


def test_step_failed_names_step_file_exit_code_and_stderr_tail(driver, project):
    body = """
        import sys
        for i in range(60):
            sys.stderr.write(f"err-{i:03d}\\n")
        sys.exit(7)
    """
    simple_recipe(project, steps={"01_noisy.py": step("Print a lot of errors.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "01_noisy.py", "err-059", "err-010")
    assert re.search(r"\b7\b", out.message), out.message
    if driver.name == "api":
        # Only the last 50 lines of stderr go into the message.
        assert "err-009" not in str(out.error)
        assert "recipes/r/01_noisy.py" in str(out.error)


def test_step_failed_message_includes_traceback_text(driver, project):
    body = "raise ValueError('the-specific-problem')\n"
    simple_recipe(project, steps={"01_bad.py": step("Raise an error.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "01_bad.py", "ValueError", "the-specific-problem")


def test_failed_run_stops_at_first_failing_step(driver, project):
    steps = {
        "01_ok.py": step("Write a work file.", "open('work/r/one.txt', 'w').write('1')\n"),
        "02_boom.py": step("Fail.", "raise SystemExit(1)\n"),
        "03_after.py": step("Must never run.", "open('work/r/three.txt', 'w').write('3')\n" + make_views_step([VIEW])),
    }
    simple_recipe(project, steps=steps)
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "02_boom.py")
    assert not (project / "work/r/three.txt").exists()
    # Work files stay for inspection after a failure.
    assert (project / "work/r/one.txt").read_text() == "1"


def test_failed_run_removes_declared_outputs(driver, project):
    outputs = [VIEW, "useful-views/second.parquet"]
    simple_recipe(project, outputs=outputs)
    assert driver.run("r").ok
    assert all((project / o).exists() for o in outputs)
    # Now the outputs get written again, then a later step fails.
    write(project, "recipes/r/02_boom.py", step("Fail after writing outputs.", "raise SystemExit(1)\n"))
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "02_boom.py")
    for o in outputs:
        assert not (project / o).exists(), o


def test_failed_run_does_not_touch_other_recipes_views(driver, project):
    simple_recipe(project, "a", outputs=["useful-views/a.parquet"])
    simple_recipe(project, "b", outputs=["useful-views/b.parquet"])
    assert driver.run("a").ok
    assert driver.run("b").ok
    a_view = (project / "useful-views/a.parquet").read_bytes()
    write(project, "recipes/b/02_boom.py", step("Fail.", "raise SystemExit(1)\n"))
    assert not driver.run("b").ok
    assert (project / "useful-views/a.parquet").read_bytes() == a_view
    assert not (project / "useful-views/b.parquet").exists()


def test_module_not_found_names_interpreter(driver, project):
    body = "import no_such_module_for_useful_views_tests\n"
    simple_recipe(project, steps={"01_imp.py": step("Import a missing package.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.StepFailed, "01_imp.py", "no_such_module_for_useful_views_tests", sys.executable)


# --- outputs -------------------------------------------------------------


def test_missing_output_is_reported_by_name(driver, project):
    outputs = [VIEW, "useful-views/forgotten.parquet"]
    before = generated(project)
    simple_recipe(project, outputs=outputs, steps={"01_make.py": make_views_step([VIEW])})
    out = driver.run("r")
    assert_failed(out, driver, uvs.OutputError, "useful-views/forgotten.parquet")
    if driver.name == "api":
        assert out.error.paths == ["useful-views/forgotten.parquet"]
    assert generated(project) == before
    # Any failure after the old views are cleared deletes every declared output.
    assert not (project / VIEW).exists()


def test_empty_output_fails(driver, project):
    before = generated(project)
    body = f"open({VIEW!r}, 'wb').close()\n"
    simple_recipe(project, steps={"01_empty.py": step("Write an empty file.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.OutputError, VIEW)
    if driver.name == "api":
        assert out.error.paths == [VIEW]
    assert generated(project) == before
    assert not (project / VIEW).exists()


def test_non_parquet_output_fails(driver, project):
    before = generated(project)
    body = f"open({VIEW!r}, 'w').write('id,label\\n1,a\\n')\n"
    simple_recipe(project, steps={"01_csv.py": step("Write CSV text under a parquet name.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.OutputError, VIEW)
    if driver.name == "api":
        assert out.error.paths == [VIEW]
    assert generated(project) == before
    assert not (project / VIEW).exists()


def test_output_that_is_a_directory_fails(driver, project):
    before = generated(project)
    body = f"import os\nos.makedirs({VIEW!r})\n"
    simple_recipe(project, steps={"01_dir.py": step("Make a folder where the view goes.", body)})
    out = driver.run("r")
    assert_failed(out, driver, uvs.OutputError, VIEW)
    assert generated(project) == before


def test_leftover_output_does_not_satisfy_rule_12(driver, project):
    simple_recipe(project)
    assert driver.run("r").ok
    before = generated(project)
    assert (project / VIEW).exists()
    # The step now writes nothing; the view from the first run must not count.
    write(project, "recipes/r/01_make.py", step("Write nothing at all.", "pass\n"))
    out = driver.run("r")
    assert_failed(out, driver, uvs.OutputError, VIEW)
    if driver.name == "api":
        assert out.error.paths == [VIEW]
    assert generated(project) == before
    assert not (project / VIEW).exists()


def test_outputs_are_deleted_before_the_first_step(driver, project):
    simple_recipe(project)
    assert driver.run("r").ok
    body = f"import os, sys\nif os.path.exists({VIEW!r}):\n    sys.exit('leftover view still present')\n" + (
        make_views_step([VIEW]).split("\n", 1)[1]
    )
    write(project, "recipes/r/01_make.py", step("Check the view is gone, then write it.", body))
    out = driver.run("r")
    assert out.ok, out.message


# --- work folder ----------------------------------------------------------


def test_work_folder_is_emptied_before_each_run(driver, project):
    simple_recipe(project)
    write(project, "work/r/leftover.txt", "old\n")
    write(project, "work/r/sub/deep.txt", "old\n")
    body = (
        "import os, sys\n"
        "if os.listdir('work/r'):\n"
        "    sys.exit('work/r not empty: ' + repr(os.listdir('work/r')))\n"
        "open('work/r/fresh.txt', 'w').write('new')\n"
    )
    write(project, "recipes/r/00_check.py", step("Check the work folder is empty.", body))
    out = driver.run("r")
    assert out.ok, out.message
    assert not (project / "work/r/leftover.txt").exists()
    assert not (project / "work/r/sub").exists()
    assert (project / "work/r/fresh.txt").exists()


def test_work_folder_is_created_if_missing(driver, project):
    shutil.rmtree(project / "work", ignore_errors=True)
    body = "open('work/r/tmp.txt', 'w').write('x')\n" + make_views_step([VIEW]).split("\n", 1)[1]
    simple_recipe(project, steps={"01_make.py": step("Use the work folder.", body)})
    out = driver.run("r")
    assert out.ok, out.message
    assert (project / "work/r/tmp.txt").is_file()


def test_run_leaves_other_recipes_work_alone(driver, project):
    simple_recipe(project, "a", outputs=["useful-views/a.parquet"])
    write(project, "work/b/keep.txt", "keep\n")
    assert driver.run("a").ok
    assert (project / "work/b/keep.txt").read_text() == "keep\n"


# --- how steps run --------------------------------------------------------


def test_steps_run_in_numeric_order(driver, project):
    def logger(tag):
        return step(f"Log {tag}.", f"open('work/r/order.txt', 'a').write({tag!r} + '\\n')\n")

    steps = {
        "10_alpha.py": logger("10"),
        "02_zulu.py": logger("02"),
        "01_mike.py": logger("01"),
        "11_make.py": make_views_step([VIEW]),
    }
    simple_recipe(project, steps=steps)
    out = driver.run("r")
    assert out.ok, out.message
    assert (project / "work/r/order.txt").read_text().split() == ["01", "02", "10"]
    assert [s["name"] for s in record_of(project, "r")["steps"]] == ["mike", "zulu", "alpha", "make"]


def test_step_runs_with_project_root_cwd_and_same_interpreter(driver, project, tmp_path):
    body = f"""
        import os, sys
        problems = []
        if not os.path.samefile(os.getcwd(), {str(project)!r}):
            problems.append('cwd ' + os.getcwd())
        if sys.executable != {sys.executable!r}:
            problems.append('python ' + sys.executable)
        if os.environ.get('PYTHONIOENCODING', '').lower().replace('-', '') != 'utf8':
            problems.append('PYTHONIOENCODING ' + repr(os.environ.get('PYTHONIOENCODING')))
        open('work/r/env.txt', 'w').write(os.getcwd() + '\\n' + sys.executable + '\\n')
        if problems:
            sys.exit('; '.join(problems))
    """
    steps = {"01_env.py": step("Check where and how the step runs.", body), "02_make.py": make_views_step([VIEW])}
    simple_recipe(project, steps=steps)
    # The driver runs from inside the project; run from a subfolder too.
    (project / "notebooks").mkdir()
    driver.cwd = project / "notebooks"
    out = driver.run("r")
    assert out.ok, out.message
    cwd, exe = (project / "work/r/env.txt").read_text().splitlines()
    assert os.path.samefile(cwd, project)
    assert exe == sys.executable


def test_step_sees_relative_paths_from_project_root(driver, project):
    raw = add_raw_csv(project, "raw/sub/data.csv")
    body = f"""
        import pyarrow.csv as pc, pyarrow.parquet as pq
        pq.write_table(pc.read_csv({raw!r}), {VIEW!r})
    """
    write_recipe(project, "r", inputs=[raw], outputs=[VIEW], steps={"01_copy.py": step("Copy the raw file.", body)})
    out = driver.run("r")
    assert out.ok, out.message
    assert pq.read_table(project / VIEW).column_names == ["id", "value"]


def _run_with_open_stdin(project: Path, how: str) -> subprocess.CompletedProcess:
    """Run recipe r in a child Python whose own stdin is an open pipe that
    never receives data, so a step that inherited it would hang."""
    if how == "api":
        cmd = [sys.executable, "-c", f"import useful_views as uvs; uvs.run('r', root={str(project)!r})"]
    else:
        cmd = [sys.executable, "-m", "useful_views", "run", "r"]
    out_path = project.parent / f"stdin-{how}.out"
    err_path = project.parent / f"stdin-{how}.err"
    with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
        proc = subprocess.Popen(
            cmd,
            cwd=project,
            stdin=subprocess.PIPE,  # held open and never written to
            stdout=out_f,
            stderr=err_f,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        try:
            code = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("the run hung: a step waiting on input() was not given a closed stdin")
        finally:
            proc.stdin.close()
    return subprocess.CompletedProcess(
        cmd, code, out_path.read_text(encoding="utf-8", errors="replace"),
        err_path.read_text(encoding="utf-8", errors="replace"),
    )


@pytest.mark.parametrize("how", ["api", "cli"])
def test_stdin_is_closed_so_input_fails_instead_of_hanging(project, how):
    body = "answer = input('type something: ')\n"
    simple_recipe(project, steps={"01_ask.py": step("Ask for input.", body)})
    before = generated(project)
    result = _run_with_open_stdin(project, how)
    assert result.returncode != 0
    assert "01_ask.py" in result.stderr
    assert "EOFError" in result.stderr
    if how == "cli":
        assert result.returncode == 3
    assert generated(project) == before


def test_step_stdout_and_stderr_reach_the_caller(driver, project, capsys):
    body = """
        import sys
        print('HELLO-FROM-STEP \u00e9')
        sys.stderr.write('NOTE-ON-STDERR\\n')
    """
    steps = {"01_talk.py": step("Talk.", body), "02_make.py": make_views_step([VIEW])}
    simple_recipe(project, steps=steps)
    out = driver.run("r")
    assert out.ok, out.message
    if driver.name == "api":
        captured = capsys.readouterr()
        stdout, stderr = captured.out, captured.err
    else:
        stdout, stderr = out.stdout, out.stderr
    assert "HELLO-FROM-STEP \u00e9" in stdout
    assert "NOTE-ON-STDERR" in stderr
    assert "HELLO-FROM-STEP" not in stderr


def test_failing_step_stderr_is_relayed_live(driver, project, capsys):
    body = "import sys\nsys.stderr.write('LIVE-ERR\\n')\nsys.exit(2)\n"
    simple_recipe(project, steps={"01_bad.py": step("Fail loudly.", body)})
    out = driver.run("r")
    assert not out.ok
    stderr = capsys.readouterr().err if driver.name == "api" else out.stderr
    assert "LIVE-ERR" in stderr
    if driver.name == "api":
        assert "LIVE-ERR" in out.error.stderr


# --- one recipe only ------------------------------------------------------


def test_downstream_first_is_refused_and_upstream_never_runs(driver, project):
    up_body = "open('work/up/ran.txt', 'w').write('ran')\n" + make_views_step(["useful-views/up.parquet"]).split("\n", 1)[1]
    simple_recipe(project, "up", outputs=["useful-views/up.parquet"],
                  steps={"01_make.py": step("Make the upstream view.", up_body)})
    write_recipe(project, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])
    before = generated(project)
    out = driver.run("down")
    assert not out.ok
    assert out.exit_code == 3
    if driver.name == "api":
        assert isinstance(out.error, uvs.RecipeRefused)
    assert "recipes/up" in out.message
    assert not (project / "work/up/ran.txt").exists()
    assert not (project / "useful-views/up.parquet").exists()
    assert not (project / "useful-views/down.parquet").exists()
    assert generated(project) == before


def test_downstream_refused_when_upstream_view_exists_but_is_unrecorded(driver, project):
    simple_recipe(project, "up", outputs=["useful-views/up.parquet"])
    write_recipe(project, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])
    # A view file placed by hand does not make the upstream recipe recorded.
    pq.write_table(pa.table({"id": [1]}), project / "useful-views/up.parquet")
    before = generated(project)
    out = driver.run("down")
    assert not out.ok
    assert out.exit_code == 3
    if driver.name == "api":
        assert isinstance(out.error, uvs.RecipeRefused)
        assert out.error.rule == 5
    assert "recipes/up" in out.message
    assert not (project / "useful-views/down.parquet").exists()
    assert generated(project) == before


def test_downstream_run_does_not_rerun_upstream(driver, project):
    up_body = "open('work/up/count.txt', 'a').write('x')\n" + make_views_step(["useful-views/up.parquet"]).split("\n", 1)[1]
    simple_recipe(project, "up", outputs=["useful-views/up.parquet"],
                  steps={"01_make.py": step("Make the upstream view.", up_body)})
    write_recipe(project, "down", inputs=["useful-views/up.parquet"], outputs=["useful-views/down.parquet"])
    assert driver.run("up").ok
    up_view = (project / "useful-views/up.parquet").read_bytes()
    out = driver.run("down")
    assert out.ok, out.message
    assert (project / "work/up/count.txt").read_text() == "x"
    assert (project / "useful-views/up.parquet").read_bytes() == up_view
    assert (project / "useful-views/down.parquet").exists()


# --- idempotence ----------------------------------------------------------


def test_second_run_is_byte_identical(driver, project):
    simple_recipe(project, "a", outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"])
    assert driver.run("a").ok
    assert driver.run("b").ok
    first = generated(project)
    assert driver.run("a").ok
    assert generated(project) == first
    assert driver.run("b").ok
    assert generated(project) == first


# --- other recipes' records -----------------------------------------------


def _two_independent_recipes(driver, project):
    simple_recipe(project, "a", outputs=["useful-views/a.parquet"])
    simple_recipe(project, "b", outputs=["useful-views/b.parquet"])
    assert driver.run("a").ok
    assert driver.run("b").ok
    return record_of(project, "b"), views_of(project, "b"), catalog_sections(project)["b.parquet"]


def test_running_a_keeps_b_record_when_b_view_changed_on_disk(driver, project):
    b_record, b_views, b_section = _two_independent_recipes(driver, project)
    # Rewrite b's view with different columns.
    pq.write_table(pa.table({"other": pa.array([1.5], pa.float64()), "flag": [True]}),
                   project / "useful-views/b.parquet")
    assert driver.run("a").ok
    assert record_of(project, "b") == b_record
    assert json.dumps(views_of(project, "b")) == json.dumps(b_views)
    assert catalog_sections(project)["b.parquet"] == b_section


def test_running_a_keeps_b_record_when_b_view_deleted(driver, project):
    b_record, b_views, b_section = _two_independent_recipes(driver, project)
    (project / "useful-views/b.parquet").unlink()
    assert driver.run("a").ok
    assert record_of(project, "b") == b_record
    assert json.dumps(views_of(project, "b")) == json.dumps(b_views)
    assert catalog_sections(project)["b.parquet"] == b_section


def test_running_a_keeps_b_record_when_b_folder_edited(driver, project):
    b_record, b_views, _ = _two_independent_recipes(driver, project)
    write(project, "recipes/b/recipe.toml",
          'description = "A brand new description."\n'
          'inputs = ["raw/data.csv"]\noutputs = ["useful-views/b.parquet"]\n')
    write(project, "recipes/b/02_more.py", step("An extra step.", "pass\n"))
    assert driver.run("a").ok
    assert record_of(project, "b") == b_record
    assert json.dumps(views_of(project, "b")) == json.dumps(b_views)


def test_running_a_keeps_b_record_byte_identical_in_file(driver, project):
    _two_independent_recipes(driver, project)
    text = (project / "datapackage.json").read_text(encoding="utf-8")
    data = json.loads(text)
    b_rec_json = json.dumps([r for r in data["recipes"] if r["name"] == "b"], indent=2, ensure_ascii=False)
    (project / "useful-views/b.parquet").unlink()
    write(project, "recipes/a/02_more.py", step("Another step in a.", "pass\n"))
    assert driver.run("a").ok
    data2 = datapackage(project)
    assert json.dumps([r for r in data2["recipes"] if r["name"] == "b"], indent=2, ensure_ascii=False) == b_rec_json
    assert [s["name"] for s in record_of(project, "a")["steps"]] == ["make", "more"]


# --- recording --------------------------------------------------------------


def test_two_outputs_are_both_recorded_with_two_catalog_sections(driver, project):
    outputs = ["useful-views/zeta.parquet", "useful-views/alpha.parquet"]
    raw = add_raw_csv(project)
    write_recipe(project, "r", inputs=[raw], outputs=outputs)
    out = driver.run("r")
    assert out.ok, out.message
    assert record_of(project, "r")["outputs"] == outputs
    views = views_of(project, "r")
    # Views of one recipe are sorted by path.
    assert [v["path"] for v in views] == sorted(outputs)
    for v in views:
        assert v["producedBy"] == {"recipe": "r", "inputs": [raw]}
    sections = catalog_sections(project)
    assert list(sections) == ["alpha.parquet", "zeta.parquet"]
    for section in sections.values():
        assert "Test recipe r." in section
        assert "| id | integer |" in section
        assert "| label | string |" in section


def test_raw_file_shared_by_two_recipes(driver, project):
    raw = add_raw_csv(project)
    write_recipe(project, "a", inputs=[raw], outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=[raw], outputs=["useful-views/b.parquet"])
    assert driver.run("a").ok
    assert driver.run("b").ok
    data = datapackage(project)
    raws = [r for r in data["resources"] if r["path"] == raw]
    assert len(raws) == 1
    assert [f["name"] for f in raws[0]["schema"]["fields"]] == ["id", "value"]
    # The raw file changes; the most recent run's description wins.
    add_raw_csv(project, raw, "id,value,extra\n1,2.5,x\n")
    b_record = record_of(project, "b")
    assert driver.run("a").ok
    data = datapackage(project)
    raws = [r for r in data["resources"] if r["path"] == raw]
    assert len(raws) == 1
    assert [f["name"] for f in raws[0]["schema"]["fields"]] == ["id", "value", "extra"]
    assert record_of(project, "b") == b_record
    # Order: raw resources first, then views by recipe name.
    assert [r["path"] for r in data["resources"]] == [raw, "useful-views/a.parquet", "useful-views/b.parquet"]


def test_raw_file_kept_when_only_one_of_two_readers_is_rerun(driver, project):
    raw_a = add_raw_csv(project, "raw/a_src.csv")
    raw_b = add_raw_csv(project, "raw/b_src.csv")
    write_recipe(project, "a", inputs=[raw_a], outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=[raw_b], outputs=["useful-views/b.parquet"])
    assert driver.run("a").ok
    assert driver.run("b").ok
    # b's raw file disappears (fresh clone); running a keeps b's raw resource.
    b_raw = [r for r in datapackage(project)["resources"] if r["path"] == raw_b]
    (project / raw_b).unlink()
    assert driver.run("a").ok
    assert [r for r in datapackage(project)["resources"] if r["path"] == raw_b] == b_raw


@pytest.mark.parametrize("given", ["r", "recipes/r", "recipes/r/"])
def test_run_result_fields(project, given):
    simple_recipe(project, outputs=["useful-views/z.parquet", "useful-views/a.parquet"])
    result = uvs.run(given, root=project)
    assert isinstance(result, uvs.RunResult)
    assert result.recipe == "r"
    assert tuple(result.outputs) == ("useful-views/z.parquet", "useful-views/a.parquet")
    assert tuple(result.warnings) == ()


def test_run_result_returns_warnings(project):
    raw = add_raw_csv(project)
    write(project, "recipes/r/recipe.toml",
          f'description = "With an extra key."\ninputs = ["{raw}"]\noutputs = ["{VIEW}"]\nauthor = "me"\n')
    write(project, "recipes/r/01_make.py", make_views_step([VIEW]))
    with pytest.warns(uvs.UsefulViewsWarning) as caught:
        result = uvs.run("r", root=project)
    assert any("author" in w for w in result.warnings)
    assert any("author" in str(w.message) for w in caught)


def test_run_name_must_match_folder_case(driver, project):
    simple_recipe(project)
    before = generated(project)
    out = driver.run("R")
    assert not out.ok
    assert out.exit_code == 3
    assert not (project / VIEW).exists()
    assert generated(project) == before


def test_successful_run_records_recipe_and_writes_catalog(driver, project):
    raw = add_raw_csv(project)
    write_recipe(project, "r", inputs=[raw], outputs=[VIEW], description="Make the out view.")
    out = driver.run("r")
    assert out.ok, out.message
    rec = record_of(project, "r")
    assert rec == {
        "name": "r",
        "description": "Make the out view.",
        "path": "recipes/r",
        "inputs": [raw],
        "outputs": [VIEW],
        "steps": [{"name": "make", "path": "recipes/r/01_make.py",
                   "description": "Write a small table to each output."}],
    }
    text = (project / "datapackage.json").read_text(encoding="utf-8")
    assert not text.endswith("\n")
    catalog = (project / "useful-views/README.md").read_bytes()
    assert catalog.endswith(b"\n") and not catalog.endswith(b"\n\n")
    assert b"\r\n" not in catalog
    assert "out.parquet" in catalog_sections(project)
