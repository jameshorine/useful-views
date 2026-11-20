"""Get started (user_guide/quickstart.qmd) makes what it says.

tests/test_docs_pages.py runs every .qmd page. This test also checks what
this one makes: the example's views equal the reference data, the new
recipe made its view, and check finds no problems.
"""

from __future__ import annotations

import polars as pl
from polars.testing import assert_frame_equal

import useful_views as uvs

from conftest import REFERENCE
from test_docs_pages import GUIDE, cells, run_cells


def test_get_started_makes_what_it_says(tmp_path, monkeypatch):
    run_cells(cells(GUIDE / "quickstart.qmd"), "quickstart.qmd", tmp_path, monkeypatch)
    root = tmp_path / "pwt-example"
    for view in ("pwt-panel.parquet", "gdp-per-capita.parquet"):
        assert_frame_equal(
            pl.read_parquet(root / "useful-views" / view),
            pl.read_parquet(REFERENCE / "useful-views" / view),
            check_exact=True,
        )
    growth = pl.read_parquet(root / "useful-views" / "growth-since-2000.parquet")
    assert growth.columns == ["iso", "country", "avg_growth_pct"] and growth.height == 4
    assert uvs.check(root=root) == []
