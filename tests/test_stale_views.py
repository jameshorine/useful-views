"""check and sync use the recorded fingerprints to find a recipe made from an
older version of a view it reads (`input-data-changed`). A fingerprint that
comes out the same is never taken to mean a reader is up to date: a step can
see what a data hash leaves out (a time zone's name, a storage type, categories), so
sync runs every recipe downstream of one it runs, and run names the readers."""

from __future__ import annotations

import json
import subprocess
import sys
import warnings

import pytest

import useful_views as uvs

from conftest import add_raw_csv, write, write_recipe
from test_record_fingerprints import DOWN, DOWN_STEP, UP, as_written_by_0_1_0, chain, views


def ours(caught) -> list:
    """The useful-views warnings among those caught: other code may warn too
    (a file closed by the garbage collector, say)."""
    return [w for w in caught if issubclass(w.category, uvs.UsefulViewsWarning)]


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
    assert "recipes/down. Bring them up to date with: useful-views sync." in result.warnings[-1]
    assert len(ours(caught)) == 1

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
    assert result.ran == ("middle", "down")


def test_a_chain_goes_on_where_the_data_comes_out_the_same(project):
    # middle writes the same table whatever up holds; down still runs after it
    three(project, "# Count nothing.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
                   f"pq.write_table(pa.table({{'id': [1, 2, 3]}}), {MIDDLE!r})\n")
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    result = uvs.sync(root=project)
    assert result.ran == ("middle", "down")
    assert uvs.check(root=project) == []


EVENTS, DAILY = "useful-views/events.parquet", "useful-views/daily.parquet"

# events writes one instant; daily takes its calendar date in the column's time zone.
EVENTS_STEP = (
    "# Write one event time.\nimport datetime as dt, polars as pl\n"
    "frame = pl.DataFrame({'at': [dt.datetime(2024, 3, 1, 2)]}).with_columns(pl.col('at').dt.replace_time_zone('UTC'))\n"
    f"frame.write_parquet({EVENTS!r})\n"
)
DAILY_STEP = (
    "# Take each event's calendar date.\nimport polars as pl\n"
    f"pl.read_parquet({EVENTS!r}).select(pl.col('at').dt.date().alias('day')).write_parquet({DAILY!r})\n"
)


def day(root):
    import polars as pl

    return pl.read_parquet(root / DAILY)["day"].to_list()


def events_and_daily(root) -> str:
    """events -> daily, synced; returns events' fingerprint."""
    add_raw_csv(root, "raw/src.csv")
    write_recipe(root, "events", inputs=["raw/src.csv"], outputs=[EVENTS], steps={"01_make.py": EVENTS_STEP})
    write_recipe(root, "daily", inputs=[EVENTS], outputs=[DAILY], steps={"01_day.py": DAILY_STEP})
    uvs.sync(root=root)
    return views(root)[EVENTS]["fingerprint"]


def test_the_same_data_hash_never_lets_sync_skip_a_reader(project):
    """The same instants in another time zone have the same data hash, but
    daily's dates differ. sync must run daily, not call it up to date."""
    import datetime as dt

    before = events_and_daily(project)
    assert day(project) == [dt.date(2024, 3, 1)]
    write(project, "recipes/events/01_make.py",
          EVENTS_STEP.replace(".dt.replace_time_zone('UTC'))",
                              ".dt.replace_time_zone('UTC').dt.convert_time_zone('America/Los_Angeles'))"))
    write_recipe(project, "events", inputs=["raw/src.csv"], outputs=[EVENTS], description="In Los Angeles time.",
                 steps={})
    result = uvs.sync(root=project)
    assert views(project)[EVENTS]["fingerprint"] == before  # the same data, by the data hash rules
    assert result.ran == ("events", "daily")
    assert day(project) == [dt.date(2024, 2, 29)]
    assert uvs.check(root=project) == []


