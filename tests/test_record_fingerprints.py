"""When a recipe runs, datapackage.json records each view's fingerprint (its
data hash) and, in `producedBy`, the fingerprints of the views the recipe
read. Records from before fingerprints stay valid."""

from __future__ import annotations

import json

import pytest

import useful_views as uvs
from useful_views import record
from useful_views.record import render_catalog, render_datapackage

from conftest import add_raw_csv, write_recipe

UP, DOWN = "useful-views/up.parquet", "useful-views/down.parquet"

# down reads up's view and adds a column, so the two views hold different data.
DOWN_STEP = (
    "# Add a column to up's table.\n"
    "import pyarrow.compute as pc, pyarrow.parquet as pq\n"
    f"table = pq.read_table({UP!r})\n"
    f"pq.write_table(table.append_column('twice', pc.multiply(table['id'], 2)), {DOWN!r})\n"
)


def chain(root) -> None:
    """up reads raw/src.csv and makes up.parquet; down reads up.parquet."""
    add_raw_csv(root, "raw/src.csv")
    write_recipe(root, "up", inputs=["raw/src.csv"], outputs=[UP])
    write_recipe(root, "down", inputs=[UP], outputs=[DOWN], steps={"01_add.py": DOWN_STEP})


def views(root) -> dict[str, dict]:
    data = json.loads((root / "datapackage.json").read_text(encoding="utf-8"))
    return {r["path"]: r for r in data["resources"] if "producedBy" in r}


def test_each_view_records_its_fingerprint_and_its_inputs(driver, project):
    chain(project)
    out = driver.sync()
    assert out.ok, out.message
    v = views(project)
    assert v[UP]["fingerprint"] == uvs.data_hash(project / UP)
    assert v[DOWN]["fingerprint"] == uvs.data_hash(project / DOWN) != v[UP]["fingerprint"]
    assert v[UP]["producedBy"]["inputFingerprints"] == {}  # raw files have no fingerprints yet
    # down records the fingerprint of the up it read: up's own, as it is now.
    assert v[DOWN]["producedBy"]["inputFingerprints"] == {UP: v[UP]["fingerprint"]}


def test_running_again_with_the_same_data_changes_nothing(project):
    chain(project)
    uvs.sync(root=project)
    before = (project / "datapackage.json").read_bytes()
    with pytest.warns(uvs.UsefulViewsWarning, match="same data, which recipes/down reads"):
        uvs.run("up", root=project)
    uvs.run("down", root=project)
    assert (project / "datapackage.json").read_bytes() == before


def test_a_view_made_again_with_other_data(project):
    """What the next stage compares: down's record of what up held no longer
    matches up's own fingerprint once up is made again with other data."""
    chain(project)
    uvs.sync(root=project)
    old = views(project)[UP]["fingerprint"]
    write_recipe(project, "up", inputs=["raw/src.csv"], outputs=[UP], steps={"01_make.py": (
        "# Write other data.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
        f"pq.write_table(pa.table({{'id': [7, 8, 9], 'label': ['x', 'y', 'z']}}), {UP!r})\n"
    )})
    with pytest.warns(uvs.UsefulViewsWarning, match="recipes/down"):
        uvs.run("up", root=project)
    v = views(project)
    assert v[UP]["fingerprint"] not in (old, None)
    assert v[DOWN]["producedBy"]["inputFingerprints"] == {UP: old}


def test_a_view_the_rules_do_not_cover_records_null(project):
    add_raw_csv(project, "raw/src.csv")
    nested = "useful-views/nested.parquet"
    write_recipe(project, "lists", inputs=["raw/src.csv"], outputs=[nested], steps={"01_make.py": (
        "# Write a list in each cell.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
        f"pq.write_table(pa.table({{'id': [1, 2], 'tags': [['a'], ['b', 'c']]}}), {nested!r})\n"
    )})
    write_recipe(project, "reader", inputs=[nested], outputs=[DOWN], steps={"01_count.py": (
        "# Count the tags.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
        f"t = pq.read_table({nested!r})\n"
        f"pq.write_table(pa.table({{'n': [t.num_rows]}}), {DOWN!r})\n"
    )})
    result = uvs.sync(root=project)
    assert result.ran == ("lists", "reader")
    v = views(project)
    assert v[nested]["fingerprint"] is None
    assert v[DOWN]["producedBy"]["inputFingerprints"] == {nested: None}
    assert v[DOWN]["fingerprint"] == uvs.data_hash(project / DOWN)
    # Not a problem, but check says it cannot judge the recipe that reads it.
    with pytest.warns(uvs.UsefulViewsWarning, match=r"nested.parquet has no data hash.*recipes/reader"):
        assert uvs.check(root=project) == []


