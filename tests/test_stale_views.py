"""check and sync use the recorded fingerprints to find a recipe made from an
older version of a view it reads (`input-data-changed`), and sync skips a
recipe whose inputs were made again with the same data."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

import useful_views as uvs

from conftest import add_raw_csv, write, write_recipe
from test_record_fingerprints import DOWN, DOWN_STEP, UP, as_written_by_0_1_0, chain, views


def other_data(root, recipe: str = "up", view: str = UP, values=(7, 8, 9)) -> None:
    """Make a recipe's only step write other data (its record is unchanged)."""
    write(root, f"recipes/{recipe}/01_make.py",
          "# Write a small table to each output.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
          f"pq.write_table(pa.table({{'id': {list(values)}, 'label': ['x', 'y', 'z']}}), {view!r})\n")


def test_a_view_made_from_an_older_input_is_out_of_date(driver, project):
    chain(project)
    uvs.sync(root=project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning, match="with different data") as caught:
        result = uvs.run("up", root=project)
    # down's record shows it read up's old data, so a plain sync is enough.
    assert result.warnings[-1].endswith("recipes/down. Bring them up to date with: useful-views sync")
    assert len(caught) == 1

    out = driver.check()
    assert out.kinds == ["out-of-date"]
    [finding] = uvs.check(root=project)
    assert (finding.recipe, finding.reason) == ("down", "input-data-changed")
    assert f"{UP} was made again with different data since it ran" in finding.message
    assert "sorts its rows" in finding.message

    out = driver.sync()
    assert out.ok, out.message
    assert uvs.check(root=project) == []
    v = views(project)
    assert v[DOWN]["producedBy"]["inputFingerprints"] == {UP: v[UP]["fingerprint"]}


def test_sync_runs_only_what_is_out_of_date(project):
    chain(project)
    uvs.sync(root=project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    result = uvs.sync(root=project)
    assert result.ran == ("down",) and result.reasons == {"down": "input-data-changed"}
    assert result.skipped == ()


def test_check_reason_in_json(project):
    chain(project)
    uvs.sync(root=project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    done = subprocess.run([sys.executable, "-m", "useful_views", "-C", str(project), "check", "--json"],
                          capture_output=True, text=True)
    assert done.returncode == 1
    [finding] = json.loads(done.stdout)["findings"]
    assert (finding["kind"], finding["reason"], finding["recipe"]) == ("out-of-date", "input-data-changed", "down")


MIDDLE = "useful-views/middle.parquet"


def three(root, middle_step: str) -> None:
    """up -> middle -> down."""
    add_raw_csv(root, "raw/src.csv")
    write_recipe(root, "up", inputs=["raw/src.csv"], outputs=[UP])
    write_recipe(root, "middle", inputs=[UP], outputs=[MIDDLE], steps={"01_make.py": middle_step})
    write_recipe(root, "down", inputs=[MIDDLE], outputs=[DOWN], steps={"01_add.py": DOWN_STEP.replace(UP, MIDDLE)})
    uvs.sync(root=root)


def test_a_change_passes_down_a_chain(project):
    three(project, DOWN_STEP.replace(DOWN, MIDDLE))  # middle copies up's data and adds a column
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    reasons = {f.recipe: f.reason for f in uvs.check(root=project)}
    assert reasons == {"middle": "input-data-changed", "down": "upstream"}
    result = uvs.sync(root=project)
    assert result.ran == ("middle", "down") and result.skipped == ()


def test_a_chain_stops_where_the_data_comes_out_the_same(project):
    # middle writes the same table whatever up holds
    three(project, "# Count nothing.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
                   f"pq.write_table(pa.table({{'id': [1, 2, 3]}}), {MIDDLE!r})\n")
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    result = uvs.sync(root=project)
    assert result.ran == ("middle",) and result.skipped == ("down",)
    assert uvs.check(root=project) == []


def test_sync_all_never_skips(project):
    chain(project)
    uvs.sync(root=project)
    result = uvs.sync(root=project, all=True)
    assert result.ran == ("up", "down") and result.skipped == ()


def test_records_from_before_fingerprints_are_never_taken_for_stale(project):
    chain(project)
    uvs.sync(root=project)
    as_written_by_0_1_0(project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning) as caught:
        result = uvs.run("up", root=project)
    # down's record cannot show what it read, so sync cannot see it: say sync --all.
    assert any("useful-views sync --all" in w and "recipes/down" in w for w in result.warnings)
    with pytest.warns(uvs.UsefulViewsWarning, match="no fingerprint yet"):
        assert uvs.check(root=project) == []
    assert caught


def test_a_view_the_rules_do_not_cover_is_never_taken_for_stale(project):
    add_raw_csv(project, "raw/src.csv")
    nested = "useful-views/nested.parquet"
    lists = ("# Write a list in each cell.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
             f"pq.write_table(pa.table({{'tags': [['a'], ['b']]}}), {nested!r})\n")
    write_recipe(project, "lists", inputs=["raw/src.csv"], outputs=[nested], steps={"01_make.py": lists})
    write_recipe(project, "reader", inputs=[nested], outputs=[DOWN], steps={"01_count.py": (
        "# Count the rows.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
        f"pq.write_table(pa.table({{'n': [pq.read_table({nested!r}).num_rows]}}), {DOWN!r})\n")})
    uvs.sync(root=project)
    write(project, "recipes/lists/01_make.py", lists.replace("['b']", "['c']"))
    with pytest.warns(uvs.UsefulViewsWarning, match="useful-views sync --all"):
        uvs.run("lists", root=project)  # it cannot tell, so it says what to do
    assert uvs.check(root=project) == []


def test_fingerprints_from_other_rules_are_never_compared(project):
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    view = next(r for r in data["resources"] if r["path"] == DOWN)
    view["producedBy"]["inputFingerprints"][UP] = "datahash2:sha256:" + "a" * 64
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    assert uvs.check(root=project) == []  # not taken for a change
    # Nor for no change: when up runs again, down runs too.
    write(project, "recipes/up/recipe.toml",
          f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    assert uvs.sync(root=project).ran == ("up", "down")


def test_cookbook_check_fails_without_the_data(project):
    """In CI there is only the git checkout: no raw files and no views. The
    records alone show down was made from an older up."""
    chain(project)
    uvs.sync(root=project)
    uvs.cookbook(root=project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    for path in [project / UP, project / DOWN, project / "raw" / "src.csv"]:
        path.unlink()
    result = uvs.cookbook(root=project, check=True)
    assert not result.up_to_date
    assert [(f.recipe, f.reason) for f in result.findings] == [("down", "input-data-changed")]
    done = subprocess.run([sys.executable, "-m", "useful_views", "-C", str(project), "cookbook", "--check"],
                          capture_output=True, text=True)
    assert done.returncode == 1 and "sorts its rows" in done.stdout + done.stderr
