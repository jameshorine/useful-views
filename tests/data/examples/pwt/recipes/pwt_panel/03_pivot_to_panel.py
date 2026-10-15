# Spread the variables into columns, giving one row per country and year.
import polars as pl

CORE = ["rgdpe", "rgdpo", "rgdpna", "pop", "emp", "avh", "hc", "rtfpna"]

core = pl.read_parquet("work/pwt_panel/core.parquet")
panel = (
    core.pivot(on="Variable code", index=["ISO code", "Country", "year"], values="value")
    .rename({"ISO code": "iso", "Country": "country"})
    .select("iso", "country", "year", *CORE)
    .sort("iso", "year")
)
panel.write_parquet("useful-views/pwt-panel.parquet")
