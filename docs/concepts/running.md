---
title: "run, sync and check"
guide-section: Concepts
---

`run` and `sync` differ the way `uv run` and `uv sync` do: `run` does the one
recipe you name, now; `sync` brings the whole project in line with its
recipe folders. `check` reports, and changes nothing.

| You want to | Use |
|---|---|
| Run one recipe after editing it | `useful-views run <recipe>` |
| Make everything that is missing or changed, in order | `useful-views sync` |
| Rebuild every view from scratch, in order | `useful-views sync --all` |
| Know what is wrong, or what sync would do | `useful-views check` |

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
recipe must have run first; `sync` handles the order for you.

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
   missing, and everything downstream of those. It stops at the first
   failure; what ran before it stays recorded, and the error says what sync
   had already done.

On a fresh clone, place the raw files and run `useful-views sync`.

`sync` does not notice a change inside a step's code, since that needs
hashing, which is planned for a future release. After editing a step's
code, `run` that recipe and the ones downstream of it, or use
`useful-views sync --all`.

## check

`check` changes nothing. It reports what is missing or out of place, every
rule a recipe breaks, and every recipe `sync` would run, each with what to do
about it. See [Check findings](../reference/check-findings.md). It exits with
1 when it finds anything, so it works as a gate in scripts and CI.

## When you change things

| When you... | Do this |
|---|---|
| add a recipe, or edit a `recipe.toml` or a step's top comment | `useful-views sync` |
| edit the code inside a step | `useful-views sync --all` |
| delete a recipe folder, or remove an output | `useful-views sync`: it removes the old records and view files |
| rename a recipe folder | change `work/<old name>/` to `work/<new name>/` in its steps, then `useful-views sync` |
| clone the project on another computer | place the raw files, then `useful-views sync` |
