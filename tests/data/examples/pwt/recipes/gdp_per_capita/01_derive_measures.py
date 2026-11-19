import polars as pl

panel = pl.read_parquet("useful-views/pwt-panel.parquet")

view = (
    panel.sort("iso", "year")
    .with_columns(
        gdp_per_capita=pl.col("rgdpe") / pl.col("pop"),
        growth_pct=(pl.col("rgdpna") / pl.col("pop")).pct_change().over("iso") * 100,
    )
    .with_columns(
        relative_to_usa=pl.col("gdp_per_capita")
        / pl.col("gdp_per_capita").filter(pl.col("iso") == "USA").first().over("year")
    )
    .select(
        "iso",
        "country",
        "year",
        pl.col("gdp_per_capita").round(0).cast(pl.Int64),
        pl.col("growth_pct").round(2),
        pl.col("relative_to_usa").round(3),
    )
)
view.write_parquet("useful-views/gdp-per-capita.parquet")
