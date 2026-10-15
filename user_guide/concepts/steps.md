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
).with_columns(pl.col("year").cast(pl.Int32))
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
files. Any other file in the recipe folder, such as notes, editor backups
(`.bak`, `~`, `.swp`) or a helper module like `helpers.py`, is ignored, with
two exceptions. A `.py` file whose name starts with a digit, such as
`2020_codes.py`, is not run but gets the warning above. A copy that keeps
the `NN_` prefix and `.py`, such as `01_clean copy.py` or `09_old_clean.py`,
is a step: it is refused if its number is taken, and run otherwise. Keep old
versions of a step outside the recipe folder.

## The description

The block of `#` lines at the very top of the file is the step's
description. Its lines are joined with spaces into one line, so a sentence
can wrap across several `#` lines. It is required, and must not start with
TODO. It becomes the step's description in `datapackage.json`, in the catalog
and in `cookbook.json`, so write it for a reader who has not seen the code:
what the step does to the data, not how.

Lines before it are skipped: blank lines, a shebang (`#!`), a coding line,
notebook cell markers (`# %%`, `# In[1]:`), a Jupytext header, an inline
script metadata block (`# /// script` ... `# ///`) and decoration lines such
as `# -----`. A cell marker is skipped as a whole line, including any title
written after `# %%`, so put the description on its own `#` lines below the
marker. A blank line, a cell marker or a decoration line after the
description ends it.

Any other `#` line at the top counts as description, including a licence
header (`# SPDX-License-Identifier: MIT`) and linter pragmas (`# ruff:
noqa`): put the description first, then a blank line, then those. A
docstring is not skipped and is never read as the description: put the `#`
comment before any docstring or code, or the step is refused as having no
top comment.

A step file must be saved as UTF-8 (a byte-order mark and Windows line
endings are fine). A file in another encoding is refused, even with a coding
line that declares it.

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
otherwise. While a recipe runs, it watches the files in `raw/` that recipes
declare or have recorded as inputs, and the other recipes' views, and warns
if a step changed or deleted one. It does not notice a new file in `raw/`, or
a step reading another recipe's work files. `check` reports a file in
`useful-views/` that no recipe declares as a stray file.

## Steps are code

`run` and `sync` execute steps as you: with your permissions, your
environment variables (including any passwords or tokens in them) and your
network access, with no sandbox. Read the steps of a project you did not
write before running them. See [run, sync and check](running.md).

## Code shared between steps

A step can import a module that sits next to it in its recipe folder, such
as `recipes/pwt_panel/helpers.py`; useful-views ignores files that are not
named like a step. The project root is not on a step's import path, so a
step cannot import a module that sits there. (Steps do inherit your
`PYTHONPATH`, so setting it can make such an import work for you, but the
project then fails for anyone without that setting.) Code several recipes
share belongs in a small package installed into the environment.

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

To debug a step, run it yourself from the project root, with the same Python
as useful-views, as `run` does:

```sh
python -X utf8 recipes/pwt_panel/01_reshape_long.py
```

Use the Python of the environment useful-views is installed in: activate it,
or use `uv run python ...`. A step failure from `run` names that interpreter.
`-X utf8` turns on the UTF-8 mode `run` uses. A step also expects what `run`
prepares: `work/<recipe>/` must exist (`run` creates it; before the recipe's
first run, create it yourself), and a later step reads the files earlier
steps left there, so run the recipe, or the earlier steps, first.

Many editors run a file from its own folder by default, so set the working
directory to the project root: in VS Code, open the project folder and, for
the Interactive window, set `"jupyter.notebookFileRoot":
"${workspaceFolder}"`; in PyCharm, set the run configuration's working
directory; in Spyder, choose the current working directory under Run
settings. An editor console started at the project root has the project
root on its import path instead of the step's recipe folder, so a helper
module next to the step does not import there; run the file as a script
instead.

A step run by hand writes its files but records nothing, and `sync` and
`check` do not notice it: they report everything in sync while
`datapackage.json` and the catalog still describe the views from the last
recorded run. Afterwards, run `useful-views run <recipe>` and then each
recipe downstream of it (`run` names them), or `useful-views sync --all`.

Rules 2 and 3 are about steps; [Rules](../reference/rules.md) lists them all.
