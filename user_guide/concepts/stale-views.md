---
title: "Stale views and fingerprints"
---

A view is **stale** when the data or code it was made from has changed since,
but the view has not been made again. A stale view looks exactly like a
current one: same file name, same columns, plausible numbers. This page
explains how a view goes stale, what useful-views catches today, what it does
not, and what is planned to close the gap: fingerprints.

## Why this matters

- **Wrong numbers reach a paper.** A table or figure built from a stale view
  carries the old results, and nothing in the file says so.
- **Co-authors disagree without knowing it.** Two people run "the same"
  project and get different numbers, and neither can tell whose are current.
- **AI assistants make it easier.** An assistant fixes a step, runs that one
  recipe, and moves on, leaving every recipe downstream of it out of date.

The record in `datapackage.json` says which recipe made each view, from
which inputs, with which steps. It does not yet say *what those files
contained* when the view was made. That is the gap.

## An example: the PWT project

The [quickstart](../quickstart.md) builds two recipes: `pwt_panel` turns the
raw Penn World Table extract into a panel, and `gdp_per_capita` reads that
panel and divides GDP by population. In 2019, the panel gives the United
States a population of 337.79 million and GDP of US$23.2 trillion, so
`gdp-per-capita.parquet` says US$68,683.

### A fixed step

Suppose a step in `pwt_panel` had a units mistake: it multiplied population
by 1,000, so `gdp-per-capita.parquet` said Americans earned US$69 a year. You
find the mistake, fix the step and run the recipe:

```sh
useful-views run pwt_panel
```

The panel is right again, but `gdp-per-capita.parquet` still says US$69: it
was made from the old panel. `run` warns once, naming `recipes/gdp_per_capita`.
Miss that warning, and nothing else tells you: `check` says
`No problems found.`, `sync` says everything is in sync, and
`cookbook --check` passes. A recipe's record holds its steps' names and top
comments, not their code, so a fix inside a step's code changes nothing that
useful-views compares.

### A corrected raw file

The Penn World Table team publishes corrected figures, and you replace
`raw/pwt110_bra_deu_esp_usa.csv` with the new file under the same name.
Neither recipe runs again. Both views keep the old numbers, and `check` says
`No problems found.`

### A git pull

A co-author changes `gdp_per_capita` to use output-side GDP instead of
expenditure-side GDP, runs `sync`, and pushes. You pull. Your recipe folder
and its record in `datapackage.json` arrived together and match, so `sync`
does nothing, while your `gdp-per-capita.parquet`, which is not in git, still
comes from the old code.

## What to do today

After any of these, make every view again:

```sh
useful-views sync --all
```

| When you... | Do this |
|---|---|
| fix or change the code inside a step, or a helper module | `useful-views sync --all` (or `run` that recipe, then each recipe `run` names) |
| replace a file in `raw/` under the same name | `useful-views sync --all`, or give the new version a new name and update `inputs`, which `sync` notices |
| `git pull`, merge, or switch branches | `useful-views sync --all` |

What useful-views does catch today:

- an edited `recipe.toml`, a step added, removed or renamed, and a step's
  changed top comment: `check` reports the recipe as out of date, and `sync`
  runs it and everything downstream of it;
- after `run`, a warning naming the recipes that read the views it just made
  again;
- when `sync` stops partway, it removes the views it would have left stale,
  so the next `sync` makes them again.

## What is planned: fingerprints

A **fingerprint** is a short code computed from a file's contents, such as
its SHA-256 hash. The same contents always give the same fingerprint, and
any change, even one byte, gives a different one. If useful-views records the
fingerprints of everything a view was made from, it can recompute them later
and know, rather than guess, whether the view is current. This is planned
in stages.

**1. Inputs and code.** The fingerprints of each recipe's raw inputs, its
`recipe.toml`, its step files and the helper modules beside them, recorded
when the recipe runs. That alone catches all three cases above: `check` and
`sync` would see that the panel, the raw file or the step changed, and run
what is out of date. Because the fingerprints of raw files would be
committed, a co-author's `check` could also say that their copy of a raw file
is not the one the views were made from. Large files are hashed again only
when their size or modification time changed, as git does.

**2. Views, two ways.** A view gets two fingerprints, because "the same view"
can mean two things:

| Fingerprint | Computed from | Answers |
|---|---|---|
| **File hash** | the file's bytes | Is this the very file that was recorded? |
| **Data hash** | the column names and types and the values, in a fixed form | Does this view hold the same data as the recorded one, whoever made it? |

The file hash alone is not enough, because a parquet file's bytes depend on
the library that wrote it, not only on the data. The sensor readings from
[Try it: two fingerprints](../guides/fingerprints.md), saved once with polars
and once with pyarrow:

| | Written by polars | Written by pyarrow |
|---|---|---|
| Same data? | yes | yes |
| File hash | `49ad3771…` | `1d3019f4…` |
| Data hash | `76c92a8b…` | `76c92a8b…` |

So the file hash is exact on one computer, and the data hash is what a
co-author, a reviewer or a CI job can compare. To see both for yourself,
including how row order and the last digit of a number affect them, work
through [Try it: two fingerprints](../guides/fingerprints.md).
`uvs.data_hash()` already computes the data hash of any parquet file, by the
[data hash rules](../reference/data-hash.md); recording it is what is planned.

**3. Deterministic results.** With both, useful-views could also check that
a recipe gives the same data from the same inputs and code. When it does
not, a step depends on something it should not, such as rows in an unsorted
`group_by`, a random number without a seed, or the current time, and its
results cannot be reproduced exactly.

**4. The environment.** The versions of the libraries the steps ran with,
since the same step can give different numbers after an upgrade.

These fingerprints are planned to go into the [cookbook](cookbook.md), and
the raw files' into `datapackage.json`, whose standard already has a `hash`
field for them.
