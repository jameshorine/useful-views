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
    with pytest.warns(uvs.UsefulViewsWarning, match="recipes/down"):
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
    assert uvs.check(root=project) == []


def test_each_view_is_hashed_once_in_a_sync(project, monkeypatch):
    chain(project)
    hashed = []
    real = record.data_hash
    monkeypatch.setattr(record, "data_hash", lambda path: hashed.append(path.name) or real(path))
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
    assert uvs.check(root=project) == []
    result = uvs.sync(root=project)
    # Nothing runs, and the catalog in the old order is not taken for a hand edit.
    assert result.ran == () and result.warnings == ()
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
    ("inputFingerprints", ["datahash1:sha256:" + "0" * 64]),
    ("inputFingerprints", {UP: "DATAHASH1:SHA256:00"}),
    ("producedBy", "up"),
])
def test_a_fingerprint_edited_by_hand_is_refused(project, field, value):
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    view = next(r for r in data["resources"] if r["path"] == DOWN)
    if field == "inputFingerprints":
        view["producedBy"][field] = value
    else:
        view[field] = value
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(uvs.ProjectError, match="never writes"):
        uvs.check(root=project)


def test_a_fingerprint_from_later_rules_is_accepted(project):
    """A file written by a later version, with datahash2 fingerprints, still loads."""
    chain(project)
    uvs.sync(root=project)
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    next(r for r in data["resources"] if r["path"] == UP)["fingerprint"] = "datahash2:sha256:" + "a" * 64
    (project / "datapackage.json").write_text(json.dumps(data), encoding="utf-8")
    assert uvs.check(root=project) == []
