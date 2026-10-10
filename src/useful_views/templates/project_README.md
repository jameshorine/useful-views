# {project}

TODO: say what this data project is for and who maintains it.

## Data sources and credit

TODO: for each file in `raw/`, say where it came from, when it was
downloaded, its license, and how to cite it. `raw/` is not committed to git,
so this is how a teammate finds the data again.

## How this project is organised

This project uses [useful-views](https://github.com/jameshorine/useful-views) to keep a fixed
structure, so that every derived table (a "useful view") records the recipe
that made it.

```
README.md          # this file: written by humans
datapackage.json   # generated: what each recipe made from what (do not edit)
.gitignore         # generated once by useful-views init
raw/               # raw inputs, placed by hand; never written by a recipe; not in git
recipes/           # one folder per recipe, written by humans (with or without AI tools)
work/              # in-between files, one folder per recipe; emptied before each run; not in git
useful-views/      # the views each recipe declares; README.md there is the generated catalog
```

- **Raw input**: a file in `raw/`. Nothing in `raw/` is produced by a recipe.
- **Recipe**: a folder in `recipes/`. From the inputs it declares, it
  produces the views it declares, by running its steps in order.
- **Step**: one file in a recipe folder that does at least one non-trivial
  transformation of the data. How much a step does is up to you.
- **Work**: files in `work/<recipe>/`, read only by later steps of the same
  recipe.
- **Useful view**: a declared output of a recipe, in `useful-views/`. Every
  view is made by exactly one recipe, and can be an input to other recipes.

## Writing a recipe

Create a recipe with `useful-views new-recipe <name>` (lowercase letters,
digits and underscores), then edit the two files it makes.

`recipes/<name>/recipe.toml` says what the recipe is, reads and makes:

```toml
description = "Country-year panel of core Penn World Table variables."
inputs  = ["raw/pwt110_bra_deu_esp_usa.csv"]
outputs = ["useful-views/pwt-panel.parquet"]
```

- `description`: one sentence saying what the recipe produces.
- `inputs`: at least one file under `raw/`, or a view that another recipe
  lists in its outputs and has already made.
- `outputs`: at least one `.parquet` file directly inside `useful-views/`.
- Paths start at the project root and use forward slashes. Each is the
  same string the steps use in their code to read or write that file.

Each step is a file named `NN_name.py`: two digits, an underscore and a
name, like `01_reshape_long.py`. Steps run in numeric order. The comment
block at the very top of the file says what the step does, and is required:

```python
# Reshape the wide export (one column per year) into one row per country and year.
import polars as pl

wide = pl.read_csv("raw/pwt110_bra_deu_esp_usa.csv")
...
long.write_parquet("work/pwt_panel/long.parquet")
```

- A step is an ordinary script. It runs from the project root and does not
  import `useful_views`. Use whichever data library you like, installed in
  the same Python environment as useful-views.
- Steps pass data to each other only through files in `work/<recipe>/`.
- A recipe never writes to `raw/` and never reads another recipe's work
  files. To use another recipe's result, list its view as an input.

## Commands

| Python | Command line | What it does |
|---|---|---|
| `useful_views.init()` | `useful-views init` | Create this structure. Safe to run twice. |
| `useful_views.new_recipe(name)` | `useful-views new-recipe <name>` | Create a recipe folder from a template. |
| `useful_views.run(recipe)` | `useful-views run <recipe>` | Run a recipe's steps in order and record what it produced. |
| `useful_views.check()` | `useful-views check` | Report missing inputs, missing views, recipes never run, and stray files. |
| `useful_views.sync()` | `useful-views sync` | Make the views match the recipe folders: run what is missing or changed (upstream first), and remove what is no longer declared. |
| `useful_views.root()` | | The project root, for building paths in a notebook. |

`run` runs only the recipe you name; if its input is another recipe's view,
run that recipe first. `sync` works on the whole project, like `uv sync`: on
a fresh clone, place the raw files and run `useful-views sync` to make every
view. It does not notice changes inside a step's code; after editing a step,
`run` that recipe, or use `useful-views sync --all`.