def test_run_names_a_reader_of_the_same_data_and_what_to_run(project):
    import datetime as dt

    events_and_daily(project)
    write(project, "recipes/events/01_make.py",
          EVENTS_STEP.replace(".dt.replace_time_zone('UTC'))",
                              ".dt.replace_time_zone('UTC').dt.convert_time_zone('America/Los_Angeles'))"))
    with pytest.warns(uvs.UsefulViewsWarning, match="with the same data, which recipes/daily reads") as caught:
        result = uvs.run("events", root=project)
    [message] = result.warnings
    assert "a time zone" in message and "run it too (useful-views run daily)" in message
    assert len(ours(caught)) == 1
    assert uvs.check(root=project) == []  # the records cannot show it: run said so
    uvs.run("daily", root=project)
    assert day(project) == [dt.date(2024, 2, 29)]


def test_sync_all_runs_every_recipe(project):
    chain(project)
    uvs.sync(root=project)
    result = uvs.sync(root=project, all=True)
    assert result.ran == ("up", "down")


def test_records_from_before_fingerprints_are_never_taken_for_stale(project):
    chain(project)
    uvs.sync(root=project)
    as_written_by_0_1_0(project)
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning) as caught:
        result = uvs.run("up", root=project)
    # down's record cannot show what it read, so sync cannot see it: say sync --all.
    assert any("useful-views sync --all" in w and "recipes/down" in w for w in result.warnings)
    # check names down, the recipe whose record cannot be judged (not up's view).
    with pytest.warns(uvs.UsefulViewsWarning, match="cannot tell whether recipes/down is out of date"):
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
    with pytest.warns(uvs.UsefulViewsWarning, match=r"nested.parquet has no data hash.*recipes/reader"):
        assert uvs.check(root=project) == []


def test_fingerprints_from_other_rules_are_never_compared(project):
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    view = next(r for r in data["resources"] if r["path"] == DOWN)
    view["producedBy"]["inputFingerprints"][UP] = "datahash2:sha256:" + "a" * 64
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.warns(uvs.UsefulViewsWarning, match="cannot tell whether recipes/down"):
        assert uvs.check(root=project) == []  # not taken for a change
    # Nor for no change: when up runs again, down runs too.
    write(project, "recipes/up/recipe.toml",
          f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    with pytest.warns(uvs.UsefulViewsWarning):  # the file is rewritten in the readable layout
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


def test_a_view_left_by_a_git_pull_stops_the_run_instead_of_looping(project):
    """After a pull, the records come from a co-author but the view files on disk
    are old. Running a reader on an old file would record reading data no recipe
    made, and sync would report it stale forever; it stops and says what to run."""
    chain(project)
    uvs.sync(root=project)
    old_up = (project / UP).read_bytes()
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)  # the co-author's run: new data, new record
    (project / UP).write_bytes(old_up)  # ...but this computer still has the old file
    before = (project / DOWN).read_bytes()
    with pytest.raises(uvs.ProjectError, match="holds other data than datapackage.json records") as caught:
        uvs.sync(root=project)
    assert "useful-views run up" in str(caught.value) and "useful-views sync --all" in str(caught.value)
    assert (project / DOWN).read_bytes() == before  # nothing changed
    result = uvs.sync(root=project, all=True)
    assert result.ran == ("up", "down") and uvs.check(root=project) == []


def test_sync_refuses_a_pulled_view_before_running_anything(project):
    """A sync that would run other recipes first stops before them, not part way."""
    chain(project)
    write_recipe(project, "alpha", inputs=["raw/src.csv"], outputs=["useful-views/alpha.parquet"],
                 steps={"01_make.py": "# Copy the raw file.\nimport pyarrow.csv as c, pyarrow.parquet as pq\n"
                                     "pq.write_table(c.read_csv('raw/src.csv'), 'useful-views/alpha.parquet')\n"})
    uvs.sync(root=project)
    old_up = (project / UP).read_bytes()
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    (project / UP).write_bytes(old_up)
    for name, inputs, out in (("alpha", "raw/src.csv", "useful-views/alpha.parquet"), ("down", UP, DOWN)):
        write(project, f"recipes/{name}/recipe.toml",  # both changed, so both queued; alpha runs first
              f'description = "Changed."\ninputs = ["{inputs}"]\noutputs = ["{out}"]\n')
    before = (project / "datapackage.json").read_bytes()
    with pytest.raises(uvs.ProjectError, match="sync would run recipes/down, which reads") as caught:
        uvs.sync(root=project)
    message = str(caught.value)
    assert "Nothing was changed." in message and "sync had already" not in message
    assert message.count("useful-views run up") == 1 and "useful-views sync --all" in message
    assert (project / "datapackage.json").read_bytes() == before  # alpha did not run


