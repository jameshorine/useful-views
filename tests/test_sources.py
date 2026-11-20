"""sources.toml: where each raw file comes from (issue #23).

A project may describe its raw data sources in sources.toml. Their details
are copied onto each raw file's record in datapackage.json (Data Package
`sources` and `licenses`), shown in the catalog and the cookbook, and kept
in step by check and sync. They never mark a recipe out of date.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import useful_views as uvs
from useful_views.sources import SOURCES, load_sources
from useful_views.status import KINDS, SYNCABLE

from conftest import add_raw_csv, write, write_recipe

VIEW = "useful-views/view.parquet"
RAW = "raw/data.csv"

PWT = '''
[[sources]]
title = "Penn World Table 11.0"
files = ["raw/data.csv"]
url = "https://www.rug.nl/ggdc/productivity/pwt/"
doi = "10.34894/FABVLR"
access = """
Exported from the PWT online data access tool.
No registration needed."""
cost = "Free"
time = "A few minutes"
obtained = 2026-10-09
license = "CC-BY-4.0"
license_url = "https://creativecommons.org/licenses/by/4.0/"
citation = """
Feenstra, Inklaar and Timmer (2015), "The Next Generation of the Penn World
Table." American Economic Review 105(10): 3150-3182."""
shared = true
'''


def synced(project: Path, sources: str | None = PWT) -> Path:
    """A project with one recipe reading raw/data.csv, synced, with `sources` as its sources.toml."""
    add_raw_csv(project, RAW)
    write_recipe(project, "demo", inputs=[RAW], outputs=[VIEW])
    if sources is not None:
        write(project, SOURCES, sources)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        uvs.sync(root=project)
    return project


def raw_resource(project: Path, rel: str = RAW) -> dict:
    data = json.loads((project / "datapackage.json").read_text(encoding="utf-8"))
    return next(r for r in data["resources"] if r["path"] == rel)


# --- the records ----------------------------------------------------------------


def test_source_details_reach_the_raw_files_record(project):
    synced(project)
    resource = raw_resource(project)
    assert resource["sources"] == [{
        "title": "Penn World Table 11.0",
        "path": "https://www.rug.nl/ggdc/productivity/pwt/",
        "doi": "10.34894/FABVLR",
        "access": "Exported from the PWT online data access tool. No registration needed.",
        "cost": "Free",
        "time": "A few minutes",
        "obtained": "2026-10-09",
        "citation": 'Feenstra, Inklaar and Timmer (2015), "The Next Generation of the Penn World Table." American '
                    "Economic Review 105(10): 3150-3182.",
        "shared": True,
    }]
    assert resource["licenses"] == [{"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/"}]
    keys = list(resource)
    assert keys.index("licenses") < keys.index("schema"), "the schema, often long, stays last"


def test_the_datapackage_with_sources_still_validates(project):
    synced(project)
    proc = subprocess.run([sys.executable, "-m", "frictionless", "validate", "datapackage.json"], cwd=project,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_without_sources_toml_records_and_notes_are_unchanged(project):
    synced(project, sources=None)
    assert "sources" not in raw_resource(project) and "licenses" not in raw_resource(project)
    assert "Raw data sources" not in (project / "useful-views/README.md").read_text(encoding="utf-8")
    assert uvs.check(root=project) == []


@pytest.mark.parametrize("license, recorded, kept", [
    pytest.param('license = "CC-BY-4.0"', [{"name": "CC-BY-4.0"}], None, id="identifier"),
    pytest.param('license = "Public domain"\nlicense_url = "https://example.org/terms"',
                 [{"title": "Public domain", "path": "https://example.org/terms"}], None, id="text-with-url"),
    pytest.param('license = "Public domain (US government work)"', None, "Public domain (US government work)",
                 id="text-only"),
])
def test_licenses_are_written_as_data_package_licenses_when_they_can_be(project, license, recorded, kept):
    synced(project, f'[[sources]]\ntitle = "Data"\nfiles = ["{RAW}"]\n{license}\n')
    resource = raw_resource(project)
    assert resource.get("licenses") == recorded
    assert resource["sources"][0].get("license") == kept


def test_a_doi_without_a_url_becomes_the_sources_path(project):
    synced(project, f'[[sources]]\ntitle = "Data"\nfiles = ["{RAW}"]\ndoi = "10.1234/abc"\n')
    assert raw_resource(project)["sources"][0]["path"] == "https://doi.org/10.1234/abc"


def test_one_source_can_cover_several_raw_files(project):
    add_raw_csv(project, "raw/a.csv")
    add_raw_csv(project, "raw/b.csv")
    write_recipe(project, "demo", inputs=["raw/a.csv", "raw/b.csv"], outputs=[VIEW])
    write(project, SOURCES, '[[sources]]\ntitle = "Survey"\nfiles = ["raw/a.csv", "raw/b.csv"]\nlicense = "CC0-1.0"\n')
    uvs.sync(root=project)
    assert raw_resource(project, "raw/a.csv")["sources"] == raw_resource(project, "raw/b.csv")["sources"]
    book = uvs.cookbook(root=project)
    assert book.written
    cookbook = json.loads((project / "cookbook.json").read_text(encoding="utf-8"))
    assert cookbook["sources"] == [{"title": "Survey", "files": ["raw/a.csv", "raw/b.csv"],
                                    "licenses": [{"name": "CC0-1.0"}]}]


# --- check and sync keep them in step -----------------------------------------------


def test_editing_sources_toml_is_stale_until_sync_which_runs_nothing(driver, project):
    synced(project)
    write(project, SOURCES, PWT.replace('cost = "Free"', 'cost = "Free of charge"'))
    out = driver.check()
    assert out.kinds == ["stale-sources"], out.stdout
    assert "stale-sources" in SYNCABLE
    out = driver.sync()
    assert out.ok, out.message
    if driver.name == "api":
        assert out.value.ran == ()
    assert any("updated the source details" in w for w in out.warnings), out.warnings
    assert raw_resource(project)["sources"][0]["cost"] == "Free of charge"
    assert driver.check().kinds == []


def test_deleting_sources_toml_removes_the_details_from_the_records(project):
    synced(project)
    (project / SOURCES).unlink()
    assert [f.kind for f in uvs.check(root=project)] == ["stale-sources"]
    with pytest.warns(uvs.UsefulViewsWarning, match="no sources.toml any more"):
        uvs.sync(root=project)
    assert "sources" not in raw_resource(project)


def test_a_raw_file_with_no_source_is_a_note_not_a_finding(driver, project):
    synced(project, '[[sources]]\ntitle = "Other"\nfiles = ["raw/other.csv"]\n')
    out = driver.check()
    assert out.kinds == [] and out.exit_code == 0
    assert any("gives no source for raw/data.csv" in w for w in out.warnings), out.warnings


def test_run_notes_its_own_raw_inputs_with_no_source(project):
    add_raw_csv(project, RAW)
    write_recipe(project, "demo", inputs=[RAW], outputs=[VIEW])
    write(project, SOURCES, "")
    with pytest.warns(uvs.UsefulViewsWarning, match="gives no source for raw/data.csv"):
        uvs.run("demo", root=project)


def test_the_new_kinds_sit_with_their_kin():
    assert KINDS.index("unreadable-sources") == KINDS.index("unreadable-recipe") + 1
    assert KINDS.index("stale-sources") == KINDS.index("stale-cookbook") - 1


# --- a sources.toml that cannot be read ------------------------------------------------


@pytest.mark.parametrize("text, says", [
    pytest.param("[[sources]\n", "not valid TOML", id="syntax"),
    pytest.param('[[sources]]\nfiles = ["raw/data.csv"]\n', "has no `title`", id="no-title"),
    pytest.param('[[sources]]\ntitle = "Data"\n', "has no `files`", id="no-files"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = []\n', "at least one path", id="empty-files"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["data/x.csv"]\n', "is not in raw/", id="not-raw"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["./raw/data.csv"]\n', "raw/data.csv", id="path-form"),
    pytest.param('[[sources]]\ntitle = "A"\nfiles = ["raw/data.csv"]\n[[sources]]\ntitle = "B"\n'
                 'files = ["raw/data.csv"]\n', "in one source only", id="file-in-two-sources"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["raw/data.csv"]\nshared = "yes"\n', "true or false",
                 id="shared-not-bool"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["raw/data.csv"]\nobtained = 2026\n', "must be a date",
                 id="obtained-not-date"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["raw/data.csv"]\ncitation = ""\n', "not empty",
                 id="empty-text"),
    pytest.param('[sources]\ntitle = "Data"\nfiles = ["raw/data.csv"]\n', "[[sources]], with two", id="single-table"),
    pytest.param('[[sources]]\ntitle = "Data"\nfiles = ["raw/data.csv"]\ncitation = "say "hi" now"\n',
                 'triple quotes: """..."""', id="inner-double-quotes"),
])
def test_an_unreadable_sources_toml_is_a_finding_and_says_why(driver, project, text, says):
    synced(project, sources=None)
    write(project, SOURCES, text)
    out = driver.check()
    assert out.kinds == ["unreadable-sources"], out.stdout
    shown = out.stdout if driver.name == "cli" else str(out.value[0])
    assert says in shown, shown


def test_run_and_sync_keep_the_recorded_details_while_sources_toml_is_broken(project):
    synced(project)
    before = raw_resource(project)["sources"]
    write(project, SOURCES, "[[sources]\n")
    with pytest.warns(uvs.UsefulViewsWarning, match="kept as they were recorded"):
        uvs.run("demo", root=project)
    assert raw_resource(project)["sources"] == before
    with pytest.warns(uvs.UsefulViewsWarning, match="kept as they were recorded"):
        uvs.sync(root=project, all=True)
    assert raw_resource(project)["sources"] == before


def test_unknown_keys_are_noted(project):
    synced(project, f'author = "me"\n[[sources]]\ntitle = "Data"\nfiles = ["{RAW}"]\nlicence = "CC0-1.0"\n')
    notes = load_sources(project).warnings
    assert any("`author`" in n for n in notes) and any("`licence`" in n for n in notes), notes


# --- what people read ---------------------------------------------------------------


def test_the_catalog_lists_each_source(project):
    synced(project, PWT.replace("shared = true", 'shared = false\nreason = "The license allows it, but it is large."'))
    catalog = (project / "useful-views/README.md").read_text(encoding="utf-8")
    section = catalog[catalog.index("## Raw data sources"):]
    assert "### Penn World Table 11.0" in section
    assert "- Files: `raw/data.csv`" in section
    assert "- License: CC-BY-4.0 https://creativecommons.org/licenses/by/4.0/" in section
    assert "- Shared in the replication package: no: The license allows it, but it is large." in section
    assert "- Cite: Feenstra" in section


def test_the_catalog_names_raw_files_with_no_source(project):
    add_raw_csv(project, "raw/a.csv")
    add_raw_csv(project, "raw/b.csv")
    write_recipe(project, "demo", inputs=["raw/a.csv", "raw/b.csv"], outputs=[VIEW])
    write(project, SOURCES, '[[sources]]\ntitle = "A"\nfiles = ["raw/a.csv"]\n')
    with pytest.warns(uvs.UsefulViewsWarning, match="gives no source for raw/b.csv"):
        uvs.sync(root=project)
    catalog = (project / "useful-views/README.md").read_text(encoding="utf-8")
    assert "Raw files with no recorded source: `raw/b.csv`" in catalog


def test_cookbook_check_needs_no_data_to_catch_an_edited_sources_toml(project):
    synced(project)
    uvs.cookbook(root=project)
    write(project, SOURCES, PWT.replace('time = "A few minutes"', 'time = "An hour"'))
    shutil.rmtree(project / "raw")
    (project / VIEW).unlink()
    result = uvs.cookbook(root=project, check=True)
    assert not result.up_to_date
    assert [f.kind for f in result.findings] == ["stale-sources"]
