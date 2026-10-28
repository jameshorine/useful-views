---
title: "run, sync and check"
---

`run` and `sync` differ the way `uv run` and `uv sync` do: `run` does the one
recipe you name, now; `sync` brings the whole project in line with its
recipe folders. `check` reports, and changes nothing.

| You want to | Use |
|---|---|
| Run one recipe now (what it reads must already be made) | `useful-views run <recipe>` |
| Make everything that is missing or changed, in order | `useful-views sync` |
| Rebuild every view from scratch, in order | `useful-views sync --all` |
| Know what is wrong, or what sync would do | `useful-views check` |

## Steps are code

`run` and `sync` execute every step in `recipes/` as an ordinary Python
program, as you: with your permissions, your environment variables
(including any passwords or tokens in them) and your network access. There
is no sandbox. Read the recipes of a project you did not write, or a change
someone proposes, before you `run` or `sync` it, as you would read any code
before running it. `check`, `rules` and `cookbook --check` only read files,
and `cookbook` writes only `cookbook.json`; none of them runs a step.

Steps must never write to `raw/`, or to another recipe's views or work
files. useful-views does not stop a step from doing so; the rules and the
record only work if steps keep to their own outputs.

## run

Before changing anything, `run` checks the recipe against the
[rules](../reference/rules.md). Then it:

1. deletes the recipe's declared views and empties `work/<recipe>/`, so a
   leftover file can never pass for a fresh result;
2. runs each step in order, showing its output as it runs, in a terminal or
   a notebook cell. The first step that fails stops the run;
3. checks that every declared view was written as a non-empty parquet file;
4. records the run in `datapackage.json` and the catalog. Only this recipe's
   entries change; every other recipe's record stays exactly as it was.

If a step fails or a view is missing, nothing is recorded, the recipe's views
are removed, and its record from the last successful run, if any, is kept.
The files in `work/<recipe>/` stay as the steps left them. Running a recipe
twice leaves `datapackage.json` and the catalog byte-for-byte unchanged.

`run` never runs other recipes. If an input is another recipe's view, that
recipe must have run first; `sync` handles the order for you. If other
recipes read the views `run` just made again with different data, it warns
and names them: their views were made from the old ones. Their records show
it, so `check` reports them as out of date (`input-data-changed`) and
`useful-views sync` runs them. If the data came out the same, they are
still up to date, and `run` says nothing. (Records written before
fingerprints cannot show it; `run` then says to use `useful-views sync --all`.)

## sync

`sync` makes the project match its recipe folders, the way `uv sync` makes
an environment match its lock file. Records only ever come from real runs.

1. It checks every recipe against the rules, judging each against the
   records as they will be when its turn comes. If any recipe would be
   refused, it lists every problem and changes nothing.
2. It removes the records of recipes whose folder was deleted, and any view
   a recipe no longer lists, along with those view files.
3. It runs, upstream first, every recipe that has never run, whose
   `recipe.toml` or steps no longer match its record, or whose views are
   missing, and everything downstream of those.

If a recipe fails, or sync is interrupted, it stops there. What ran before
stays recorded, and the message says what sync had done, what it had not
reached, and which command to run next. Recipes it had not reached that read
views it had already made again would be left out of date, so their views
are removed: the next `sync` sees them as missing and makes them again.

On a fresh clone, place the raw files and run `useful-views sync`.

`sync` does not notice a change inside a step's code, or a raw file replaced
under the same name, until the recipe runs again: fingerprints of code and
raw files are planned for a future release. After such a change, `run`
that recipe and then `useful-views sync`, which runs the recipes downstream
whose inputs came out different (or use `useful-views sync --all`). After a
`git pull`, merge or branch switch, use `useful-views sync --all`: the views
are not in git, so they still come from the recipes as they were before.
See [Stale views and fingerprints](stale-views.md).

## One command at a time

`run`, `sync` and `cookbook` (when it writes) hold a lock on the project
while they work, in `work/.useful-views.lock`, so two of them started at
once cannot overwrite each other's records. A second one is refused with a
message saying another command is changing the project; wait, and run it
again. The operating system releases the lock when the command ends, even if
it crashes. `check`, `rules` and `cookbook --check` do not take it. The lock
file is the one thing a refused `run` or `sync` leaves behind (in `work/`,
which is not in git).

## check

`check` changes nothing. It reports what is missing or out of place, every
rule a recipe breaks, and every recipe `sync` would run, each with what to do
about it. See [Check findings](../reference/check-findings.md). It exits with
1 when it finds anything, so it works as a gate in scripts, and in CI jobs
that have the raw data and run `useful-views sync` first. A plain git
checkout has no raw files or views, so there `check` always reports them
missing; to gate such a job, use `useful-views cookbook --check` (see
[Cookbook](cookbook.md#checking-it-in-ci)).

## When you change things

| When you... | Do this |
|---|---|
| add a recipe, or edit a `recipe.toml` or a step's top comment | `useful-views sync` |
| edit the code inside a step | `useful-views run` that recipe, then `useful-views sync` (or `useful-views sync --all`) |
| replace or correct a file in `raw/` under the same name | `useful-views sync --all` (or give the new version a new file name and update `inputs`; then `sync` alone notices) |
| `git pull`, merge, or switch branches | `useful-views sync --all` |
| delete a recipe folder, or remove an output | `useful-views sync`: it removes the old records and view files (first change the inputs of any recipe that reads those views) |
| rename a recipe folder | change `work/<old name>/` to `work/<new name>/` in its steps, then `useful-views sync` |
| clone the project on another computer | place the raw files, then `useful-views sync` |
| finish a change you will commit | `useful-views sync`, then `useful-views cookbook`, then commit |