def test_a_pulled_view_whose_recipe_was_renamed_is_made_again(project):
    """The recorded maker of a view left by a pull no longer exists, but the
    renamed recipe runs first and makes it again: nothing to refuse."""
    import os

    chain(project)
    uvs.sync(root=project)
    old_up = (project / UP).read_bytes()
    other_data(project)
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    (project / UP).write_bytes(old_up)
    os.rename(project / "recipes" / "up", project / "recipes" / "up2")
    result = uvs.sync(root=project)
    assert result.removed == ("up",) and result.ran == ("up2", "down")
    assert uvs.check(root=project) == []


def test_two_pulled_views_of_one_recipe_name_it_once(project):
    add_raw_csv(project, "raw/src.csv")
    a, b = "useful-views/a.parquet", "useful-views/b.parquet"
    write_recipe(project, "up", inputs=["raw/src.csv"], outputs=[a, b])
    write_recipe(project, "both", inputs=[a, b], outputs=["useful-views/both.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(UP, a).replace(DOWN, "useful-views/both.parquet")})
    uvs.sync(root=project)
    old = {v: (project / v).read_bytes() for v in (a, b)}
    write(project, "recipes/up/01_make.py",
          "# Write a small table to each output.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
          f"pq.write_table(pa.table({{'id': [7, 8, 9], 'label': ['x', 'y', 'z']}}), {a!r})\n"
          f"pq.write_table(pa.table({{'id': [4, 5, 6], 'label': ['x', 'y', 'z']}}), {b!r})\n")
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    for v, data in old.items():
        (project / v).write_bytes(data)
    with pytest.raises(uvs.ProjectError, match="their files on disk hold other data") as caught:
        uvs.run("both", root=project)
    assert str(caught.value).count("useful-views run up") == 1


def test_run_tells_each_reader_what_changed(project):
    add_raw_csv(project, "raw/src.csv")
    a, b = "useful-views/a.parquet", "useful-views/b.parquet"
    write_recipe(project, "up", inputs=["raw/src.csv"], outputs=[a, b])
    write_recipe(project, "reads_a", inputs=[a], outputs=["useful-views/ra.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(UP, a).replace(DOWN, "useful-views/ra.parquet")})
    write_recipe(project, "reads_b", inputs=[b], outputs=["useful-views/rb.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(UP, b).replace(DOWN, "useful-views/rb.parquet")})
    uvs.sync(root=project)
    write(project, "recipes/up/01_make.py",  # a changes, b stays the same
          "# Write a small table to each output.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
          f"pq.write_table(pa.table({{'id': [7, 8, 9], 'label': ['x', 'y', 'z']}}), {a!r})\n"
          f"pq.write_table(pa.table({{'id': pa.array([1, 2, 3], pa.int64()), 'label': ['a', 'b', 'c']}}), {b!r})\n")
    with pytest.warns(uvs.UsefulViewsWarning) as caught:
        result = uvs.run("up", root=project)
    changed, same = result.warnings
    # reads_a read other data: its record shows it, so sync runs it.
    assert "with different data" in changed and "recipes/reads_a." in changed and "recipes/reads_b" not in changed
    assert "useful-views sync." in changed and "sync --all" not in changed
    # reads_b read the same data: named, with what to run, as the records cannot show the rest.
    assert "with the same data, which recipes/reads_b reads" in same and "recipes/reads_a" not in same
    assert "useful-views run reads_b" in same
    assert len(ours(caught)) == 2
    assert {f.recipe for f in uvs.check(root=project)} == {"reads_a"}


def pulled(project) -> bytes:
    """Bob's state after pulling Alice's work: her records, his older views.
    Returns his old down.parquet."""
    chain(project)
    uvs.sync(root=project)
    old_up, old_down = (project / UP).read_bytes(), (project / DOWN).read_bytes()
    add_raw_csv(project, "raw/src.csv", "id,value\n1,9.5\n2,8.5\n")  # Alice's corrected raw file
    other_data(project)  # ...and her change to up
    with pytest.warns(uvs.UsefulViewsWarning):
        uvs.run("up", root=project)
    uvs.sync(root=project)  # her records: down made from the new up
    (project / UP).write_bytes(old_up)  # Bob's files are still the old ones
    (project / DOWN).write_bytes(old_down)
    return old_down


def test_sync_makes_a_view_on_disk_that_is_not_the_recorded_one_again(project):
    """After a pull the records say down is current, but Bob's down.parquet is
    older. Once up runs, down runs too, so his old file is replaced."""
    old_down = pulled(project)
    (project / UP).unlink()  # up's view is missing, so sync makes it again
    result = uvs.sync(root=project)
    assert result.ran == ("up", "down")
    assert (project / DOWN).read_bytes() != old_down


def test_run_names_the_readers_after_a_pull(project):
    """Bob runs up: it makes exactly the data its record says, and down's record
    says it read that data, but the up.parquet it replaced was older, so Bob's
    down.parquet, made from that, may be stale: not "the same data"."""
    pulled(project)
    with pytest.warns(uvs.UsefulViewsWarning, match="file it replaced held other data") as caught:
        result = uvs.run("up", root=project)
    [message] = result.warnings
    assert "recipes/down" in message and "useful-views sync --all" in message and "same data" not in message
    assert len(ours(caught)) == 1


LISTS = "# Write a list in each cell.\nimport pyarrow as pa, pyarrow.parquet as pq\n"


@pytest.mark.parametrize("recorded_has_hash", [True, False])
def test_a_file_and_a_record_that_disagree_on_having_a_data_hash_differ(project, recorded_has_hash):
    """A file with a data hash and one without cannot hold the same data: a view
    left by a branch switch is refused like any other, by run and by sync."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    add_raw_csv(project, "raw/src.csv")
    flat = f"pq.write_table(pa.table({{'id': [1, 2]}}), {UP!r})\n"
    lists = f"pq.write_table(pa.table({{'tags': [['a'], ['b']]}}), {UP!r})\n"
    write_recipe(project, "up", inputs=["raw/src.csv"], outputs=[UP],
                 steps={"01_make.py": LISTS + (flat if recorded_has_hash else lists)})
    write_recipe(project, "down", inputs=[UP], outputs=[DOWN], steps={"01_count.py": (
        "# Count the rows.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
        f"pq.write_table(pa.table({{'n': [pq.read_table({UP!r}).num_rows]}}), {DOWN!r})\n")})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", uvs.UsefulViewsWarning)  # a view with no data hash gets a note
        uvs.sync(root=project)
    left = pa.table({"tags": [["a"], ["b"], ["c"]]}) if recorded_has_hash else pa.table({"id": [1, 2, 3]})
    pq.write_table(left, project / UP)  # as a branch switch leaves it
    with pytest.raises(uvs.ProjectError, match="holds other data than datapackage.json records"):
        uvs.run("down", root=project)
    write_recipe(project, "down", inputs=[UP], outputs=[DOWN], description="Edited.", steps={})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", uvs.UsefulViewsWarning)
        with pytest.raises(uvs.ProjectError, match="sync would run recipes/down, which reads"):
            uvs.sync(root=project)


def test_a_sync_that_stops_removes_the_views_made_from_the_old_ones(project):
    """up runs again with the same data, then boom, which reads up, fails. down
    reads up too and comes after boom: its view was made from the old up, which
    a step may see differently, so it was removed before anything ran, and the
    next sync makes it."""
    chain(project)
    write_recipe(project, "boom", inputs=[UP], outputs=["useful-views/boom.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(DOWN, "useful-views/boom.parquet")})
    uvs.sync(root=project)
    write(project, "recipes/up/recipe.toml",  # up runs again, with the same data
          f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    write(project, "recipes/boom/01_add.py", "# Add a column to up's table.\nraise SystemExit('boom')\n")
    write_recipe(project, "boom", inputs=[UP], outputs=["useful-views/boom.parquet"], description="Now fails.",
                 steps={})
    with pytest.raises(uvs.StepFailed) as caught:
        uvs.sync(root=project)
    assert caught.value.sync_result.ran == ("up",)
    assert "The views of recipes/boom, recipes/down were removed before anything ran" in str(caught.value)
    assert not (project / DOWN).exists()


def test_a_stopped_sync_leaves_no_view_to_pass_for_current_whatever_queued_it(project):
    """down is queued for its own change, not only because up runs. After the
    sync stops at boom, the change to down is undone: down's view is missing
    all the same, so the next sync makes it from the new up."""
    chain(project)
    write_recipe(project, "boom", inputs=[UP], outputs=["useful-views/boom.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(DOWN, "useful-views/boom.parquet")})
    uvs.sync(root=project)
    toml = (project / "recipes/down/recipe.toml").read_text(encoding="utf-8")
    write(project, "recipes/up/recipe.toml", f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    write(project, "recipes/down/recipe.toml", toml.replace("Test recipe down.", "Edited."))
    good = (project / "recipes/boom/01_add.py").read_text(encoding="utf-8")
    write(project, "recipes/boom/01_add.py", "# Add a column to up's table.\nraise SystemExit('boom')\n")
    with pytest.raises(uvs.StepFailed):
        uvs.sync(root=project)
    assert not (project / DOWN).exists()
    write(project, "recipes/down/recipe.toml", toml)  # the edit to down is undone
    write(project, "recipes/boom/01_add.py", good)
    assert uvs.sync(root=project).ran == ("boom", "down")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")
def test_a_sync_killed_between_recipes_leaves_no_view_to_pass_for_current(project):
    """The process is killed (a kernel restart, power lost): no cleanup runs, but
    the views to make again were removed before anything ran."""
    chain(project)
    write_recipe(project, "boom", inputs=[UP], outputs=["useful-views/boom.parquet"],
                 steps={"01_add.py": DOWN_STEP.replace(DOWN, "useful-views/boom.parquet")})
    uvs.sync(root=project)
    write(project, "recipes/up/recipe.toml", f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    good = (project / "recipes/boom/01_add.py").read_text(encoding="utf-8")
    write(project, "recipes/boom/01_add.py",
          "# Add a column to up's table.\nimport os, signal\nos.kill(os.getppid(), signal.SIGKILL)\n")
    done = subprocess.run([sys.executable, "-c", "import sys, useful_views as uvs; uvs.sync(root=sys.argv[1])",
                           str(project)], capture_output=True, text=True)
    assert done.returncode == -9
    assert not (project / DOWN).exists()
    assert {(f.recipe, f.kind) for f in uvs.check(root=project)} >= {("down", "missing-view")}
    write(project, "recipes/boom/01_add.py", good)
    assert uvs.sync(root=project).ran == ("boom", "down")


def test_a_recipe_counts_as_run_once_recorded(project, monkeypatch):
    """Ctrl-C while sync prints a recipe's warnings, after its run was recorded:
    it counts as run, so what was done is said rightly."""
    from useful_views import syncing

    chain(project)
    uvs.sync(root=project)
    write(project, "recipes/up/01_make.py", (project / "recipes/up/01_make.py").read_text(encoding="utf-8")
          + "open('raw/src.csv', 'a').write('')\nimport os\nos.utime('raw/src.csv', (1, 1))\n")
    write(project, "recipes/up/recipe.toml", f'description = "Changed."\ninputs = ["raw/src.csv"]\noutputs = ["{UP}"]\n')
    real = syncing.warn

    def interrupt(message, *args, **kwargs):
        if "changed raw/src.csv" in str(message):
            raise KeyboardInterrupt
        return real(message, *args, **kwargs)

    monkeypatch.setattr(syncing, "warn", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        uvs.sync(root=project)
    assert caught.value.sync_result.ran == ("up",)
    assert not (project / DOWN).exists()


def test_no_note_for_a_fingerprint_in_the_record_of_a_renamed_recipe(project):
    import os

    from useful_views.record import render_datapackage

    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    next(r for r in data["resources"] if r["path"] == UP)["fingerprint"] = "datahash9:abc"
    (project / "datapackage.json").write_text(render_datapackage(data), encoding="utf-8", newline="\n")
    os.rename(project / "recipes" / "up", project / "recipes" / "up2")
    result = uvs.sync(root=project)
    assert result.ran == ("up2", "down")
    assert not any("does not understand" in w for w in result.warnings)
