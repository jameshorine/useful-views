# {project}

TODO: say what this data project is for and who maintains it.

## Data sources

TODO: for each source, say where its raw files come from, when you got
them, their license, and how to cite them. `raw/` isn't in git, so this is
how a teammate finds the data again. Put the same details in
`sources.toml`, one `[[sources]]` block per source: useful-views copies them
into `datapackage.json` and the catalog.

## How this project works

This project uses [useful-views](https://jameshorine.github.io/useful-views/).
Every derived table, a "useful view", records the recipe that made it.

```
README.md          this file
AGENTS.md          instructions for AI coding assistants
datapackage.json   what each recipe made, from what (generated)
cookbook.json      the recipes, in the order they run (generated)
.gitignore         keeps raw data, work files and views out of git
raw/               the files you start from; not in git
recipes/           one folder per recipe
work/              in-between files, one folder per recipe; not in git
useful-views/      the views; its README.md is the catalog (generated)
```

- A **raw input** is a file in `raw/`. No recipe makes it.
- A **recipe** is a folder in `recipes/`. From the inputs it lists, its
  steps make the views it lists.
- A **step** is a numbered Python file in a recipe folder that changes the
  data at least once. How much a step does is up to you.
- **Work** files, in `work/<recipe>/`, are read only by later steps of the
  same recipe.
- A **view** is a table a recipe makes, in `useful-views/`. Other recipes
  can read it.

## Rebuild the views

Install the libraries the steps use (for example `uv sync`), put the raw
files in `raw/`, then run:

```sh
useful-views sync
```

## Write a recipe

`useful-views new-recipe <name>` makes `recipes/<name>/` with a
`recipe.toml` and a first step. Fill in both:

```toml
description = "Country-year panel of core Penn World Table variables."
inputs  = ["raw/pwt110_bra_deu_esp_usa.csv"]
outputs = ["useful-views/pwt-panel.parquet"]

[[steps]]
file = "01_reshape_long.py"
description = "Reshape the wide export into one row per country, variable and year."
```

- An input is a file under `raw/`, or a view another recipe makes. An
  output is a `.parquet` file directly inside `useful-views/`.
- Paths start at the project root, use forward slashes, and are the same
  strings the steps use in their code.
- Each step is a file named `NN_name.py`, with a `[[steps]]` block in
  `recipe.toml`, in the order they run. Change both when you add, rename or
  remove a step.
- A step is an ordinary script that runs from the project root. It passes
  data to later steps only through `work/<recipe>/`.
- Steps are code: `run` and `sync` run them with your permissions. Read a
  recipe you didn't write before you run it.

## Commands

| Command | What it does |
|---|---|
| `useful-views sync [--all]` | Make every view match its recipe, upstream first. `--all` runs every recipe. |
| `useful-views run <recipe>` | Run one recipe and record what it made. |
| `useful-views check` | Report anything missing, refused or out of date. Changes nothing. |
| `useful-views cookbook [--check]` | Write `cookbook.json`, the recipes in run order. `--check` checks it in CI, without the data. |
| `useful-views new-recipe <name>` | Start a recipe. |
| `useful-views rules` | List the rules a recipe must follow. |
| `useful-views init` | Make the project structure. Safe to run twice. |
| `useful-views example <name>` | Get a ready-made example project. |

Every command takes `--json`, for one JSON document on standard output, and
`-C <path>`, to run as if in that folder. Each is also a Python function:
`import useful_views as uvs`, then `uvs.sync()`.

| When you... | Run |
|---|---|
| add a recipe or a step, or edit a `recipe.toml` | `useful-views sync` |
| edit a step's code | `useful-views run <recipe>`, then what it says to run next |
| replace a raw file under the same name, `git pull`, or switch branches | `useful-views sync --all` |
| finish a change you'll commit | `useful-views sync`, then `useful-views cookbook` |

## What to commit

Commit everything git doesn't ignore: `README.md`, `AGENTS.md`,
`sources.toml`, `recipes/`, `datapackage.json`, `cookbook.json`,
`useful-views/README.md`, `.gitignore`, `raw/.gitkeep`, and the file that
lists your steps' libraries (`pyproject.toml` and `uv.lock`,
`requirements.txt` or `environment.yml`). Raw data, work files and views
stay out of git.