def test_each_view_is_hashed_once_in_a_sync(project, monkeypatch):
    chain(project)
    hashed = []
    real = record.hash_file
    monkeypatch.setattr(record, "hash_file", lambda path, shown: hashed.append(path.name) or real(path, shown))
    uvs.sync(root=project)
    # up.parquet is hashed when up makes it, not again when down reads it.
    assert sorted(hashed) == ["down.parquet", "up.parquet"]
    hashed.clear()
    uvs.run("down", root=project)  # on its own, run hashes what it reads
    assert sorted(hashed) == ["down.parquet", "up.parquet"]


def as_written_by_0_1_0(root) -> None:
    """Rewrite datapackage.json and the catalog as useful-views 0.1.0 wrote
    them: no fingerprints, recipes and views by name, indented JSON."""
    data = json.loads((root / "datapackage.json").read_text(encoding="utf-8"))
    raws = [r for r in data["resources"] if "producedBy" not in r]
    old_views = []
    for r in sorted((r for r in data["resources"] if "producedBy" in r), key=lambda r: r["producedBy"]["recipe"]):
        produced = {k: v for k, v in r["producedBy"].items() if k != "inputFingerprints"}
        old_views.append({**{k: v for k, v in r.items() if k not in ("fingerprint", "producedBy")}, "producedBy": produced})
    data = {"name": data["name"], "resources": raws + old_views,
            "recipes": sorted(data["recipes"], key=lambda r: r["name"])}
    (root / "datapackage.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "useful-views" / "README.md").write_text(render_catalog(data), encoding="utf-8")


def test_records_from_before_fingerprints_stay_valid(project):
    chain(project)
    uvs.sync(root=project)
    as_written_by_0_1_0(project)
    with pytest.warns(uvs.UsefulViewsWarning, match="cannot tell whether recipes/down is out of date"):
        assert uvs.check(root=project) == []  # a note, not a problem
    with pytest.warns(uvs.UsefulViewsWarning):
        result = uvs.sync(root=project)
    # Nothing runs, and the catalog in the old order is not taken for a hand edit. One note
    # names the recipe whose record cannot be judged and how to fix it (up reads only a
    # raw file, so needs none); another says the file was rewritten in the new layout.
    assert result.ran == () and len(result.warnings) == 2
    unjudged, rewritten = result.warnings
    assert "recipes/down" in unjudged and "recipes/up" not in unjudged and "useful-views sync --all" in unjudged
    assert "rewrote datapackage.json and useful-views/README.md in this version's layout" in rewritten
    text = (project / "datapackage.json").read_text(encoding="utf-8")
    assert text == render_datapackage(json.loads(text))  # rewritten in the new layout, once
    assert all("fingerprint" not in v for v in views(project).values())  # nothing ran, so none yet
    uvs.sync(root=project, all=True)
    assert all(v["fingerprint"] for v in views(project).values())


@pytest.mark.parametrize("field, value", [
    ("fingerprint", "abc"),
    ("fingerprint", 42),
    ("fingerprint", "sha256:" + "0" * 64),
    ("fingerprint", {"data": "datahash1:sha256:" + "0" * 64}),
    ("fingerprint", "datahash1:sha256:" + "0" * 64 + "\n"),
    ("fingerprint", "datahash1:sha256:" + "G" * 64),
    ("inputFingerprints", ["datahash1:sha256:" + "0" * 64]),
    ("inputFingerprints", {UP: "DATAHASH1:SHA256:00"}),
])
def test_a_fingerprint_it_does_not_understand_is_ignored_with_a_note(project, field, value):
    """From a newer version of useful-views, or a hand edit: never an error,
    and never taken for a change or for being up to date. check and sync say
    they cannot tell, and why."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    view = next(r for r in data["resources"] if r["path"] == DOWN)
    if field == "inputFingerprints":
        view["producedBy"][field] = value
    else:
        view[field] = value
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.warns(uvs.UsefulViewsWarning, match="does not understand"):
        assert uvs.check(root=project) == []
    with pytest.warns(uvs.UsefulViewsWarning):
        result = uvs.sync(root=project)
    assert result.ran == ()
    assert any("does not understand" in w and DOWN in w and "newer version" in w for w in result.warnings)


def test_sync_all_does_not_say_to_run_sync_all_for_fingerprints_it_records_again(project):
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    next(r for r in data["resources"] if r["path"] == DOWN)["fingerprint"] = "datahash9:abc"
    (project / "datapackage.json").write_text(render_datapackage(data), encoding="utf-8", newline="\n")
    result = uvs.sync(root=project, all=True)  # every entry is recorded again: nothing to say
    assert result.ran == ("up", "down") and result.warnings == ()


def test_line_endings_alone_are_not_taken_for_a_layout_change(project):
    """A git checkout on Windows can turn the generated files' line endings
    into CRLF: that is not a change of layout, and sync says nothing."""
    chain(project)
    uvs.sync(root=project)
    for rel in ("datapackage.json", "useful-views/README.md"):
        path = project / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert uvs.sync(root=project).warnings == ()


def _view(data, path=DOWN):
    return next(r for r in data["resources"] if r["path"] == path)


def _recipe(data, name="down"):
    return next(r for r in data["recipes"] if r["name"] == name)


@pytest.mark.parametrize("breaks", [
    lambda d: _view(d).update(producedBy="up"),
    lambda d: _view(d).update(producedBy=None),
    lambda d: _view(d)["producedBy"].update(inputs=[["x"]]),
    lambda d: _view(d).update(schema=None),
    lambda d: _view(d)["schema"]["fields"][0].update(name=1),
    lambda d: _recipe(d).update(steps="abc"),
    lambda d: _recipe(d)["steps"][0].pop("description"),
    lambda d: _recipe(d).update(description=5),
    lambda d: _recipe(d).update(inputs=[1]),
    lambda d: _recipe(d).update(inputs=[["x"]]),
    lambda d: d["resources"].append(dict(_view(d))),  # a view twice, as a bad merge leaves it
    lambda d: d["recipes"].append(dict(_recipe(d))),
    lambda d: _recipe(d)["outputs"].append("useful-views/a\u202eb.parquet"),
    lambda d: _view(d)["producedBy"].update(recipe="down\x1b[31m"),
    lambda d: _recipe(d)["inputs"].append("raw/\x1b[31mred.csv"),
    lambda d: _view(d)["producedBy"]["inputs"].append("useful-views/\u202eb.parquet"),
])
def test_a_broken_record_is_still_refused(project, breaks):
    """Every command refuses it with the file and how to restore it, rather than crash."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    breaks(data)
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    for command in (uvs.check, uvs.sync, lambda root: uvs.cookbook(root=root, check=True)):
        with pytest.raises(uvs.ProjectError, match="never writes|structure useful-views writes|invisible character") as raised:
            command(root=project)
        assert "datapackage.json" in str(raised.value) and "git checkout" in str(raised.value)


def test_a_path_with_an_invisible_character_from_0_1_says_what_to_do(project):
    """useful-views 0.1 recorded names with text-direction marks, which 0.2
    refuses: the message says so, and how to get out, not only "restore"."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    raw = next(r for r in data["resources"] if r["path"] == "raw/src.csv")
    raw["path"] = "raw/\u200fsrc.csv"
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(uvs.ProjectError) as raised:
        uvs.check(root=project)
    message = str(raised.value)
    assert "U+200F" in message and "useful-views 0.1 accepted" in message
    assert "useful-views init and useful-views sync --all" in message
    assert str(project) not in message  # named relative to the project


def test_an_input_fingerprint_for_a_view_it_does_not_read_is_ignored(project):
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    _view(data)["producedBy"]["inputFingerprints"][DOWN] = "datahash1:sha256:" + "0" * 64
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    assert uvs.check(root=project) == []


def test_a_fingerprint_from_later_rules_is_accepted(project):
    """A file written by a later version, with datahash2 fingerprints, still loads."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    next(r for r in data["resources"] if r["path"] == UP)["fingerprint"] = "datahash2:sha256:" + "a" * 64
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    # Under other rules, up's fingerprint cannot be compared: a note, not a problem.
    with pytest.warns(uvs.UsefulViewsWarning, match="cannot tell whether recipes/down"):
        assert uvs.check(root=project) == []


def test_fields_from_a_newer_version_are_kept_and_never_a_change(project):
    """A newer version of useful-views may record more: at the top level, in
    a recipe, a step or a view. This one leaves what it does not know alone:
    no recipe is out of date for it, and rewriting the file keeps it, until
    that recipe runs again and gets a fresh entry."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    data["generatedBy"] = {"tool": "useful-views", "version": "9.0"}
    _recipe(data)["environment"] = "env9:abc"
    _recipe(data)["steps"][0]["codeFingerprint"] = "code9:abc"
    _view(data)["fileHash"] = "sha256:abc"
    (project / "datapackage.json").write_text(render_datapackage(data), encoding="utf-8", newline="\n")
    uvs.cookbook(root=project)
    before = (project / "datapackage.json").read_bytes()

    assert uvs.check(root=project) == []
    assert uvs.cookbook(root=project, check=True).up_to_date
    assert uvs.sync(root=project).ran == ()
    assert (project / "datapackage.json").read_bytes() == before

    with pytest.warns(uvs.UsefulViewsWarning, match="same data"):
        uvs.run("up", root=project)  # rewrites the file, the same data
    kept = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    assert kept["generatedBy"] == data["generatedBy"]
    assert _recipe(kept) == _recipe(data) and _view(kept) == _view(data)
    assert uvs.check(root=project) == []

    with pytest.warns(uvs.UsefulViewsWarning, match="cookbook.json no longer matches"):
        uvs.run("down", root=project)
    fresh = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    assert "environment" not in _recipe(fresh) and "codeFingerprint" not in _recipe(fresh)["steps"][0]
    assert "fileHash" not in _view(fresh)
    assert fresh["generatedBy"] == data["generatedBy"]
