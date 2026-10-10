# Quickstart

This walkthrough builds the Penn World Table example in `reference/` from an
empty folder: once in Python, as you would in a notebook, and once on the
command line. Each takes a few minutes. Do either one, or both; they make
separate projects.

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

The walkthroughs copy their raw file and recipes from the `reference/`
folder of this repository, so get a copy of it in the same folder:

<!-- quickstart-test: skip -->
```sh
git clone https://github.com/jameshorine/useful-views.git
```

You only take the example files from the clone; useful-views itself is the
installed package. Both walkthroughs start in this folder, which now holds
`useful-views/` (the clone) and `.venv/`.

For the notebook walkthrough, the notebook must run in this environment
too: install Jupyter into it (`uv add jupyterlab` or `pip install
jupyterlab`) and start it from this folder, or in VS Code choose `.venv` as
the notebook's kernel. If `import useful_views` fails in a notebook, the
notebook is running a different Python.

## In Python (a notebook)

Create the project. `init` makes the folder structure, a starter
`README.md`, the `.gitignore`, and an empty `datapackage.json`.

```python
import shutil
import useful_views as uvs

root = uvs.init("pwt-notebook")
shutil.copy("useful-views/reference/raw/pwt110_bra_deu_esp_usa.csv", root / "raw")
```

Raw files are placed by hand, and `raw/` is not committed to git, so record
where each one came from in the project's `README.md`.

Create the first recipe from the template:

```python
folder = uvs.new_recipe("pwt_panel", root=root)
```

That writes `recipes/pwt_panel/recipe.toml` and a stub step,
`01_first_step.py`. Both still say TODO, so running the recipe is refused,
with a message that says what to fix and where:

```python
try:
    uvs.run("pwt_panel", root=root)
except uvs.RecipeRefused as err:
    print(err)
```

Normally you would now write the recipe yourself. Here, replace the template
with the finished example recipe: three steps that reshape the wide export
into a country-year panel.

```python
(folder / "01_first_step.py").unlink()
shutil.copytree("useful-views/reference/recipes/pwt_panel", folder, dirs_exist_ok=True)
print((folder / "recipe.toml").read_text())
uvs.run("pwt_panel", root=root)
```

The second recipe takes the first one's view as its input:

```python
shutil.copytree("useful-views/reference/recipes/gdp_per_capita", root / "recipes" / "gdp_per_capita")
uvs.run("gdp_per_capita", root=root)
print(uvs.check(root=root))  # [] means no problems
```

`datapackage.json` now records both recipes and the files they read and
made, and `useful-views/README.md` is a catalog of the two views.

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

## On the command line

The same project, from a terminal. On Windows PowerShell, use `Copy-Item`
for `cp` and `Copy-Item -Recurse` for `cp -r`.

```sh
useful-views init pwt-cli
cp useful-views/reference/raw/pwt110_bra_deu_esp_usa.csv pwt-cli/raw/
cd pwt-cli
useful-views new-recipe pwt_panel
```

As above, the template would be refused until its TODOs are replaced. Put
the finished example recipe in place and run it:

```sh
rm recipes/pwt_panel/01_first_step.py
cp ../useful-views/reference/recipes/pwt_panel/* recipes/pwt_panel/
useful-views run pwt_panel
```

`run` runs the one recipe you name. `sync` works on the whole project, like
`uv sync`: it runs every recipe that is new, changed or missing its views,
upstream first, and removes what is no longer declared. Add the second
recipe and let `sync` work out what to run; it runs only `gdp_per_capita`,
since `pwt_panel` is already up to date:

```sh
cp -r ../useful-views/reference/recipes/gdp_per_capita recipes/
useful-views sync
useful-views check
cat useful-views/README.md
```

`useful-views check` prints `No problems found.` and exits with status 0.

## What to commit

`git init` in the project folder, then commit everything git does not
ignore: `README.md`, `recipes/`, `datapackage.json`, `.gitignore`,
`raw/.gitkeep` and `useful-views/README.md`. The raw data, `work/` and the
parquet views stay out of git. A teammate who clones the project places the
raw files as your `README.md` describes, then runs `useful-views sync`, which
makes every view again, upstream first.

If you delete a recipe folder, or remove an output from a recipe, run
`useful-views sync`: it removes the old records and view files. If you
rename a recipe folder, also change `work/<old name>/` to `work/<new name>/`
in its steps, then run `useful-views sync`. `sync` does not notice a change
inside a step's code; after editing a step, `run` that recipe, or use
`useful-views sync --all` to rebuild everything.

## References

Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next
Generation of the Penn World Table." *American Economic Review*, 105(10),
3150–3182. https://doi.org/10.1257/aer.20130954

The example uses Penn World Table version 11.0, available at
https://www.rug.nl/ggdc/productivity/pwt/ under a Creative Commons
Attribution 4.0 International License. The extract in `reference/raw/` was exported on
2026-10-09 from the PWT online data access tool; `reference/README.md` gives
the exact source. Cite the paper above whenever you use PWT data.
