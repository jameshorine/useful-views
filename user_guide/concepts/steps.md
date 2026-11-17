---
title: "Steps"
---

A **step** is one Python (eventually other languages! R users please reach out!) file in a recipe folder, and the smallest unit of
work in a project. A step does at least one real transformation of the
data. There is no maximum: it may do many, and you decide where one step
ends and the next begins. A [recipe](recipes.md) runs its steps in order.

The recipe's `recipe.toml` lists each step, with one sentence saying what it
does:

```toml
[[steps]]
file = "01_reshape_long.py"
description = """
Reshape the wide export (one column per year) into one row per country,
variable and year."""
```

The step file, `01_reshape_long.py`, is the code:

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

A step is an ordinary script. It does not import `useful_views`, and it can
use any data library installed in the environment. It may start with
anything (imports, a docstring or comments): useful-views does not read what
is in it.

## Names

A step file is named `NN_name.py`: two digits, an underscore and a name, such
as `01_reshape_long.py`. Steps run in numeric order; `00_` and gaps in the
numbering are fine, so you can add a step between `01_` and `02_` later by
renumbering, or leave room (`10_`, `20_`, ...). Two steps may not share a
number.

`recipe.toml` lists exactly the step files in the folder, in the order of
their numbers: one `[[steps]]` block for each, whose `file` is the file's
name, letter case included. So to add a step, add its file and a `[[steps]]`
block for it; to rename or renumber one, rename the file and change its
`file =` to match; to remove one, delete both. Otherwise the recipe is
refused, with a message that names what to fix.

A file named almost like a step is not run, and `run` warns about it:
`1_clean.py`, `001_clean.py`, `01-clean.py`, `01 clean.py`, `01_clean.PY`,
`01_clean` with no extension, or `01_clean.py.txt` (an extension your editor
added). Listed in `recipe.toml`, such a file is refused instead. An R,
notebook, Quarto, Julia or Stata file named like a step (`02_clean.R`,
`02_explore.ipynb`, ...) is refused: steps must be Python files. Any other
file in the recipe folder, such as notes, editor backups (`.bak`, `~`,
`.swp`) or a helper module like `helpers.py`, is ignored, with two
exceptions. A `.py` file whose name starts with a digit, such as
`2020_codes.py`, is not run but gets the warning above. A copy that keeps
the `NN_` prefix and `.py`, such as `01_clean copy.py` or `09_old_clean.py`,
is a step: the recipe is refused if its number is taken or `recipe.toml`
does not list it. Keep old versions of a step outside the recipe folder.

## The description

A step's description is the `description` in its `[[steps]]` block in
`recipe.toml`: one sentence saying what the step does to the data. It is
required, and must not start with TODO. It becomes the step's description in
`datapackage.json`, in the catalog and in `cookbook.json`, so write it for a
reader who has not seen the code: what the step does to the data, not how.

Its spaces and line breaks are joined into one line, so a long description
can be written over several lines in triple quotes, as above. Comments and
docstrings in the step file are not read. See [Recipes](recipes.md) for the
rest of `recipe.toml`.

Changing a step's description is a change `sync` notices: it runs the recipe
again, and every recipe downstream of it.

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
notice it until a recipe that uses it runs. Run each recipe that imports
it, then what each run says to run next; or use `useful-views sync --all`.

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
recorded run. (Only when a recipe is about to read such a view do `run` and
`sync` see that it is not the recorded one, and refuse.) Afterwards, run `useful-views run <recipe>` and then what it says to
run next, or `useful-views sync --all`.

Rules 2 and 3 are about steps, and rule 1 covers their `[[steps]]` blocks in
`recipe.toml`; [Rules](../reference/rules.md) lists them all.
