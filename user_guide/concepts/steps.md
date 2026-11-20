---
title: "Steps"
---

A step is one Python file in a recipe folder (eventually other languages! R
users please reach out!). It is the smallest unit of work in a project. A
[recipe](recipes.md) runs its steps in order.

This is the first step of the example's `pwt_panel` recipe,
`01_reshape_long.py`:

```python
import polars as pl

wide = pl.read_csv("raw/pwt110_bra_deu_esp_usa.csv")
long = wide.unpivot(
    index=["ISO code", "Country", "Variable code", "Variable name"],
    variable_name="year",
    value_name="value",
).with_columns(pl.col("year").cast(pl.Int32))
long.write_parquet("work/pwt_panel/long.parquet")
```

The recipe's `recipe.toml` lists it, with one sentence saying what it does:

```toml
[[steps]]
file = "01_reshape_long.py"
description = """
Reshape the wide export (one column per year) into one row per country,
variable and year."""
```

A step is an ordinary script. It doesn't import `useful_views`, and it can
use any library installed in the environment. It makes at least one real
change to the data, and may make many. You decide where one step ends and
the next begins.

## Steps are code

`run` and `sync` run every step as you. A step has your permissions, your
environment variables (including any passwords or tokens in them) and your
network access. There is no sandbox.

Read the steps of a project you didn't write, or a change someone proposes,
before you run them. `check`, `rules` and `cookbook` never run a step.

## Names

A step file is named `NN_name.py`: two digits, an underscore and a name.

- Steps run in numeric order. `00_` and gaps are fine, so you can leave room
  (`10_`, `20_`, ...).
- Two steps can't share a number.
- `recipe.toml` lists exactly the step files, in the order of their numbers,
  in one `[[steps]]` block each. Its `file` is the file's name, letter case
  included.

| To | Do |
|---|---|
| add a step | add its file, and its `[[steps]]` block in the position of its number |
| rename or renumber a step | rename the file, and change its `file =` |
| remove a step | delete the file and its block |

Otherwise the recipe is refused, with a message that names what to fix.

Other files in the recipe folder:

| File | What happens |
|---|---|
| named almost like a step: `1_clean.py`, `001_clean.py`, `01-clean.py`, `01 clean.py`, `01_clean.PY`, `01_clean`, `01_clean.py.txt` | not run, and `run` warns. Refused if `recipe.toml` lists it. |
| a `.py` file whose name starts with a digit, such as `2020_codes.py` | not run, and `run` warns |
| an R, notebook, Quarto, Julia or Stata file named like a step: `02_clean.R`, `02_explore.ipynb`, ... | refused |
| a copy that keeps `NN_` and `.py`: `01_clean copy.py`, `09_old_clean.py` | a step, so refused if its number is taken or `recipe.toml` doesn't list it |
| anything else: notes, editor backups (`.bak`, `~`, `.swp`), a helper module | ignored |

Keep old versions of a step outside the recipe folder.

## The description

The `description` in a step's `[[steps]]` block is one sentence saying what
the step does to the data. It is required, and can't start with TODO.
`datapackage.json`, the catalog and `cookbook.json` show it. Write it for a
reader who hasn't seen the code. Say what the step does to the data, and
leave out how.

useful-views doesn't read the step file's comments or docstring, so the file
may start with anything. [Recipes](recipes.md#quoting) shows how to write a
long description.

## How a step runs

`run` and `sync` run each step as its own process:

- from the project root, so the paths in its code are the strings in
  `recipe.toml`;
- with the Python that runs useful-views, so the libraries it imports must
  be installed there;
- with `work/<recipe>/` created and empty before the first step;
- with its output shown as it runs, in a terminal or a notebook cell;
- in UTF-8 mode, so `open()` reads and writes UTF-8 on every system (pass
  `encoding=` for a file in another encoding);
- with no keyboard input, so `input()` and `breakpoint()` fail.

The first step that fails stops the run.
[Environment](../reference/environment.md) has every detail.

Save step files as UTF-8. A byte-order mark and Windows line endings are
fine. A step file that can't be read as UTF-8 is refused under rule 2 before
anything changes, even if a coding line declares another encoding.

## What a step reads and writes

A step:

- reads its recipe's inputs, and the files earlier steps wrote to
  `work/<recipe>/`;
- writes in-between files to `work/<recipe>/`, in any format;
- writes the recipe's views, as its `outputs` declare.

It never writes to `raw/` or to another recipe's views, and never reads
another recipe's work files. To use another recipe's result, declare its
view as an input.

useful-views doesn't stop a step that breaks these rules. While a recipe
runs, it warns if a step changed or deleted a raw file that recipes declare
or have recorded, or another recipe's view. It doesn't notice a new file in
`raw/`, or a step reading another recipe's work files. `check` reports a
file in `useful-views/` that no recipe declares as a stray file.

## Code shared between steps

A step can import a module that sits next to it, such as
`recipes/pwt_panel/helpers.py`. The project root isn't on a step's import
path. Code that several recipes share belongs in a small package installed
into the environment.

Steps inherit your `PYTHONPATH`, so setting it can make other imports work
for you. The project then fails for anyone without that setting.

`sync` doesn't notice an edit to a helper module, as with a step's code.
Run each recipe that imports it, then what each run says to run next. Or run
`useful-views sync --all`.

## When a step fails

Nothing is recorded, and the recipe's views are removed. Its record from the
last successful run is kept. The files in `work/<recipe>/` stay as the steps
left them, so you can see how far it got. The message shows the last lines
of the step's error output, and the command to run again.

Two failures get their own advice:

- `ModuleNotFoundError`: the message names the Python the step ran with, and
  how to install the package into it. Or it says the module is your own code
  in the wrong place.
- A step stopped by the system (`SIGKILL`, on Linux and macOS): most often
  it ran out of memory. Process the data in smaller pieces, or ask for more
  memory.

## Run a step by hand

To debug a step, run it from the project root with the Python that runs
useful-views:

```sh
python -X utf8 recipes/pwt_panel/01_reshape_long.py
```

- Activate the environment, or use `uv run python ...`. A step failure from
  `run` names the interpreter.
- `-X utf8` turns on the UTF-8 mode that `run` uses.
- The step expects what `run` prepares. `work/<recipe>/` must exist, and a
  later step reads what earlier steps left there. So run the recipe once
  first, or create the folder and run the earlier steps.
- An editor console started at the project root can't import a helper module
  that sits next to the step. Run the file as a script instead.

Many editors run a file from its own folder. Set the working directory to
the project root:

| Editor | Where |
|---|---|
| VS Code | Open the project folder. For the Interactive window, set `"jupyter.notebookFileRoot": "${workspaceFolder}"`. |
| PyCharm | The run configuration's working directory. |
| Spyder | The current working directory, under Run settings. |

A step run by hand records nothing, and `sync` and `check` don't notice it.
They report everything in sync, while `datapackage.json` and the catalog
still describe the last recorded run. `run` and `sync` refuse only when a
recipe is about to read such a view. Afterwards, run
`useful-views run <recipe>`, then what it says to run next. Or run
`useful-views sync --all`.

Rules 1 to 3 are about steps. [Rules](../reference/rules.md) lists them all.
