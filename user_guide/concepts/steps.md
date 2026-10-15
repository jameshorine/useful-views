---
title: "Steps"
---

A **step** is one Python file in a recipe folder, and the smallest unit of
work in a project. A step does at least one real transformation of the
data. There is no maximum: it may do many, and you decide where one step
ends and the next begins. A [recipe](recipes.md) runs its steps in order.

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

A step is an ordinary script. It does not import `useful_views`, and it can
use any data library installed in the environment.

## Names

A step file is named `NN_name.py`: two digits, an underscore and a name, such
as `01_reshape_long.py`. Steps run in numeric order; `00_` and gaps in the
numbering are fine, so you can add a step between `01_` and `02_` later by
renumbering, or leave room (`10_`, `20_`, ...). Two steps may not share a
number.

A file named almost like a step is not run, and `run` warns about it:
`1_clean.py`, `001_clean.py`, `01-clean.py`, `01 clean.py`, `01_clean.PY`,
`01_clean` with no extension, or `01_clean.py.txt` (an extension your editor
added). An R, notebook, Quarto, Julia or Stata file named like a step
(`02_clean.R`, `02_explore.ipynb`, ...) is refused: steps must be Python
files. Any other file in the recipe folder, such as notes, editor backups or
a helper module, is ignored.

## The description

The block of `#` lines at the very top of the file is the step's
description, joined into one sentence. It is required, and must not start
with TODO. It becomes the step's description in `datapackage.json`, in the
catalog and in `cookbook.json`, so write it for a reader who has not seen
the code: what the step does to the data, not how.

Lines before it are skipped: blank lines, a shebang (`#!`), a coding line,
notebook cell markers (`# %%`, `# In[1]:`), a Jupytext header, an inline
script metadata block (`# /// script` ... `# ///`) and decoration lines such
as `# -----`. A decoration line after the description ends it. A docstring
does not count; `useful-views` reads `#` comments only.

Changing a step's description is a change `sync` notices: it runs the recipe
again, and everything downstream of it.

## How a step runs

`run` and `sync` run each step as its own process:

- from the project root, so paths in the code are the same strings as in
  `recipe.toml`: `raw/...`, `work/<recipe>/...`, `useful-views/...`;
- with the same Python as useful-views, so the libraries it imports must be
  installed in that environment;
- with `work/<recipe>/` created and empty before the first step;
- with its output shown as it runs, in a terminal or a notebook cell;
- in UTF-8 mode, so `open()` reads and writes UTF-8 by default on every
  system (pass `encoding=` for a file in another encoding);
- with no keyboard input, so `input()` and `breakpoint()` fail.

The first step that fails stops the run. See
[Environment](../reference/environment.md) for every detail.

## What a step may read and write

A step reads its recipe's inputs and the files earlier steps of the same
recipe wrote to `work/<recipe>/`. It writes in-between files to
`work/<recipe>/`, in any format, and the recipe's views to the outputs
declared in `recipe.toml`.

It never writes to `raw/`, never writes another recipe's views, and never
reads another recipe's work files: to use another recipe's result, declare
its view as an input of yours. useful-views does not stop a step from doing
otherwise, but it warns when a step changed a raw input or another recipe's
view while it ran.

## Steps are code

`run` and `sync` execute steps as you: with your permissions, your
environment variables (including any passwords or tokens in them) and your
network access, with no sandbox. Read the steps of a project you did not
write before running them. See [run, sync and check](running.md).

## Code shared between steps

A step can import a module that sits next to it in its recipe folder, such
as `recipes/pwt_panel/helpers.py`; useful-views ignores files that are not
named like a step. A step cannot import from the project root. Code several
recipes share belongs in a small package installed into the environment.

Editing a helper module is like editing a step's code: `sync` does not
notice it, so use `useful-views sync --all`.

## When a step fails

Nothing is recorded, the recipe's views are removed, and its record from the
last successful run is kept. The files in `work/<recipe>/` stay as the steps
left them, so you can see how far it got. The message shows the last lines
of the step's error output and the command to run again. Two failures get
their own advice:

- `ModuleNotFoundError`: the message names the Python the step ran with and
  how to install the package into it, or says that the module is your own
  code in the wrong place.
- A step stopped by the system (on Linux and macOS, `SIGKILL`): most often
  it ran out of memory. Process the data in smaller pieces, or ask for more
  memory.

## Running a step by hand

To debug a step, run it yourself, with the project root as the working
directory, as `run` does:

```sh
python recipes/pwt_panel/01_reshape_long.py
```

Many editors run a file from its own folder by default, so set the working
directory to the project root: in VS Code, open the project folder and, for
the Interactive window, set `"jupyter.notebookFileRoot":
"${workspaceFolder}"`; in PyCharm, set the run configuration's working
directory; in Spyder, choose the current working directory under Run
settings. A step run by hand writes its files but records nothing; run the
recipe afterwards with `useful-views run` or `sync`.

Rules 2 and 3 are about steps; [Rules](../reference/rules.md) lists them all.
