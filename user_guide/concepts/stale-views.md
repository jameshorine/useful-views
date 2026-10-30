---
title: "Stale views and fingerprints"
---

A view is **stale** when the data or code it was made from has changed since,
but the view has not been made again. A stale view looks exactly like a
current one: same file name, same columns, plausible numbers. This page
explains how a view goes stale, what useful-views catches today with
fingerprints (new in version 0.2.0), what it does not catch yet, and what
is planned.

## Why this matters

- **Wrong numbers reach a paper.** A table or figure built from a stale view
  carries the old results, and nothing in the file says so.
- **Co-authors disagree without knowing it.** Two people run "the same"
  project and get different numbers, and neither can tell whose are current.
- **AI assistants make it easier.** An assistant fixes a step, runs that one
  recipe, and moves on, leaving every recipe downstream of it out of date.

The record in `datapackage.json` says which recipe made each view, from
which inputs, with which steps. Since version 0.2.0 it also holds a
**fingerprint** of each view, and of each view a recipe read: a short code
computed from the view's contents (its [data hash](../reference/data-hash.md)),
the same for the same data and different when the data changes. It does
not yet hold fingerprints of raw files or of steps' code, and the views on
disk are compared with the record only when a recipe is about to read
them; `check` never looks. That is the gap that remains.

## An example: the PWT project

The example project in the [quickstart](../quickstart.md) has two recipes: `pwt_panel` turns the
raw Penn World Table extract into a panel, and `gdp_per_capita` reads that
panel and divides GDP by population. In 2019, the panel gives the United
States a population of 337.79 million and GDP of US$23.2 trillion, so
`gdp-per-capita.parquet` says US$68,683.

### A fixed step

Suppose a step in `pwt_panel` had a units mistake: it multiplied population
by 1,000, so `gdp-per-capita.parquet` gave GDP per person of US$69 a year. You
find the mistake, fix the step and run the recipe:

```sh
useful-views run pwt_panel
```

The panel is right again, but `gdp-per-capita.parquet` still says US$69: it
was made from the old panel. useful-views notices: the fix changed the
panel's data, and so its fingerprint, while `gdp_per_capita`'s record still
holds the fingerprint of the panel it read. `run` warns, naming
`recipes/gdp_per_capita`; `check` reports it as out of date
(`input-data-changed`); `cookbook --check` fails; and `useful-views sync`
runs it.

What is not noticed is the fix itself, until the recipe runs. A recipe's
record holds its steps' names and top comments, not their code, so if you
fix the step but do not run it, `check` says `No problems found.`

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

`check` does not look inside the files on disk, so it says
`No problems found.` useful-views notices only when it is about to rely on
the old file: `run` and `sync` stop before a recipe reads a view whose file
holds other data than `datapackage.json` records, and name the recipe that
makes it.

## What to do today

The simplest answer, after any of these, is to make every view again:

```sh
useful-views sync --all
```

| When you... | Do this |
|---|---|
| fix or change the code inside a step, or a helper module | `useful-views run` that recipe, then what it says to run next (or `useful-views sync --all`) |
| replace a file in `raw/` under the same name | `useful-views sync --all`, or give the new version a new name, and change the path in `inputs` and in the step that reads it, which `sync` notices |
| `git pull`, merge, or switch branches | `useful-views sync --all` |

What useful-views does catch today:

- an edited `recipe.toml`, a step added, removed or renamed, and a step's
  changed top comment: `check` reports the recipe as out of date, and `sync`
  runs it, then every recipe downstream of it;
- a view made from an older version of another: the fingerprints in
  `datapackage.json` show it (`input-data-changed`), so `check` reports it,
  `sync` runs its recipe, and `cookbook --check` fails, even in CI without
  the data;
- after `run`, a warning naming the recipes that read the views it just made
  again: out of date when the data changed (`sync` runs them); when it came
  out the same, to run them too if what changed is how the data is stored
  (a time zone's name, a column's storage type or unit, its categories),
  which a data hash does not show; and when the records cannot show which,
  why, and to use `useful-views sync --all`;
- a `sync` that stops partway, however it stops, leaves no view that looks
  current but is not: before it runs anything, it removes the views it will
  make again, so the next `sync` makes them;
- a view on disk that holds other data than its record (left by a pull or a
  branch switch), when a recipe is about to read it: `run` and `sync` stop
  and name the recipe to run first. Nothing changes until then.

## Fingerprints: what is done and what is planned

If useful-views records the fingerprints of everything a view was made from,
it can compare them later and know, rather than guess, whether the view is
current. This is being built in stages; the views' data hashes, part of
stage 2, came first, in version 0.2.0.

**1. Inputs and code.** The fingerprints of each recipe's raw inputs, its
`recipe.toml`, its step files and the helper modules beside them, recorded
when the recipe runs. With the views' fingerprints already recorded, that
would catch all three cases above, and notice a changed raw file or step
before the recipe runs again. Because the fingerprints of raw files would be
committed, a co-author's `check` could also say that their copy of a raw file
is not the one the views were made from. Large files are hashed again only
when their size or modification time changed, as git does.

**2. Views, two ways.** A view can have two fingerprints, because "the same
view" can mean two things. The data hash is recorded since version 0.2.0;
the file hash is planned.

| Fingerprint | Computed from | Answers |
|---|---|---|
| **File hash** | the file's bytes | Is this the very file that was recorded? |
| **Data hash** | the column names and types and the values, in a fixed form | Does this view hold the same data as the recorded one, whoever made it? |

The file hash alone is not enough, because a parquet file's bytes depend on
the library that wrote it, not only on the data. The sensor readings from
[Try it: two fingerprints](../guides/fingerprints.md), saved once with polars
and once with pyarrow (polars 2.0 and pyarrow 26; other versions give other
file hashes):

| | Written by polars | Written by pyarrow |
|---|---|---|
| Same data? | yes | yes |
| File hash | `49ad3771…` | `1d3019f4…` |
| Data hash | `76c92a8b…` | `76c92a8b…` |

So the file hash is exact on one computer, and the data hash is what a
co-author, a reviewer or a CI job can compare. Neither shows that a recipe
reading the view would give the same results: a step can see what the data
hash leaves out, such as a time zone's name, a column's storage type or
unit, or its categories. So `sync` acts on a fingerprint that changed, and
never skips a recipe because one stayed the same. After `run` on its own,
the records show only the readers whose input data changed; `run` names
the others. To see both for yourself,
including how row order and the last digit of a number affect them, work
through [Try it: two fingerprints](../guides/fingerprints.md).
`uvs.data_hash()` computes the data hash of any parquet file, by the
[data hash rules](../reference/data-hash.md). useful-views already records
each view's data hash, and those of the views each recipe read, in
`datapackage.json` (see [Fingerprints](records.md#fingerprints)), and
`check` and `sync` compare them to find a view made from an older version
of another. `run` and `sync` also compare a view's file on disk with its
record before a recipe reads it; having `check` do so too, which would catch
a `git pull` at once, is next.

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
