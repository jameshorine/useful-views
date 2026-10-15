# Reshape the wide export (one column per year) into one row per country, variable and year.
import polars as pl

wide = pl.read_csv("raw/pwt110_bra_deu_esp_usa.csv")
long = wide.unpivot(
    index=["ISO code", "Country", "Variable code", "Variable name"],
    variable_name="year",
    value_name="value",
).with_columns(pl.col("year").cast(pl.Int32))
long.write_parquet("work/pwt_panel/long.parquet")
