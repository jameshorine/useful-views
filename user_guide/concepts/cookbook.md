---
title: "Cookbook"
---

A **cookbook** is a collection of recipes. In a useful-views project it is
`cookbook.json` at the project root: the ledger of every recipe the project
uses, in the order they run, with each recipe's steps. It is written by a
command, the way `uv lock` writes `uv.lock`, and never edited by hand.

```sh
useful-views cookbook           # write or update cookbook.json
useful-views cookbook --check   # exit 1 if it is missing or out of date
```

From Python: `uvs.cookbook()` and `uvs.cookbook(check=True)`.

## Why keep one

The record in `datapackage.json` is written for tools: every file, its
columns and types, recipes in name order. The cookbook is written for
people and reviewers: one file that reads top to bottom as the method of the
project, upstream first, and that changes in git when a recipe's
`recipe.toml`, its list of steps or a step's top comment changes (not when
only the code inside a step changes; see [What is next](#what-is-next)). A reviewer reading a pull request, an examiner reading a thesis
repository, or an AI assistant opening the project sees what is made from
what without running anything.

| To find out | Read |
|---|---|
| the method: which recipes, in which order, with which steps | `cookbook.json` |
| what one view contains, its columns and their types | `useful-views/README.md` (the catalog) |
| everything, in a standard format for tools | `datapackage.json` |

## When to write it

After the views are made, and before you commit:

```sh
useful-views sync
useful-views cookbook
git add cookbook.json datapackage.json useful-views/README.md recipes/
```

`cookbook` writes only when the project is in sync, since the cookbook
describes recipes as they last ran. If `check` reports anything other than
stray files or a stale catalog or cookbook, `cookbook` refuses, lists the
problems, and writes nothing. Writing an unchanged cookbook leaves the file
and its timestamp alone, so git shows a change only when there is one.

`run` and `sync` do not update the cookbook themselves. Once the project has
a `cookbook.json`, they warn when they change the records and leave it out of
date, and `check` reports `stale-cookbook` until you run `useful-views
cookbook`. Before the first `useful-views cookbook`, nothing reminds you:
`check` says `No problems found.`, but `cookbook --check` (and so the CI job
below) exits with 1 until `cookbook.json` is written and committed.

## Checking it in CI

`cookbook --check` needs no data: the raw files and the views are not in
git, so a CI job only has the recipes and the generated files. It exits
with 1, and lists why, when:

- `cookbook.json` is missing, or differs from the committed
  `datapackage.json`;
- a recipe folder no longer matches its record: a recipe added or removed,
  its `recipe.toml` edited, a step added, removed or renamed, a step's top
  comment changed, or an output dropped, and committed without running
  `sync`;
- a recipe's record shows it read a view whose data has changed since
  (`out-of-date`, reason `input-data-changed`): someone ran the recipe
  upstream of it with `useful-views run` and committed without running
  `sync`. If that data should not have changed, make sure the upstream
  recipe sorts its rows: the same rows in another order have another
  fingerprint;
- a recipe breaks a rule (`refused`), or a folder in `recipes/` has no
  readable `recipe.toml` (`unreadable-recipe`). `sync` does not fix these:
  fix what is listed first.

An edit inside a step's code passes, as it passes `check` and `sync` (see
[What is next](#what-is-next)). The catalog, `useful-views/README.md`, is
not checked; `useful-views sync` writes it again. If `datapackage.json`
cannot be read, for example because of merge conflict markers, the command
exits with 3.

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
      - run: pip install useful-views==0.1.0   # the version your project uses
      - run: useful-views cookbook --check
```

The job assumes the project is at the top of the repository. If it is in a
subfolder, name it: `useful-views -C path/to/project cookbook --check`.
Without that, the command finds no project and exits with 3, not 1.

It runs no step, so the job needs neither your data nor your steps'
libraries. Pin useful-views to the version in your environment file:
`--check` compares the committed file with what the installed version would
write, so an unpinned job could fail on an unchanged project when a new
release changes the format.

With `--json`, `ok` is the answer. `findings` lists the recipe folders that
no longer match their records, as the same objects `check` gives; it is
empty when the only problem is that `cookbook.json` is missing or differs,
so test `ok`, not `findings`. When `useful-views cookbook` (without
`--check`) refuses to write, it exits with 3, and its findings are in
`error.findings`.

## What is in it

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

(Shortened: the real file lists every step, and `gdp_per_capita` after
`pwt_panel`, one list item per line.)

| Field | What it holds |
|---|---|
| `version` | The format's version: 1 |
| `generator` | `useful-views` |
| `project` | The package name recorded in `datapackage.json`. It is chosen once, from the folder's name when the project is created, and kept, so a clone in a folder with another name writes the same file. |
| `raw_inputs` | Every file under `raw/` a recorded recipe reads, sorted |
| `recipes` | Every recorded recipe, upstream first, each exactly as its record in `datapackage.json`: name, description, path, inputs, outputs and steps |

## Common situations

- **`check` reports `stale-cookbook`**: the records changed since the
  cookbook was written. Run `useful-views sync`, then `useful-views
  cookbook`, and commit both.
- **A merge conflict in `cookbook.json`**: do not resolve it by hand;
  `datapackage.json` and `useful-views/README.md` usually conflict too. Take
  either side of each generated file (`git checkout --ours datapackage.json
  useful-views/README.md cookbook.json`), run `useful-views sync --all` (it
  runs every recipe again, so it needs the raw data), then `useful-views
  cookbook`, and `git add` those files. A cookbook that is not valid JSON, or
  not UTF-8, counts as out of date, and `cookbook` writes it again. See
  [Records](records.md).
- **A co-author's clone shows a changed cookbook**: it should not, if both of
  you ran `sync` and `cookbook` on the same recipes with the same version of
  useful-views. A difference means the recipes differ, one of you committed
  without running `cookbook`, or you use different versions of useful-views.
  `git diff cookbook.json` shows which recipe and step changed. Keep everyone
  on the version pinned in the project's environment file.

## What is next

Version 1 says which recipes and steps made the views. It does not yet
record checksums, so it cannot tell that a raw file, a step's code or a view
changed since. Later versions are planned to add the SHA-256 of every raw
input, recipe file, step and view, and then the environment the steps ran
in, so that `cookbook --check` can tell when anything changed, the way
`uv.lock` locks a Python environment. See
[Stale views and fingerprints](stale-views.md).
