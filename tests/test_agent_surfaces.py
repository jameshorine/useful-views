"""What scripts and AI tools rely on: the rules in the package, JSON output,
-C, structured errors, and check/sync agreeing on what is wrong."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

import useful_views as uvs

from conftest import add_raw_csv, snapshot, write, write_recipe

PRINTING_STEP = (
    "# Print something, then write the view.\n"
    "import pyarrow as pa, pyarrow.parquet as pq\n"
    "print('hello from the step')\n"
    "pq.write_table(pa.table({'x': [1]}), 'useful-views/a.parquet')\n"
)


def cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "useful_views", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8")


def simple(project, steps=None):
    rel = add_raw_csv(project)
    write_recipe(project, "a", inputs=[rel], outputs=["useful-views/a.parquet"], steps=steps)


# --- rules ---------------------------------------------------------------------


def test_rules_are_in_the_package():
    assert sorted(uvs.RULES) == list(range(1, 9))
    assert all(r.title and r.detail for r in uvs.RULES.values())


def test_rules_command_text_and_json(tmp_path):
    out = cli("rules", cwd=tmp_path)
    assert out.returncode == 0 and "Rule 7: Outputs are allowed and unique." in out.stdout
    data = json.loads(cli("rules", "--json", cwd=tmp_path).stdout)
    assert [r["number"] for r in data["rules"]] == list(range(1, 9))


def test_a_refusal_names_its_rule(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["work/a.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.run("a", root=project)
    assert err.value.rule == 7 and err.value.rule_title == uvs.RULES[7].title
    assert "(Rule 7: Outputs are allowed and unique." in str(err.value)
    assert err.value.recipe == "a"


def test_help_lists_exit_codes(tmp_path):
    for args in (["--help"], ["check", "--help"], ["sync", "--help"]):
        assert "3    refused or failed" in cli(*args, cwd=tmp_path).stdout


# --- -C ------------------------------------------------------------------------


def test_dash_c_runs_in_another_folder(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = cli("-C", str(project), "check", cwd=elsewhere)
    assert out.returncode == 0 and "No problems found." in out.stdout
    out = cli("-C", str(tmp_path / "missing"), "check", cwd=elsewhere)
    assert out.returncode == 2 and "cannot use -C" in out.stderr  # a usage error


# --- --json ----------------------------------------------------------------------


def test_json_success_outputs(project):
    simple(project, steps={"01_make.py": PRINTING_STEP})
    out = cli("check", "--json", cwd=project)
    data = json.loads(out.stdout)
    assert out.returncode == 1 and data["ok"] is False
    assert [f["kind"] for f in data["findings"]] == ["never-run"]
    out = cli("sync", "--json", cwd=project)
    data = json.loads(out.stdout)  # the step's own print must not break the JSON
    assert out.returncode == 0 and data == {"ok": True, "removed": [], "dropped": [], "deleted": [],
                                            "ran": ["a"], "warnings": []}
    assert "hello from the step" in out.stderr and "useful-views: running recipes/a" in out.stderr
    data = json.loads(cli("run", "a", "--json", cwd=project).stdout)
    assert data == {"ok": True, "recipe": "a", "outputs": ["useful-views/a.parquet"], "warnings": []}
    assert json.loads(cli("check", "--json", cwd=project).stdout) == {"ok": True, "findings": []}


def test_json_refusal(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["work/a.parquet"])
    out = cli("run", "a", "--json", cwd=project)
    error = json.loads(out.stdout)["error"]
    assert out.returncode == 3
    assert error["type"] == "RecipeRefused" and error["rule"] == 7 and error["recipe"] == "a"
    assert error["path"] == "recipes/a/recipe.toml" and error["rule_title"] == uvs.RULES[7].title


def test_json_step_failure(project):
    simple(project, steps={"01_fail.py": "# Fail.\nraise SystemExit('boom')\n"})
    out = cli("run", "a", "--json", cwd=project)
    error = json.loads(out.stdout)["error"]
    assert out.returncode == 3 and error["type"] == "StepFailed"
    assert error["step"] == "recipes/a/01_fail.py" and error["returncode"] == 1 and "boom" in error["stderr"]


# --- structured errors -----------------------------------------------------------


def test_errors_carry_the_recipe(project):
    simple(project, steps={"01_fail.py": "# Fail.\nraise SystemExit('boom')\n"})
    with pytest.raises(uvs.StepFailed) as err:
        uvs.run("a", root=project)
    assert err.value.recipe == "a" and err.value.to_dict()["recipe"] == "a"
    write(project, "recipes/a/01_fail.py", "# Write nothing.\n")
    with pytest.raises(uvs.OutputError) as err:
        uvs.run("a", root=project)
    assert err.value.recipe == "a"
    assert err.value.problems == [("useful-views/a.parquet", "was not written")]


# --- sync refuses everything at once, before changing anything ------------------


def test_sync_lists_every_refusal_and_changes_nothing(project):
    rel = add_raw_csv(project)
    write_recipe(project, "gone", inputs=[rel], outputs=["useful-views/gone.parquet"])
    uvs.run("gone", root=project)
    shutil.rmtree(project / "recipes" / "gone")  # sync would remove this record...
    write_recipe(project, "b_out", inputs=[rel], outputs=["work/b.parquet"])  # rule 7
    uvs.new_recipe("c_todo", root=project)  # rule 1
    before = snapshot(project)
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert sorted((r.recipe, r.rule) for r in err.value.refusals) == [("b_out", 7), ("c_todo", 1)]
    assert "sync found 2 problems, so it changed nothing" in str(err.value)
    assert snapshot(project) == before  # ...but nothing changed


def test_sync_sends_progress_to_stderr(project, capfd):
    simple(project)
    uvs.sync(root=project)
    captured = capfd.readouterr()
    assert "useful-views: running recipes/a" in captured.err
    assert "useful-views: running" not in captured.out


# --- check reports what run and sync would do ------------------------------------


def test_check_reports_refusals_with_rule_and_recipe(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["work/a.parquet"])
    [finding] = uvs.check(root=project)
    assert (finding.kind, finding.rule, finding.recipe) == ("refused", 7, "a")
    assert "useful-views run" not in finding.message  # no advice to run what would be refused


def test_check_reports_out_of_date_recipes(project):
    simple(project)
    uvs.run("a", root=project)
    assert uvs.check(root=project) == []
    rel = "raw/data.csv"
    write(project, "recipes/a/recipe.toml",
          f'description = "Changed."\ninputs = ["{rel}"]\noutputs = ["useful-views/a.parquet"]\n')
    assert [f.kind for f in uvs.check(root=project)] == ["out-of-date"]
    assert uvs.sync(root=project).ran == ("a",)
    assert uvs.check(root=project) == []


def test_check_finds_what_sync_would_refuse_after_its_removals(project):
    """A view claimed by a deleted recipe's record is not a problem for a new
    recipe declaring it: sync removes that record first."""
    rel = add_raw_csv(project)
    write_recipe(project, "old", inputs=[rel], outputs=["useful-views/v.parquet"])
    uvs.run("old", root=project)
    shutil.rmtree(project / "recipes" / "old")
    write_recipe(project, "new", inputs=[rel], outputs=["useful-views/v.parquet"])
    assert "refused" not in [f.kind for f in uvs.check(root=project)]
    assert uvs.sync(root=project).ran == ("new",)


# --- review regressions: the plan replays the run order exactly ------------------


def test_rule_8_is_judged_against_the_records_at_each_recipes_turn(project):
    """a's old record still reads raw/foo.csv; new recipe b makes
    useful-views/foo.parquet and runs before a. When b runs, a's old record
    clashes with it, so sync must refuse up front, not partway."""
    add_raw_csv(project, "raw/foo.csv")
    write_recipe(project, "a", inputs=["raw/foo.csv"], outputs=["useful-views/a.parquet"])
    uvs.run("a", root=project)
    write_recipe(project, "b", inputs=["raw/data.csv"], outputs=["useful-views/foo.parquet"])
    add_raw_csv(project)
    write_recipe(project, "a", inputs=["useful-views/foo.parquet"], outputs=["useful-views/a.parquet"])
    before = snapshot(project)
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert [(r.recipe, r.rule) for r in err.value.refusals] == [("b", 8)]
    assert snapshot(project) == before
    refused = [(f.recipe, f.rule) for f in uvs.check(root=project) if f.kind == "refused"]
    assert refused == [("b", 8)]


def test_a_name_clash_is_blamed_only_on_the_recipe_that_has_it(project):
    add_raw_csv(project)
    add_raw_csv(project, "raw/c.csv")
    write_recipe(project, "a", inputs=["raw/data.csv"], outputs=["useful-views/a.parquet"])
    write_recipe(project, "c", inputs=["raw/c.csv"], outputs=["useful-views/c.parquet"])
    write_recipe(project, "e", inputs=["raw/data.csv"], outputs=["useful-views/e.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert [(r.recipe, r.rule) for r in err.value.refusals] == [("c", 8)]


def test_check_reports_a_recipe_that_replaced_all_its_outputs(project):
    add_raw_csv(project)
    write_recipe(project, "a", inputs=["raw/data.csv"], outputs=["useful-views/old.parquet"])
    uvs.run("a", root=project)
    write_recipe(project, "a", inputs=["raw/data.csv"], outputs=["useful-views/new.parquet"])
    findings = uvs.check(root=project)
    assert [f.kind for f in findings] == ["out-of-date"]
    assert "outputs changed" in findings[0].message


def test_a_cycle_does_not_hide_other_problems(project):
    write_recipe(project, "c1", inputs=["useful-views/c2.parquet"], outputs=["useful-views/c1.parquet"])
    write_recipe(project, "c2", inputs=["useful-views/c1.parquet"], outputs=["useful-views/c2.parquet"])
    write_recipe(project, "z", inputs=["raw/absent.csv"], outputs=["useful-views/z.parquet"])
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert sorted((r.recipe, r.rule) for r in err.value.refusals) == [("c1", 6), ("c2", 6), ("z", 4)]


def test_reading_its_own_view_is_reported_once(project):
    write_recipe(project, "s", inputs=["useful-views/s.parquet"], outputs=["useful-views/s.parquet"])
    findings = uvs.check(root=project)
    assert [(f.kind, f.rule) for f in findings] == [("refused", 6)]


@pytest.mark.parametrize("content", [b"not parquet at all", b""])
def test_bad_raw_parquet_is_refused_before_sync_changes_anything(project, content):
    (project / "raw" / "bad.parquet").write_bytes(content)
    write_recipe(project, "a", inputs=["raw/bad.parquet"], outputs=["useful-views/a.parquet"])
    before = snapshot(project)
    with pytest.raises(uvs.RecipeRefused) as err:
        uvs.sync(root=project)
    assert err.value.rule == 4
    assert snapshot(project) == before


def test_a_sync_with_nothing_to_do_leaves_files_untouched(project):
    simple(project)
    uvs.sync(root=project)
    files = [project / "datapackage.json", project / "useful-views" / "README.md"]
    stamps = [f.stat().st_mtime_ns for f in files]
    assert uvs.sync(root=project).ran == ()
    assert [f.stat().st_mtime_ns for f in files] == stamps


def test_check_shows_the_warnings_sync_would_show(project):
    simple(project)
    with open(project / "recipes" / "a" / "recipe.toml", "a", encoding="utf-8") as f:
        f.write('extra = "x"\n')
    with pytest.warns(uvs.UsefulViewsWarning, match="unknown key `extra`"):
        uvs.check(root=project)


# --- review regressions: the command line ------------------------------------------


@pytest.mark.parametrize("args", [["check", "--json", "--bogus"], ["bogus", "--json"],
                                  ["--json", "-C", "/nonexistent-folder", "check"]])
def test_json_usage_errors(tmp_path, args):
    out = cli(*args, cwd=tmp_path)
    assert out.returncode == 2
    assert json.loads(out.stdout)["error"]["type"] == "UsageError"


def test_json_and_dash_c_after_the_command(project, tmp_path):
    out = cli("check", "--json", "-C", str(project), cwd=tmp_path)
    assert out.returncode == 0 and json.loads(out.stdout) == {"ok": True, "findings": []}


def test_json_is_ascii_on_any_console(project):
    out = subprocess.run([sys.executable, "-m", "useful_views", "new-recipe", "grün", "--json"], cwd=project,
                         capture_output=True, env={**__import__("os").environ, "PYTHONIOENCODING": "cp1252"})
    assert json.loads(out.stdout.decode("ascii"))["error"]["type"] == "RecipeRefused"


def test_interrupt_under_json(project, monkeypatch, capsys):
    from useful_views import cli as cli_module

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "check", interrupted)
    assert cli_module.main(["-C", str(project), "check", "--json"]) == 130
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "Interrupted"


def test_main_restores_the_working_directory(project, tmp_path, monkeypatch):
    from useful_views import cli as cli_module

    monkeypatch.chdir(tmp_path)
    cli_module.main(["-C", str(project), "check"])
    import os
    assert os.getcwd() == str(tmp_path)
