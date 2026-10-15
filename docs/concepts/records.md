---
title: "Records"
guide-section: Concepts
---

After each successful run, useful-views rewrites two files: the record,
`datapackage.json`, and a readable catalog, `useful-views/README.md`. Never
edit them by hand; if one is damaged, restore it from git.

## datapackage.json

The record is a [Data Package](https://datapackage.org/), a standard format
many tools can read. `frictionless validate datapackage.json` passes on it
whenever every recorded file is present. It sits at the project root because
it describes files in both `raw/` and `useful-views/`.

- `resources` lists every raw file a recorded recipe read, then every view,
  each with its columns and their types. A view also has `producedBy`: the
  recipe that made it and that recipe's inputs.
- `recipes` lists each recorded recipe with its description, inputs,
  outputs and steps, as they were at its last successful run.

```json
{
  "name": "gdp-per-capita",
  "type": "table",
  "path": "useful-views/gdp-per-capita.parquet",
  "scheme": "file",
  "format": "parquet",
  "mediatype": "application/parquet",
  "schema": {
    "fields": [
      {"name": "iso", "type": "string"},
      {"name": "year", "type": "integer"}
    ]
  },
  "producedBy": {
    "recipe": "gdp_per_capita",
    "inputs": ["useful-views/pwt-panel.parquet"]
  }
}
```

Column types come from the files themselves: a parquet file's own types, and
for a raw CSV, types inferred from the whole file. In a raw CSV only an empty
cell counts as missing, so a column that marks missing values with `NA` or
`.` is recorded as `string`. Other raw formats (`.dta`, `.xlsx`, ...), and
CSVs that are not plain comma-separated UTF-8, are recorded without columns.

A resource's name is its file name without the extension, lowercased, with
other characters than `a-z`, `0-9`, `.`, `_` and `-` turned into `-`. Two
recorded files may not share one ([rule 8](../reference/rules.md)).

## useful-views/README.md

The catalog is generated from `datapackage.json` alone. For each view it
gives the recipe's description, its inputs, its numbered steps (their top
comments) and its columns with their types. It is committed to git, so a
reviewer sees what each view is and how it was made without running
anything.

## cookbook.json

The cookbook is the project's ledger of the recipes it uses, written by a
command, the way `uv lock` writes `uv.lock`:

```sh
useful-views cookbook           # write or update cookbook.json
useful-views cookbook --check   # exit 1 if it is missing or out of date (for CI)
```

It lists every recorded recipe in run order, upstream first, with its
description, inputs, outputs and steps, and the raw inputs the recipes read.
It is generated from `datapackage.json`, so the project must be in sync
first: `cookbook` refuses, and says why, while `check` reports anything other
than stray files. Writing an unchanged cookbook leaves the file untouched.
Commit it to git; `check` reports `stale-cookbook` when it no longer matches.

```json
{
  "version": 1,
  "generator": "useful-views",
  "project": "my-project",
  "raw_inputs": ["raw/pwt110_bra_deu_esp_usa.csv"],
  "recipes": [
    {"name": "pwt_panel", "description": "...", "steps": ["..."]},
    {"name": "gdp_per_capita", "description": "...", "steps": ["..."]}
  ]
}
```

This is version 1 of the format. Later versions are planned to add the
SHA-256 of every raw input, recipe file, step and view, and the environment
the steps ran in, so that `cookbook --check` can tell when anything changed.

## What the record does not track yet

The record says which recipe made each view, from which inputs, with which
steps. It does not yet record checksums of the files, so it cannot tell that
a raw file, a step's code or a view changed after the run. That is planned
for a future release.
