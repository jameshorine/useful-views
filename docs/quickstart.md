---
title: "Quickstart"
guide-section: Get started
---

This walkthrough starts from a ready-made example project, runs its recipes,
and then adds a recipe of your own: once in Python, as you would in a
notebook, and once on the command line. Each takes a few minutes. Do either
one, or both; they make separate projects.

The example data is an extract of the Penn World Table, version 11.0
(Feenstra, Inklaar and Timmer 2015), for Brazil, Germany, Spain and the
United States. It is licensed under CC BY 4.0; see [References](#references).

## Setup

Work in a new, empty folder. Install useful-views together with polars,
which the example's steps use, into one Python environment. Use uv or pip,
whichever you prefer.

With uv:

<!-- quickstart-test: skip -->
```sh
uv init --bare                     # creates a pyproject.toml for this folder
uv add useful-views polars
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
```

With pip:

<!-- quickstart-test: skip -->
```sh
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install useful-views polars
```

Steps run with the same Python as useful-views, so whatever library your
steps import must be installed in that environment too: `uv add <package>`
or `pip install <package>`. (With uv you can also skip activating and put
`uv run` in front of each command, as in `uv run useful-views check`.)

For the notebook walkthrough, the notebook must run in this environment
too: install Jupyter into it (`uv add jupyterlab` or `pip install
jupyterlab`) and start it from this folder, or in VS Code choose `.venv` as
the notebook's kernel. If `import useful_views` fails in a notebook, the
notebook is running a different Python.

## In Python (a notebook)

Get the example project. `example` downloads its raw data and recipes from
the [useful-views-data](https://github.com/jameshorine/useful-views-data)
repository, checks each file against the checksum this version of
useful-views expects, and sets the folder up the way `init` would:

```python
import useful_views as uvs

root = uvs.example("pwt", "pwt-notebook")
```

It created this:

```text
pwt-notebook/
├── README.md          # what the example is, and the data credit
├── datapackage.json   # the record of what each recipe made; empty until a recipe runs
├── .gitignore         # keeps raw data, work files and views out of git
├── raw/
│   └── pwt110_bra_deu_esp_usa.csv
├── recipes/
│   ├── pwt_panel/     # recipe.toml and three steps
│   └── gdp_per_capita/
├── work/              # scratch files, emptied before each run
└── useful-views/
    └── README.md      # the catalog of views; empty until a recipe runs
```

For your own projects, `uvs.init("my-project")` makes the same structure,
empty, and you place the raw files in `raw/` by hand.

Run the first recipe:

```python
uvs.run("pwt_panel", root=root)
```

`run` checked the recipe, emptied `work/pwt_panel/`, ran the three steps in
order (their output appears as they run), checked that
`useful-views/pwt-panel.parquet` was written, and recorded the run in
`datapackage.json` and the catalog, `useful-views/README.md`. Had a step
failed, nothing would have been recorded, and the error would show the
step's own error output.

`run` does the one recipe you name. `sync` brings the whole project in line
with its recipe folders, like `uv sync`: it runs every recipe that is new,
changed or missing its views, upstream first. Here only `gdp_per_capita` is
left:

```python
result = uvs.sync(root=root)
print(result.ran)            # ('gdp_per_capita',)
print(uvs.check(root=root))  # [] means no problems
```

`datapackage.json` now records both recipes and the files they read and
made. Open the catalog, `useful-views/README.md`: for each view it gives the
recipe's description, its inputs, the numbered step descriptions (the top
comments of the step files), and the columns with their types.

In a notebook saved inside the project, such as `pwt-notebook/notebooks/`,
the working directory is that folder. `uvs.root()` finds the project from
there, so you can read a view with whichever library you like:

```python
import os
import polars as pl

os.makedirs(root / "notebooks", exist_ok=True)
os.chdir(root / "notebooks")  # Jupyter does this for a notebook saved there
gdp = pl.read_parquet(uvs.root() / "useful-views" / "gdp-per-capita.parquet")
print(gdp.filter(pl.col("year") == 2023))
```

### Write your own recipe

Add a recipe that reads the `gdp-per-capita` view and averages each
country's growth since 2000. Start from the template:

```python
folder = uvs.new_recipe("growth_since_2000", root=root)
```

That writes `recipe.toml` and a stub step, `01_first_step.py`. Both still
say TODO, so running the recipe is refused before anything changes, with a
message that says what to fix and where:

```python
try:
    uvs.run("growth_since_2000", root=root)
except uvs.RecipeRefused as err:
    print(err)
```

```text
recipes/growth_since_2000/recipe.toml needs fixing:
  - `description` still starts with TODO: replace it with one sentence saying what this recipe produces
  - `inputs` is empty: list at least one path, like inputs = ["raw/my_data.csv"]
  - `outputs` is empty: list at least one path, like outputs = ["useful-views/my-view.parquet"]
```

Normally you would now edit the two files in your editor. Here Python writes
them, so the walkthrough runs as it stands. The step's first line is its
description, and its paths are the same strings as in `recipe.toml`:

```python
(folder / "recipe.toml").write_text(
    'description = "Average annual growth of GDP per capita since 2000, by country."\n'
    'inputs  = ["useful-views/gdp-per-capita.parquet"]\n'
    'outputs = ["useful-views/growth-since-2000.parquet"]\n'
)
(folder / "01_first_step.py").unlink()
(folder / "01_average_growth.py").write_text(
    "# Average each country's annual growth of GDP per capita from 2000 on.\n"
    "import polars as pl\n"
    "\n"
    'gdp = pl.read_parquet("useful-views/gdp-per-capita.parquet")\n'
    "growth = (\n"
    '    gdp.filter(pl.col("year") >= 2000)\n'
    '    .group_by("iso", "country")\n'
    '    .agg(pl.col("growth_pct").mean().round(2).alias("avg_growth_pct"))\n'
    '    .sort("iso")\n'
    ")\n"
    'growth.write_parquet("useful-views/growth-since-2000.parquet")\n'
)
print(uvs.sync(root=root).ran)  # ('growth_since_2000',)
print(pl.read_parquet(uvs.root() / "useful-views" / "growth-since-2000.parquet"))
```

The catalog now lists three views.

## On the command line

The same, from a terminal:

```sh
useful-views example pwt pwt-cli
cd pwt-cli
useful-views run pwt_panel
useful-views sync
useful-views check
cat useful-views/README.md
```

`sync` runs only `gdp_per_capita`, since `pwt_panel` is already up to date,
and `useful-views check` prints `No problems found.` To add a recipe of your
own, `useful-views new-recipe <name>` writes the template; edit its two files
as above, then `useful-views sync`.

## What to commit

`git init` in the project folder, then commit everything git does not
ignore: `README.md`, `recipes/`, `datapackage.json`, `.gitignore`,
`raw/.gitkeep` and `useful-views/README.md`. The raw data, `work/` and the
parquet views stay out of git. A teammate who clones the project places the
raw files as your `README.md` describes, then runs `useful-views sync`, which
makes every view again, upstream first.

## Changing things later

| When you... | Do this |
|---|---|
| add a recipe, or edit a `recipe.toml` or a step's top comment | `useful-views sync`: it runs that recipe and everything downstream of it |
| edit the code inside a step | `useful-views sync --all`, or `run` that recipe and then each recipe downstream of it. (`sync` alone does not notice code changes yet.) |
| delete a recipe folder, or remove an output from a recipe | `useful-views sync`: it removes the old records and view files |
| rename a recipe folder | change `work/<old name>/` to `work/<new name>/` in its steps, then `useful-views sync` |
| clone the project on another computer | place the raw files, then `useful-views sync` |
| want to know if anything is missing or broken | `useful-views check` (it does not notice edited recipes; `useful-views sync` runs what changed) |

## When something is refused

Every refusal names the file to fix and says how. `run` refuses before
anything on disk changes; `sync` checks each recipe just before running it,
so what it has already done is kept, and the message says so. The common
ones:

- **An input does not exist**: check the path in `recipe.toml` against the
  file, including capital letters. If it is another recipe's view, run that
  recipe first, or use `sync`.
- **A path is written in the wrong form**: paths are relative to the project
  root with forward slashes, like `raw/data.csv`. The message gives the
  corrected string where it can.
- **A step has no top comment**: start the step file with a `#` comment
  saying what it does. A docstring does not count.
- **A step failed**: this is not a refusal. The run had started, so the
  recipe's views were removed and nothing was recorded; `work/<recipe>/`
  shows how far it got. The message shows the last lines of the step's error
  output. A `ModuleNotFoundError` means the package is not installed in the
  environment useful-views runs in; the message gives the install command.

The README lists [every rule](https://github.com/jameshorine/useful-views#rules).

## References

Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next
Generation of the Penn World Table." *American Economic Review*, 105(10),
3150–3182. https://doi.org/10.1257/aer.20130954

The example uses Penn World Table version 11.0, available at
https://www.rug.nl/ggdc/productivity/pwt/ under a Creative Commons
Attribution 4.0 International License. The extract was exported on
2026-10-09 from the PWT online data access tool; the example project's
`README.md` gives the exact source. Cite the paper above whenever you use
PWT data.
