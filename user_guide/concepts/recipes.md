---
title: "Recipes"
---

A recipe is a folder in `recipes/` that makes one or more views. It holds a
`recipe.toml` and one or more [steps](steps.md), and runs the steps in order.

This is the example project's first recipe, `recipes/pwt_panel/recipe.toml`:

```toml
description = "Country-year panel of core Penn World Table variables for Brazil, Germany, Spain and the United States."
inputs  = ["raw/pwt110_bra_deu_esp_usa.csv"]
outputs = ["useful-views/pwt-panel.parquet"]

[[steps]]
file = "01_reshape_long.py"
description = """
Reshape the wide export (one column per year) into one row per country,
variable and year."""

[[steps]]
file = "02_keep_core_variables.py"
description = """
Keep eight core variables: three measures of GDP, population, employment,
hours worked, human capital and TFP."""

[[steps]]
file = "03_pivot_to_panel.py"
description = "Spread the variables into columns, giving one row per country and year."
```

The folder holds that file and the three steps it lists:

```text
recipes/pwt_panel/
├── recipe.toml
├── 01_reshape_long.py
├── 02_keep_core_variables.py
└── 03_pivot_to_panel.py
```

## Make a recipe

```sh
useful-views new-recipe growth_since_2000
```

`new-recipe` writes `recipe.toml`, which lists one step, and that step's
stub file, `01_first_step.py`. The recipe is refused until you replace the
two TODO descriptions in `recipe.toml`. Replace the stub's note with your
code. If you rename the file, change its `file =` to match.

A recipe's name is its folder's name: lowercase letters, digits and
underscores, such as `pwt_panel`. It can't be `__pycache__`, which
useful-views ignores, or a name Windows keeps for devices (`con`, `aux`,
`nul`, `prn`, `com1`, ...).

## recipe.toml

| Key | What it holds |
|---|---|
| `description` | One sentence saying what the recipe produces. The catalog and the cookbook say it first about each of its views. |
| `inputs` | At least one file the steps read: a file under `raw/`, or a view another recipe makes. |
| `outputs` | At least one `.parquet` file directly inside `useful-views/`: the views the steps write. |
| `[[steps]]` | One block for each step file, in the order of their numbers, which is the order they run. |

Each `[[steps]]` block has two keys:

- `file`: the step file's name, with no path, so renaming the recipe's
  folder changes nothing here;
- `description`: one sentence saying what the step does to the data. See
  [Steps](steps.md#the-description).

Keep the `[[steps]]` blocks at the end of the file. TOML puts every line
below a `[[steps]]` header into that step. An `outputs = [...]` written below
one is read as part of the step, and useful-views tells you to move it up.

No path and no step file may be listed twice. Other keys, at the top or in a
`[[steps]]` block, are ignored with a warning, so check the spelling. Save
the file as UTF-8. A byte-order mark and Windows line endings are fine.

## Quoting

A description's spaces and line breaks are joined into one line.

- Write a long description over several lines in triple quotes, `"""..."""`.
- Put a description with double quotes in it in triple quotes too.
- A backslash starts an escape sequence. Write it as `\\`, or put the text
  in triple single quotes, `'''...'''`, which keep backslashes as they are.

## Inputs and outputs

A raw input is a file you place in `raw/` by hand. It may sit in a
subfolder, and be in any format that is a single file.

A view input is a view another recipe has made. Reading it makes that recipe
**upstream** of this one. `sync` runs the upstream recipe first, and runs
this one again whenever it runs the upstream one.
[Run, sync and check](running.md) says what `run` does then.

- A recipe needs at least one input. To use a database or a web API, save an
  extract into `raw/` with a script outside `recipes/`. Note in the
  project's `README.md` when and how, and read that file.
- Recipes can't form a cycle, and a recipe can't read its own view.
- An output is a view that only this recipe makes. In-between files, in any
  format, go in `work/<recipe>/` instead.

Every raw input and every view gets a [resource name](records.md): a view's
file name, or a raw file's path below `raw/`, without the extension and
lowercased. `raw/wave1/survey.csv` is `wave1-survey`. No two recorded files
may share one ([rule 8](../reference/rules.md)). So a recipe can't read
`raw/gdp.csv` and write `useful-views/gdp.parquet`. Call the view
`gdp-panel.parquet` instead. Nor can a recipe read both `raw/survey.csv` and
`raw/survey.dta`.

## Paths

Paths are relative to the project root, use forward slashes, and match the
letter case on disk.

| Write | Not |
|---|---|
| `raw/data.csv` | `./raw/data.csv`, `raw\data.csv`, or an absolute path |

- A path in `recipe.toml` is the same string the steps use in their code,
  and the same string another recipe uses to read the view.
- Each path names one file. There are no wildcards, so list every file a
  recipe reads: `raw/2024-01.csv`, `raw/2024-02.csv`, and so on.
- Every folder and file name must be one that can exist on Windows. It has
  no `<`, `>`, `:`, `"`, `|`, `?` or `*`, doesn't end in a dot or a space,
  and isn't a device name such as `con`, `aux` or `nul`, with or without an
  extension. Rename such files before you list them. Spaces inside a name
  are fine.

A raw input can't yet be a folder (a partitioned parquet dataset, a Zarr
store), or several files read together, such as a shapefile's `.shp`, `.dbf`
and `.shx`, which share one resource name. See
[#35](https://github.com/jameshorine/useful-views/issues/35).

## Rename or delete a recipe

- **Rename**: rename the folder, and change `work/<old name>/` to
  `work/<new name>/` in its steps. Then run `useful-views sync`. The `file`
  names in its `recipe.toml` stay as they are.
- **Delete**: delete the folder, then run `useful-views sync`. It removes the
  recipe's record, its views and its work files.
- **Remove an output**: take it out of `outputs`, then run
  `useful-views sync`. It deletes that view and drops it from the record,
  without running the recipe.

Before you delete a recipe or an output, change the `inputs` of every recipe
that reads those views, or delete it too. Otherwise its views stay as they
were, and only `check` reports the missing input.

After any other change to a recipe, run `useful-views sync`.
[Run, sync and check](running.md) lists the changes `sync` notices, and the
ones that need `sync --all`.

## When a recipe is refused

`run` and `sync` check each recipe against the
[rules](../reference/rules.md) before they change anything on disk. A
refusal names the file to fix and the rule it broke, and says how to fix it.
`sync` checks every recipe first and lists every problem at once. The common
ones:

| Refused because | Fix |
|---|---|
| a description says TODO | Replace it with one sentence. |
| an input doesn't exist | Check the path against the file, capital letters included. If it's another recipe's view, run that recipe first, or use `sync`. |
| a path is in the wrong form | Write it from the project root, with forward slashes. The message gives the corrected string where it can. Inside double quotes a backslash is a TOML escape, so then the message only says to use forward slashes. |
| the steps don't match the step files | Give each `NN_name.py` file its own `[[steps]]` block, in the order of their numbers. The message names each file that doesn't match. See [Steps](steps.md#names). |

A step that fails isn't a refusal, because the run had started. See
[When a step fails](steps.md#when-a-step-fails).

`useful-views rules` lists every rule. Rules 1 and 4 to 8 are about recipes.
