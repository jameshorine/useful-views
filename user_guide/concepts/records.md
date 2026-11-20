---
title: "Records"
---

After each successful run, useful-views rewrites two files. The record,
`datapackage.json`, says what each recipe made from what. The catalog,
`useful-views/README.md`, says the same for people. Never edit them by hand.
If one is damaged, restore it from git.

## datapackage.json

The record is a [Data Package](https://datapackage.org/), a standard format
many tools can read. `frictionless validate datapackage.json` passes on it
whenever every recorded file is present. It sits at the project root,
because it describes files in both `raw/` and `useful-views/`.

It holds two lists:

- `resources`: every raw file a recorded recipe read, then every view, each
  with its columns and their types. A raw file also has its `sources` and
  `licenses` when [`sources.toml`](sources.md) describes it. A view also
  has its `fingerprint`, and `producedBy`. `producedBy` names the recipe
  that made the view and lists that recipe's inputs. For each input that is
  a view, it gives the fingerprint that view had when the recipe ran.
- `recipes`: each recorded recipe, with its description, path, inputs,
  outputs and steps, as they were at its last successful run.

Recipes, and the views under them, are listed in the order they run, so the
file reads in the order the data is made. Each view starts with what it is
and where it came from, and its columns come last. Anything short, such as a
column or a list of paths, is on one line. Here is the view
`gdp-per-capita` from the example project, with its columns cut to two:

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

A field this version doesn't write, perhaps from a newer useful-views, is
never an error, and never makes a recipe out of date. It is kept when the
file is rewritten, except in the entries a run writes afresh. Those are the
entries of the recipe that ran, its views, and the raw files it read.

### Columns

Column types come from the files themselves. A parquet file has its own
types. A raw CSV's types are inferred from the whole file. In a raw CSV,
only an empty cell counts as missing, so a column that marks missing values
with `NA` or `.` is recorded as `string`. Other raw formats (`.dta`,
`.xlsx`, ...), and CSVs that aren't plain comma-separated UTF-8, are
recorded without columns.

### Names

A resource's name is a view's file name, or a raw file's path below `raw/`,
without the extension. It is lowercased, and `/` and any character other
than `a-z`, `0-9`, `.`, `_` and `-` become `-`. So `raw/wave1/survey.csv`
is `wave1-survey`. Accented letters lose their accents, and letters of other
scripts are written as their Unicode code point, so names stay distinct. Two
recorded files may not share a name ([rule 8](../reference/rules.md)).

## Fingerprints

*New in version 0.2.0.* When a recipe runs, useful-views takes fingerprints
at two moments. Both are [data hashes](../reference/data-hash.md) of a
view's contents.

- Just before the steps run, it takes one of each view the recipe reads. It
  records them in `producedBy.inputFingerprints`, by each view's path.
- Right after the steps succeed, it takes one of each view the recipe made,
  in `fingerprint`.

A view's `inputFingerprints` says what each view it read held at the time.
That view's own `fingerprint` says what it holds now. In the example
project, with the fingerprints cut to four characters:

| View | Its fingerprint | Made from | That input's fingerprint then |
|---|---|---|---|
| pwt-panel | `bdc1` | the raw CSV | (raw files have none yet) |
| gdp-per-capita | `6937` | pwt-panel | `bdc1` |

The two `bdc1`s match, so gdp-per-capita was made from pwt-panel as it is.
Fix a step of `pwt_panel` and run only that recipe. If the fix changes the
data, pwt-panel's fingerprint changes, say to `7a2e`, while gdp-per-capita
still records `bdc1`. It was made from the old pwt-panel.

Some views have no fingerprint that can be compared:

- A view with a column the data hash doesn't cover yet has the fingerprint
  `null`. That is a list, struct or map in each cell, or a column the file
  labels GEOMETRY or GEOGRAPHY.
- Records written by useful-views 0.1 have no fingerprints. They stay valid,
  and each view gets its fingerprint the next time its recipe runs
  (`useful-views sync --all` runs them all).
- A fingerprint this version doesn't understand, perhaps written by a newer
  useful-views, counts as missing.

In each of these cases, `check` and `sync` can't tell whether the recipes
that read the view are up to date, and say so in a note.

Taking fingerprints means reading each view a recipe makes or reads once
more. `run` also reads the file a view replaces, when other recipes read it.
Hashing runs at about 22 million values a second on a laptop. That is a few
seconds for most views, and about 2 minutes for a 15 GB one (see
[Speed](../reference/data-hash.md#speed)).

For what the record doesn't track yet, see
[Stale views](stale-views.md) and
[#32](https://github.com/jameshorine/useful-views/issues/32).

## useful-views/README.md

The catalog is generated from `datapackage.json` alone. For each view, it
gives the recipe's description, its inputs, its numbered steps (with their
descriptions from `recipe.toml`) and its columns with their types. It is
committed to git, so a reviewer sees what each view is and how it was made,
without running anything.

## cookbook.json

From this record, `useful-views cookbook` writes a third file you commit.
`cookbook.json` is the ledger of every recipe the project uses, in run
order, with its steps. See [Cookbook](cookbook.md).

## Merge conflicts

If a merge leaves git conflict markers in these files, don't resolve them
by hand:

1. Take either side: `git checkout --ours datapackage.json
   useful-views/README.md`, and `cookbook.json` if the project has one.
2. Run `useful-views sync --all`, then `useful-views cookbook` if the
   project has a cookbook.
3. `git add` the files.

useful-views says so when it finds markers in `datapackage.json`. Markers in
`cookbook.json` alone are reported as `stale-cookbook`.
