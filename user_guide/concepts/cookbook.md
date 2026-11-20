---
title: "Cookbook"
---

A cookbook is a collection of recipes. In a useful-views project it is
`cookbook.json`, at the project root. It lists every recipe the project
uses, in the order they run, with each recipe's steps. `useful-views
cookbook` writes it, the way `uv lock` writes `uv.lock`. Commit it, and
never edit it by hand.

## Write it

After `useful-views sync`, write the cookbook, then check it:

```sh
useful-views cookbook
useful-views cookbook --check
```

`cookbook` writes `cookbook.json` and says how many recipes it lists.
`cookbook --check` writes nothing. It says whether `cookbook.json` is up to
date, and exits with 1 if it isn't. From Python, use `uvs.cookbook()` and
`uvs.cookbook(check=True)`.

Commit the cookbook with the other generated files (see
[What to commit](projects.md#what-to-commit)). `cookbook` writes only when
the project is in sync, since the cookbook describes recipes as they last
ran. If `check` reports anything other than stray files or a stale catalog
or cookbook, `cookbook` lists the problems, writes nothing, and exits with
3. Writing an unchanged cookbook leaves the file and its timestamp alone, so
git shows a change only when there is one.

## What is in it

The example project's cookbook, shortened:

```json
{
  "version": 1,
  "generator": "useful-views",
  "project": "pwt-example",
  "raw_inputs": ["raw/pwt110_bra_deu_esp_usa.csv"],
  "recipes": [
    {
      "name": "pwt_panel",
      "description": "Country-year panel of core Penn World Table variables for ...",
      "path": "recipes/pwt_panel",
      "inputs": ["raw/pwt110_bra_deu_esp_usa.csv"],
      "outputs": ["useful-views/pwt-panel.parquet"],
      "steps": [
        {
          "name": "reshape_long",
          "path": "recipes/pwt_panel/01_reshape_long.py",
          "description": "Reshape the wide export (one column per year) into ..."
        }
      ]
    }
  ]
}
```

The real file also has the `sources` from the project's `sources.toml`, every
step, and `gdp_per_capita` after `pwt_panel`. Each list item is on its own
line.

| Field | What it holds |
|---|---|
| `version` | The format's version, 1. |
| `generator` | `useful-views`. |
| `project` | The package name recorded in `datapackage.json`. It is chosen once, from the folder's name when the project is created, and kept. So a clone in a folder with another name writes the same file. |
| `raw_inputs` | Every file under `raw/` that a recorded recipe reads, sorted. |
| `sources` | Only when the project has a [`sources.toml`](sources.md). Each source of those raw files, with the files it covers, as recorded in `datapackage.json`. |
| `recipes` | Every recorded recipe, upstream first, each exactly as its record in `datapackage.json`: name, description, path, inputs, outputs and steps. |

Version 1 holds no fingerprints. Fingerprints of raw files, code and the
environment are planned
([#32](https://github.com/jameshorine/useful-views/issues/32)), so that
`cookbook --check` could tell when anything changed, the way `uv.lock` does.

## Keep it up to date

`run` and `sync` don't update the cookbook. Once a project has a
`cookbook.json`, they warn when they leave it out of date, and `check`
reports `stale-cookbook`. Then write it again:

```sh
useful-views cookbook
```

Before the first `useful-views cookbook`, nothing reminds you. `check` says
`No problems found.`, but `cookbook --check`, and so the CI job below,
exits with 1 until `cookbook.json` is written and committed.

## Check it in CI

`cookbook --check` needs no data. A CI job has only the recipes and the
generated files, since the raw files and the views aren't in git. It exits
with 1, and lists why, when:

- `cookbook.json` is missing, or differs from the committed
  `datapackage.json`;
- a recipe folder no longer matches its record, and was committed without a
  `sync`. A recipe was added or removed, its description, inputs or outputs
  changed, an output was dropped, or a step was added, removed, renamed or
  described differently.
- `sources.toml` was edited without a `sync` (`stale-sources`);
- a recipe read a view whose data has changed since (`out-of-date`, reason
  `input-data-changed`). A recipe upstream of it ran again, with `run` or in
  a `sync` that stopped, and the result was committed without a `sync`. If
  that data shouldn't have changed, make sure the upstream recipe sorts its
  rows by columns that together identify each row.
- a recipe breaks a rule (`refused`), or a folder in `recipes/` has no
  readable `recipe.toml` (`unreadable-recipe`). `sync` doesn't fix these, so
  fix what is listed first.

An edit inside a step's code passes, as does a raw file replaced under the
same name. Neither shows in `check` or `sync` either. The catalog isn't
checked, since `sync` writes it again. If `datapackage.json` can't be read,
for example because of merge conflict markers, the command exits with 3.

A GitHub Actions job that requires an up-to-date cookbook:

```yaml
name: Cookbook
on: [push, pull_request]
permissions:
  contents: read
jobs:
  cookbook:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - run: pip install useful-views==0.2.0   # the version your project uses
      - run: useful-views cookbook --check
```

The job assumes the project is at the top of the repository. If it is in a
subfolder, name it: `useful-views -C path/to/project cookbook --check`.
Otherwise the command finds no project and exits with 3.

The job runs no step, so it needs neither your data nor your steps'
libraries. Pin useful-views to the version in your environment file.
`--check` compares the committed file with what the installed version would
write, so an unpinned job could fail on an unchanged project when a new
release changes the format.

A script should read `ok` from `cookbook --check --json` (see
[Working with AI tools](../guides/ai-tools.md#machine-readable-output)).

## Common situations

- **`check` reports `stale-cookbook`.** The records changed since the
  cookbook was written. Run `useful-views sync`, then `useful-views
  cookbook`, and commit both.
- **A merge conflict in `cookbook.json`.** Don't resolve it by hand.
  `datapackage.json` and the catalog usually conflict too. Take either side
  of each, make them again as [Records](records.md#merge-conflicts) shows,
  then run `useful-views cookbook`. A cookbook that isn't valid JSON, or
  isn't UTF-8, counts as out of date, and `cookbook` writes it again.
- **A co-author's clone shows a changed cookbook.** It shouldn't, if you
  both ran `sync` and `cookbook` on the same recipes with the same version
  of useful-views. A difference means the recipes differ, one of you
  committed without running `cookbook`, or you use different versions.
  `git diff cookbook.json` shows which recipe and step changed. Keep
  everyone on the version pinned in the project's environment file.

## Why keep one

`datapackage.json` is written for tools, and changes whenever a view's data
changes. The cookbook is written for people. It reads top to bottom as the
project's method, upstream first, and changes only when a recipe's
description, inputs, outputs or steps change. A reviewer reading a pull
request, an examiner reading a thesis repository, or an AI assistant sees
what is made from what, without running anything.

| To find out | Read |
|---|---|
| the method: which recipes, in which order, with which steps | `cookbook.json` |
| what one view contains, its columns and their types | `useful-views/README.md` (the catalog) |
| everything, in a standard format for tools | `datapackage.json` |
