---
title: "Recipes and steps"
guide-section: Concepts
---

A **recipe** is a folder in `recipes/`. From the inputs it declares, it makes
the views it declares, by running its steps in order. A **step** is one
Python file in that folder, doing at least one real transformation of the
data; how much a step does is up to you.

```text
recipes/pwt_panel/
├── recipe.toml                 # description, inputs, outputs
├── 01_reshape_long.py          # one step; its top comment describes it
├── 02_keep_core_variables.py
└── 03_pivot_to_panel.py
```

Create one with `useful-views new-recipe <name>`. The template says TODO and
is refused until you fill it in.

## recipe.toml

```toml
description = "Country-year panel of core Penn World Table variables."
inputs  = ["raw/pwt110_bra_deu_esp_usa.csv"]
outputs = ["useful-views/pwt-panel.parquet"]
```

- `description`: one sentence saying what the recipe produces.
- `inputs`: at least one file under `raw/`, or a view another recipe makes.
- `outputs`: at least one `.parquet` file directly inside `useful-views/`.
  Each view is made by exactly one recipe.

Other keys are ignored with a warning, so check the spelling.

## Paths

Paths are relative to the project root, use forward slashes, and match the
letter case on disk: `raw/data.csv`, not `./raw/data.csv`, `raw\data.csv` or
an absolute path. A path in `recipe.toml` is the same string the step uses
in its code, and the same string another recipe uses to read the view. When
a path is written wrongly, the refusal gives the corrected string where it
can.

## Steps

```python
# Reshape the wide export (one column per year) into one row per country,
# variable and year.
import polars as pl

wide = pl.read_csv("raw/pwt110_bra_deu_esp_usa.csv")
long = wide.unpivot(
    index=["ISO code", "Country", "Variable code", "Variable name"],
    variable_name="year",
    value_name="value",
)
long.write_parquet("work/pwt_panel/long.parquet")
```

- **Names**: `NN_name.py`, two digits, an underscore and a name. Steps run in
  numeric order; `00_` and gaps are fine. `1_clean.py`, `001_clean.py` and
  `01-clean.py` are not run, and `run` warns about them. Steps must be Python
  files: an R, notebook, Quarto, Julia or Stata file named like a step is
  refused.
- **Description**: the block of `#` lines at the very top, joined into one
  sentence. It becomes the step's description in `datapackage.json` and the
  catalog. Blank lines, a shebang, a coding line, notebook cell markers and
  a Jupytext header before it are skipped. A docstring does not count.
- **How a step runs**: as its own process, with the project root as the
  working directory and the same Python as useful-views, with
  `work/<recipe>/` created and empty. It gets no keyboard input, so
  `input()` and `breakpoint()` fail inside `run`; to debug a step, run it
  yourself from the project root, as in
  `python recipes/pwt_panel/01_reshape_long.py`.
- **What a step may touch**: it reads its recipe's inputs and earlier steps'
  files in `work/<recipe>/`, and writes the recipe's outputs. It never
  writes to `raw/` and never reads another recipe's work files; to use
  another recipe's result, declare its view as an input.

Steps do not import useful_views, and you can use any data library.

The full list of what a recipe must satisfy is in [Rules](../reference/rules.md).
