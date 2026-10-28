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
  each with its columns and their types. A view also has its `fingerprint`
  and `producedBy`: the recipe that made it, that recipe's inputs, and the
  fingerprints those inputs had when it ran (see
  [Fingerprints](#fingerprints) below).
- `recipes` lists each recorded recipe with its description, inputs,
  outputs and steps, as they were at its last successful run.

Recipes, and the views under them, are listed in the order they run, so the
file reads in the order the data is made. Each view starts with what it is
and where it came from; its columns come last. Anything short, such as a
column or a list of paths, is on one line.

```json
{
  "name": "gdp-per-capita",
  "path": "useful-views/gdp-per-capita.parquet",
  "fingerprint": "datahash1:sha256:69378218dc105ea3cc282fc234f9e6646592d4877692a013eb0cf67305e5cfe1",
  "producedBy": {
    "recipe": "gdp_per_capita",
    "inputs": ["useful-views/pwt-panel.parquet"],
    "inputFingerprints": {
      "useful-views/pwt-panel.parquet": "datahash1:sha256:bdc104f8b4ba8296317c5c9eb9b5500cece148f7c7e36318d423ba60fa806d14"
    }
  },
  "type": "table",
  "scheme": "file",
  "format": "parquet",
  "mediatype": "application/parquet",
  "schema": {
    "fields": [
      {"name": "iso", "type": "string"},
      {"name": "year", "type": "integer"}
    ]
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

## Fingerprints

When a recipe runs, useful-views takes two kinds of fingerprint, both
[data hashes](../reference/data-hash.md) of a view's contents:

- just before the steps run, the fingerprint of each view the recipe reads,
  recorded in `producedBy.inputFingerprints`;
- right after they succeed, the fingerprint of each view it made, recorded
  in `fingerprint`.

This is how the parts of `datapackage.json` refer to each other:

```text
datapackage.json
├── resources                    one entry per file
│   ├── a raw file               name, path, format, schema
│   └── a view
│       ├── name, path           the view's path identifies it
│       ├── fingerprint          what the view holds, as a data hash
│       ├── producedBy
│       │   ├── recipe           → the recipe of that name in `recipes`
│       │   ├── inputs           → the paths of the raw files and views it read
│       │   └── inputFingerprints  { path of each view in `inputs` : its fingerprint then }
│       └── schema               its columns and their types
└── recipes                      one entry per recipe, in run order
    └── a recipe                 name, description, path, inputs, outputs, steps
```

A view's `inputFingerprints` says what each view it read held at the time,
and that view's own `fingerprint` says what it holds now. In the quickstart
project, with the fingerprints shortened to four characters:

| View | Its fingerprint | Made from | That input's fingerprint then |
|---|---|---|---|
| pwt-panel | `bdc1` | the raw CSV | (raw files have none yet) |
| gdp-per-capita | `6937` | pwt-panel | `bdc1` |

The two `bdc1`s match: gdp-per-capita was made from pwt-panel as it is. Fix a
step of `pwt_panel` and run only that recipe, and if the fix changes the data,
pwt-panel's fingerprint changes, say to `7a2e`, while gdp-per-capita still
records `bdc1`: it was made from the old pwt-panel.

A view whose columns the data hash does not cover yet (a list, struct or map
in each cell) has the fingerprint `null`. Records written before
fingerprints, by useful-views 0.1, have none; they stay valid, and each view
gets its fingerprint the next time its recipe runs (`useful-views sync
--all` runs them all).

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
steps, and the fingerprints of the views involved. `check` and `sync`
compare those fingerprints with each other, so they report a view made
from an older version of another (`input-data-changed`). The record does
not yet hold fingerprints of raw files or of steps' code, and nothing yet
compares a view's file on disk with its record, as is needed after a
`git pull`. See
[Stale views and fingerprints](stale-views.md) for what that means, what to
do today, and what is planned.
