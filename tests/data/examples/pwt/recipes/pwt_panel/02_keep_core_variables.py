import polars as pl

CORE = ["rgdpe", "rgdpo", "rgdpna", "pop", "emp", "avh", "hc", "rtfpna"]

long = pl.read_parquet("work/pwt_panel/long.parquet")
core = long.filter(pl.col("Variable code").is_in(CORE))
core.write_parquet("work/pwt_panel/core.parquet")
