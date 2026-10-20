---
title: "Records"
---

After each successful run, useful-views rewrites two files: the record,
`datapackage.json`, and a readable catalog, `useful-views/README.md`. Never
edit them by hand; if one is damaged, restore it from git.

If a merge leaves git conflict markers in them, do not resolve the conflict
by hand: take either side (`git checkout --ours datapackage.json
useful-views/README.md`, and `cookbook.json` if the project has one), then
run `useful-views sync --all` (and `useful-views cookbook`), and `git add`
the files. useful-views says so when it finds markers in `datapackage.json`;
markers in `cookbook.json` alone are reported as `stale-cookbook`.

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

A resource's name is a view's file name, or a raw file's path below `raw/`,
without the extension, lowercased, with `/` and other characters than
`a-z`, `0-9`, `.`, `_` and `-` turned into `-`: `raw/wave1/survey.csv` is
`wave1-survey`. Accented letters lose their accents, and letters of other
scripts are written as their Unicode code point, so names stay distinct. Two
recorded files may not share one ([rule 8](../reference/rules.md)).

## useful-views/README.md

The catalog is generated from `datapackage.json` alone. For each view it
gives the recipe's description, its inputs, its numbered steps (their top
comments) and its columns with their types. It is committed to git, so a
reviewer sees what each view is and how it was made without running
anything.

## cookbook.json

From this record, `useful-views cookbook` writes a third file you commit:
`cookbook.json`, the ledger of every recipe the project uses, in run order,
with its steps. See [Cookbook](cookbook.md).

## What the record does not track yet

The record says which recipe made each view, from which inputs, with which
steps. It does not yet record checksums of the files, so it cannot tell that
a raw file, a step's code or a view changed after the run. See
[Stale views and fingerprints](stale-views.md) for what that means, what to
do today, and what is planned.
