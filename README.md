<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jameshorine/useful-views/main/docs/useful-views-logo-dark.svg">
  <img src="https://raw.githubusercontent.com/jameshorine/useful-views/main/docs/useful-views-logo.svg" alt="useful-views" width="480">
</picture>

*Documented derived tables for data projects.*

[![PyPI](https://img.shields.io/pypi/v/useful-views)](https://pypi.org/project/useful-views/)
[![Python](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)](https://pypi.org/project/useful-views/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/jameshorine/useful-views/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-jameshorine.github.io-informational)](https://jameshorine.github.io/useful-views/)

</div>

## Overview

useful-views gives a data project a fixed structure, so that every derived
table, a "useful view", records the recipe that made it. A recipe lists its
inputs, the steps that run in order, and the views it makes.

- `init` makes a project.
- `new-recipe` starts a recipe.
- `run` runs one recipe and records what it made.
- `sync` makes every view match its recipe, upstream first.
- `check` reports anything missing, refused or out of date.
- `cookbook` writes `cookbook.json`, the recipes in the order they run.

Each is a command, `useful-views sync`, and a Python function, `uvs.sync()`.
[Get started](https://jameshorine.github.io/useful-views/user-guide/quickstart.html)
walks through them.

## Installation

```sh
uv add useful-views polars
```

Or `pip install useful-views polars`. Install useful-views in the same
environment as the libraries your steps use, such as polars or pandas,
because steps run with the Python that runs useful-views. So don't run it
with `uvx` or `pipx`.
[Installation](https://jameshorine.github.io/useful-views/user-guide/installation.html)
covers pip, conda, Windows and notebooks.

## Usage

```console
$ useful-views example pwt
Example 'pwt' ready at /path/to/pwt-example. Make its views with: useful-views -C "/path/to/pwt-example" sync
$ cd pwt-example
$ useful-views sync
useful-views: running recipes/pwt_panel
useful-views: running recipes/gdp_per_capita
Ran, in order: recipes/pwt_panel, recipes/gdp_per_capita.
Recorded in datapackage.json and useful-views/README.md.
$ useful-views check
No problems found.
```

`example` downloads a ready-made project from the
[useful-views-data](https://github.com/jameshorine/useful-views-data)
repository. Each release of useful-views pins its files by checksum, so an
example never changes under you.

Views are parquet files, so any library can read them:

```python
>>> import polars as pl
>>> gdp = pl.read_parquet("useful-views/gdp-per-capita.parquet")
>>> gdp.filter(pl.col("year") == 2023).select("country", "gdp_per_capita", "growth_pct")
shape: (4, 3)
┌───────────────┬────────────────┬────────────┐
│ country       ┆ gdp_per_capita ┆ growth_pct │
│ ---           ┆ ---            ┆ ---        │
│ str           ┆ i64            ┆ f64        │
╞═══════════════╪════════════════╪════════════╡
│ Brazil        ┆ 18806          ┆ 2.83       │
│ Germany       ┆ 59854          ┆ -0.81      │
│ Spain         ┆ 46733          ┆ 2.5        │
│ United States ┆ 74654          ┆ 2.31       │
└───────────────┴────────────────┴────────────┘
```

Steps are code: `run` and `sync` run the Python in a project's `recipes/`.
Read the recipes of a project you didn't write before you run them.

## Getting help

- Questions, bugs and ideas:
  [GitHub issues](https://github.com/jameshorine/useful-views/issues).
- Planned work:
  [issues labelled enhancement](https://github.com/jameshorine/useful-views/issues?q=is%3Aissue%20is%3Aopen%20label%3Aenhancement).

## License

useful-views is released under the MIT License; see `LICENSE`. The example
data isn't part of the package or under its license. It lives in the
[useful-views-data](https://github.com/jameshorine/useful-views-data)
repository, with a copy in `tests/data/` for the tests. It is an extract of
the Penn World Table, licensed CC BY 4.0, and the example's `README.md`
gives the citation.

## Citing useful-views

If you use useful-views in your research, please cite it. GitHub's
**Cite this repository** button (from `CITATION.cff`) gives APA and BibTeX,
or use:

> Horine, J. (2026). *useful-views: Documented derived tables for
> data projects* (Version 0.3.0) [Computer software].
> https://github.com/jameshorine/useful-views

```bibtex
@software{horine_useful_views_2026,
  author  = {Horine, James},
  title   = {useful-views: Documented derived tables for data projects},
  year    = {2026},
  version = {0.3.0},
  url     = {https://github.com/jameshorine/useful-views},
  license = {MIT}
}
```

Please also cite the sources of any data you use with it.
