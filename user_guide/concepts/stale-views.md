---
title: "Stale views and fingerprints"
---

A view is **stale** when the data or code it was made from has changed, but
the view hasn't been made again. A stale view looks exactly like a current
one: same file name, same columns, plausible numbers. This page shows how a
view goes stale, and what useful-views catches today.

## Why it matters

- **Wrong numbers reach a paper.** A table or figure built from a stale view
  carries the old results, and nothing in the file says so.
- **Co-authors disagree without knowing it.** Two people run "the same"
  project and get different numbers. Neither can tell whose are current.
- **AI assistants make it easier.** An assistant fixes a step, runs that one
  recipe, and moves on. Every recipe downstream of it is left out of date.

## Fingerprints

`datapackage.json` records which recipe made each view, from which inputs,
with which steps. Since version 0.2.0 it also holds a **fingerprint** of
each view, and of each view a recipe read. A fingerprint is a short code
computed from the view's contents, its [data hash](../reference/data-hash.md).
It is the same for the same data, and changes when the data changes. See
[Records](records.md#fingerprints) for where they are kept, and
[Try it: two fingerprints](../guides/fingerprints.qmd) for what they notice.

The record holds no fingerprints of raw files or of steps' code yet. `check`
never compares the views on disk with the record. `run` and `sync` compare a
view only when a recipe is about to read it.

## An example: the PWT project

The example project in [Get started](../quickstart.qmd) has two recipes.
`pwt_panel` turns the raw Penn World Table extract into a panel.
`gdp_per_capita` reads that panel and divides GDP by population. In 2019,
the panel gives the United States a population of 337.79 million and GDP of
US$23.2 trillion, so `gdp-per-capita.parquet` says US$68,683.

### A fixed step

Suppose a step in `pwt_panel` had a units mistake. It multiplied population
by 1,000, so `gdp-per-capita.parquet` gave GDP per person of US$69 a year.
You find the mistake, fix the step and run the recipe:

```sh
useful-views run pwt_panel
```

The panel is right again, but `gdp-per-capita.parquet` still says US$69. It
was made from the old panel. useful-views notices, because the fix changed
the panel's data, and so its fingerprint. `gdp_per_capita`'s record still
holds the fingerprint of the panel it read. So:

- `run` warns, naming `recipes/gdp_per_capita`;
- `check` reports it as out of date (`input-data-changed`);
- `cookbook --check` fails;
- `useful-views sync` runs it.

The fix itself isn't noticed until the recipe runs. A recipe's record holds
its steps' names and descriptions, and none of their code. If you fix the
step but don't run it, `check` says `No problems found.`

### A corrected raw file

The Penn World Table team publishes corrected figures. You replace
`raw/pwt110_bra_deu_esp_usa.csv` with the new file under the same name.
Neither recipe runs again. Both views keep the old numbers, and `check`
says `No problems found.`

### A git pull

A co-author changes `gdp_per_capita` to use output-side GDP instead of
expenditure-side GDP, runs `sync`, and pushes. You pull. Your recipe folder
and its record in `datapackage.json` arrive together and match, so `sync`
does nothing. Your `gdp-per-capita.parquet` isn't in git, so it still comes
from the old code.

`check` doesn't look inside the files on disk, so it says
`No problems found.` useful-views notices only when it is about to rely on
an old file. `run` and `sync` stop before a recipe reads a view whose file
holds other data than `datapackage.json` records, and name the recipe that
makes it.

## What is caught today

- **A changed `recipe.toml`:** its description, inputs or outputs, a step
  added, removed or renamed, or a step's description. `check` reports the
  recipe as out of date, and `sync` runs it, then every recipe downstream of
  it. An output you only remove is different. `sync` deletes that view and
  drops it from the record, without running the recipe again.
- **A view made from an older version of another:** the fingerprints in
  `datapackage.json` show it (`input-data-changed`). `check` reports it,
  `sync` runs its recipe, and `cookbook --check` fails, even in CI without
  the data.
- **The readers of a view `run` made again:** `run` warns and names them,
  and says what to run (see [Run one recipe](running.qmd#run-one-recipe)).
- **A `sync` that stops partway:** it leaves no view that looks current but
  isn't, however it stops. Before it runs anything, it removes the views it
  will make again, so the next `sync` makes them.
- **A view on disk that holds other data than its record,** left by a pull
  or a branch switch: when a recipe is about to read it, `run` and `sync`
  stop and name the recipe to run first. Nothing changes until then.

After an edit to a step's code, a raw file replaced under the same name, or
a `git pull`, make every view again:

```sh
useful-views sync --all
```

[What to run after a change](running.qmd#what-to-run-after-a-change) lists
the quicker options.

## Planned

The next step is for `check` to compare each view on disk with its record,
which would catch a `git pull` at once. Later stages record fingerprints of
raw files, code and the environment. The plan is in
[#32](https://github.com/jameshorine/useful-views/issues/32).
