# Penn World Table example

A complete [useful-views](https://github.com/jameshorine/useful-views)
project: raw data and two recipes, ready to run. Get a copy with
`useful-views example pwt`, then, from its folder:

```sh
useful-views sync       # run both recipes, upstream first, to make the views
useful-views check      # expect: No problems found.
useful-views cookbook   # write cookbook.json, the ledger of the recipes used
```

## Data source and credit

`raw/pwt110_bra_deu_esp_usa.csv` is an extract of the Penn World Table,
version 11.0: all variables and all years (1950 to 2023) for Brazil, Germany,
Spain and the United States. It was exported on 2026-10-09 from the PWT
online data access tool, and is unchanged apart from its name:

https://pwt-data-tool.streamlit.app/?page=Thematic+select&countries=USA%2CDEU%2CESP%2CBRA&variables=All&years=All

Penn World Table 11.0 by Robert C. Feenstra, Robert Inklaar and Marcel P.
Timmer (Groningen Growth and Development Centre) is licensed under a Creative
Commons Attribution 4.0 International License:
https://creativecommons.org/licenses/by/4.0/. The full dataset is at
https://www.rug.nl/ggdc/productivity/pwt/ (doi:10.34894/FABVLR). When using
these data, please cite:

> Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next
> Generation of the Penn World Table." American Economic Review, 105(10),
> 3150-3182. https://doi.org/10.1257/aer.20130954. Available for download at
> www.ggdc.net/pwt.

The views the recipes make are reshaped and derived from this extract. They
are not published or endorsed by the Groningen Growth and Development Centre.

## Recipes

- `recipes/pwt_panel/` reshapes the wide export into a country-year panel of
  eight core variables: `useful-views/pwt-panel.parquet`.
- `recipes/gdp_per_capita/` takes that view and derives GDP per capita, its
  annual growth, and its level relative to the United States:
  `useful-views/gdp-per-capita.parquet`.

The steps use polars, so install it in the same environment as useful-views.

## What the project makes

- `useful-views/pwt-panel.parquet` and `useful-views/gdp-per-capita.parquet`:
  the two views.
- `useful-views/README.md`: the generated catalog. For each view, the recipe
  that made it, its inputs, its numbered steps and its columns.
- `datapackage.json`: the generated record of what each recipe made from
  what, as a [Data Package](https://datapackage.org/).
- `cookbook.json`: written by `useful-views cookbook`. Every recipe in run
  order, with its inputs, outputs and steps, like a lock file.
  `useful-views cookbook --check` tells you whether it is up to date.
- `work/`: in-between files the steps pass to each other, emptied before
  each run.

Commit `datapackage.json`, the catalog and `cookbook.json` with your recipes.
The `.gitignore` that useful-views writes keeps `raw/`, `work/` and the
parquet views out of git.
