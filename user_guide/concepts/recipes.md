---
title: "Recipes"
---

A **recipe** is a folder in `recipes/`: a `recipe.toml` and one or more
[steps](steps.md). From the inputs it declares, it makes the views it
declares, by running its steps in order. Every view is made by exactly one
recipe, and records that recipe.

```text
recipes/pwt_panel/
├── recipe.toml                 # description, inputs, outputs
├── 01_reshape_long.py          # the steps, run in numeric order
├── 02_keep_core_variables.py
└── 03_pivot_to_panel.py
```

## Making one

```sh
useful-views new-recipe pwt_panel
```

This writes `recipes/pwt_panel/recipe.toml` and a stub step,
`01_first_step.py`. Both say TODO, and the recipe is refused until you
replace them. A recipe's name is its folder's name: lowercase letters,
digits and underscores, such as `pwt_panel`. It may not be a name Windows
reserves for devices (`con`, `aux`, `nul`, `prn`, `com1`, ...), or
`__pycache__`, which useful-views ignores.

## recipe.toml

```toml
description = "Country-year panel of core Penn World Table variables."
inputs  = ["raw/pwt110_bra_deu_esp_usa.csv"]
outputs = ["useful-views/pwt-panel.parquet"]
```

- `description`: one sentence saying what the recipe produces. It is the
  first thing the catalog and the cookbook say about the recipe's views.
- `inputs`: at least one file the steps read: a file under `raw/`, or a view
  another recipe makes.
- `outputs`: at least one `.parquet` file directly inside `useful-views/`:
  the views the steps write.

No path may be listed twice. Other keys are ignored with a warning, so check
the spelling.

## Inputs and outputs

A raw input is a file you place in `raw/` by hand; it may sit in a subfolder
and be in any format that is a single file. A view input is another recipe's
output. Reading it makes that recipe **upstream** of this one: `sync` runs
it first, and runs this one again whenever the upstream one makes its view
again with different data. `useful-views run` on the upstream recipe runs
only that one: if its view changed, it warns and names this one, `check`
reports this one as out of date, and `sync` runs it (see
[Fingerprints](records.md#fingerprints)). Recipes may not form a cycle, and a recipe may not read
its own view.

An output is a view that only this recipe makes. Views are parquet files, so
the record can say exactly which columns and types each one has. In-between
files, in any format, belong in `work/<recipe>/` instead; see
[Steps](steps.md).

Every raw input and every view also gets a resource name: a view's file
name, or a raw file's path below `raw/`, without the extension and
lowercased (`raw/wave1/survey.csv` is `wave1-survey`; see
[Records](records.md)). No two files recorded anywhere in the project may
share one ([rule 8](../reference/rules.md)). So a recipe cannot read
`raw/gdp.csv` and write `useful-views/gdp.parquet` (call the view
`gdp-panel.parquet`), and cannot read both `raw/survey.csv` and
`raw/survey.dta`.

A recipe needs at least one input. To use data from a database or a web API,
save an extract into `raw/` first, with a script outside `recipes/`, note in
the project's `README.md` when and how, and read that file.

## Paths

Paths are relative to the project root, use forward slashes, and match the
letter case on disk: `raw/data.csv`, not `./raw/data.csv`, `raw\data.csv` or
an absolute path. A path in `recipe.toml` is the same string the steps use
in their code, and the same string another recipe uses to read the view.
When a path is written wrongly, the refusal gives the corrected string where
it can.

Each path names one file. There are no wildcards, so list every file a
recipe reads (`raw/2024-01.csv`, `raw/2024-02.csv`, ...). Formats made of a
folder (a partitioned parquet dataset, a Zarr store) or of several files read
together (a shapefile's `.shp`, `.dbf` and `.shx`, which share one resource
name) are not supported yet.

Every folder and file name in a path must be one that can exist on Windows:
no `<`, `>`, `:`, `"`, `|`, `?` or `*`, not ending in a dot or a space, and
not a device name such as `con`, `aux` or `nul`, with or without an
extension. Rename such files before listing them. Spaces inside a name are
fine.

## Changing a recipe

`sync` compares each recipe folder with its record from the last successful
run. It notices a changed description, inputs or outputs in `recipe.toml`, a
step added, removed or renamed, and a step's changed description, and runs
the recipe again, with everything downstream of it. An output you only
remove is different: `sync` deletes that view and drops it from the record,
without running the recipe again.

It does not notice edits inside a step's code, a helper module, or a raw
file replaced under the same name. Nor does it notice a recipe change that
arrives together with its record from a `git pull`, merge or branch switch:
the record then already matches the folder, while the views, which are not
in git, still come from the recipe as it was. After any of those, use
`useful-views sync --all`.

To rename a recipe, rename its folder, change `work/<old name>/` to
`work/<new name>/` in its steps, then run `useful-views sync`. To delete
one, delete its folder and run `useful-views sync`, which removes its record,
its views and its work files. If another recipe reads one of those views,
change that recipe's `inputs` first, or delete it too: otherwise its own
views stay as they were, and only `check` reports the missing input. The
same goes for removing an output that another recipe reads. See
[run, sync and check](running.md).

Rules 1 and 4 to 8 are about recipes; [Rules](../reference/rules.md) lists
them all.
