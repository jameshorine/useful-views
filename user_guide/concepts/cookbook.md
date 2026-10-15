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
project, upstream first, and that changes in git exactly when the recipes
change. A reviewer reading a pull request, an examiner reading a thesis
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

`run` and `sync` do not update the cookbook themselves. When they change the
records, they warn that `cookbook.json` no longer matches, and `check`
reports `stale-cookbook` until you run `useful-views cookbook`.

## Checking it in CI

`cookbook --check` needs no data: the raw files and the views are not in
git, so a CI job only has the recipes and the generated files. It exits
with 1, and lists why, when:

- `cookbook.json` is missing, or differs from the committed
  `datapackage.json`;
- a recipe folder no longer matches its record: a recipe added, edited or
  removed, or an output dropped, and committed without running `sync`.

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
      - run: pip install useful-views
      - run: useful-views cookbook --check
```

It runs no step, so the job needs neither your data nor your steps'
libraries. Pin useful-views to the version in your environment file if you
want the job to match it exactly. With `--json`, the reasons are in the
document's `findings`, the same objects `check` gives.

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
- **A merge conflict in `cookbook.json`**: do not resolve it by hand. Take
  either side (`git checkout --ours cookbook.json`), make the project
  consistent with `useful-views sync --all`, then run `useful-views
  cookbook`. A cookbook that is not valid JSON, or not UTF-8, counts as out of
  date, and `cookbook` writes it again.
- **A co-author's clone shows a changed cookbook**: it should not, if both of
  you ran `sync` and `cookbook` on the same recipes. A difference means the
  recipes differ; `useful-views cookbook --check` and `useful-views check`
  say where.

## What is next

Version 1 says which recipes and steps made the views. It does not yet
record checksums, so it cannot tell that a raw file, a step's code or a view
changed since. Later versions are planned to add the SHA-256 of every raw
input, recipe file, step and view, and then the environment the steps ran
in, so that `cookbook --check` can tell when anything changed, the way
`uv.lock` locks a Python environment.
