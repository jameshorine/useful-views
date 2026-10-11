# Reference project: Penn World Table example

This folder is a complete `useful-views` project. The package's tests rebuild
it from `raw/` and `recipes/` and compare the result with the files here.

## Data source and credit

`raw/pwt110_bra_deu_esp_usa.csv` is an extract of the Penn World Table,
version 11.0: all variables and all years (1950 to 2023) for Brazil, Germany,
Spain and the United States. It was exported on 2026-10-09 from the PWT
online data access tool:

https://pwt-data-tool.streamlit.app/?page=Thematic+select&countries=USA%2CDEU%2CESP%2CBRA&variables=All&years=All

The file is unchanged apart from its name.

The Penn World Table is licensed under a Creative Commons Attribution 4.0
International License. Please cite:

> Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next
> Generation of the Penn World Table." American Economic Review, 105(10),
> 3150-3182.

## Recipes

- `recipes/pwt_panel/` reshapes the wide export into a country-year panel of
  eight core variables: `useful-views/pwt-panel.parquet`.
- `recipes/gdp_per_capita/` takes that view and derives GDP per capita, its
  annual growth, and its level relative to the United States:
  `useful-views/gdp-per-capita.parquet`.

`useful-views/README.md` is the generated catalog of both views.
