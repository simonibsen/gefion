"""Validation-catalog tests (008, T002 — Foundational).

TDD: written FIRST. The catalog is configuration, not code (SC-306: covering a
new metric is a YAML edit). The loader must be strict — an invalid catalog is
refused loudly at load, never half-applied — and coverage must be enumerable:
covered metrics AND uncovered numeric columns, so there is no coverage
illusion.
"""
import textwrap

import pytest

from gefion.quality import catalog


def _load(tmp_path, text):
    p = tmp_path / "catalog.yaml"
    p.write_text(textwrap.dedent(text))
    return catalog.load(p)


VALID = """
    defaults:
      tolerance_factor: 10
      spike_factor: 100
      robust_z_threshold: 10
    metrics:
      beta:
        entity_table: stocks
        table: stocks_fundamentals
        column: beta
        bounds: {min: -50, max: 50}
        why: Beta is a bounded regression slope.
      dividend_yield:
        entity_table: stocks
        table: stocks_fundamentals
        column: dividend_yield
        bounds: {min: 0, max: 2.0}
        derivation:
          expression: dividend_per_share / close
          inputs:
            dividend_per_share: overview.DividendPerShare
            close: stock_ohlcv.close
        why: Yield is dividend/price.
      vix:
        entity_table: macro_series
        table: macro_series_values
        column: value
        series: vix
        bounds: {min: 0.01, max: 200}
        why: A volatility index is strictly positive.
    universe:
      test_tickers: [ZVZZT, ZWZZT]
      selectors:
        asset_type_common: "asset_type = 'Common Stock'"
"""


def test_valid_catalog_loads_with_defaults_and_metrics(tmp_path):
    cat = _load(tmp_path, VALID)
    assert cat.defaults["tolerance_factor"] == 10
    assert set(cat.metrics) == {"beta", "dividend_yield", "vix"}
    beta = cat.metrics["beta"]
    assert beta.entity_table == "stocks"
    assert beta.bounds == (-50.0, 50.0)
    assert beta.derivation is None
    assert "regression slope" in beta.why
    dy = cat.metrics["dividend_yield"]
    assert dy.derivation is not None
    assert cat.universe["test_tickers"] == ["ZVZZT", "ZWZZT"]


def test_unknown_keys_are_refused(tmp_path):
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, VALID.replace("why: Beta is a bounded regression slope.",
                                      "why: x\n        surprise_key: 1"))
    assert "surprise_key" in str(exc.value)
    assert "beta" in str(exc.value)  # the offending stanza is named


def test_non_numeric_bounds_refused(tmp_path):
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, VALID.replace("{min: -50, max: 50}",
                                      "{min: low, max: high}"))
    assert "beta" in str(exc.value)


def test_bounds_require_why(tmp_path):
    """No magic numbers: an envelope without its definitional argument is
    refused."""
    bad = VALID.replace("        why: Beta is a bounded regression slope.\n", "")
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, bad)
    assert "why" in str(exc.value)


def test_metric_must_name_real_table_and_column(tmp_path):
    """DB-backed check: a stanza naming a nonexistent table/column is refused
    at verification time (catalog.verify_against_db)."""
    import os
    import psycopg
    from gefion.db import schema
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    conn.autocommit = True
    catalog.verify_against_db(conn, catalog.load_default())  # shipped catalog verifies
    bogus = _load(tmp_path, VALID.replace("column: beta", "column: not_a_column"))
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.verify_against_db(conn, bogus)
    assert "not_a_column" in str(exc.value)
    conn.close()


def test_coverage_listing_enumerates_gaps(tmp_path):
    """The coverage report lists covered metrics AND uncovered numeric columns
    on validated tables — the gap is enumerable, never silent."""
    import os
    import psycopg
    from gefion.db import schema
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    conn.autocommit = True
    cat = _load(tmp_path, VALID)
    report = catalog.coverage(conn, cat)
    assert "beta" in report["covered"]
    # stocks_fundamentals has numeric columns the VALID catalog doesn't cover
    uncovered = {f"{t}.{c}" for t, c in report["uncovered"]}
    assert "stocks_fundamentals.pe_ratio" in uncovered
    conn.close()


def test_tuned_bounds_tolerate_real_prod_extremes():
    """The 2026-07-09 prod backfill surfaced borderline convictions that are
    real, not garbage (SC-306 tuning): near-zero-revenue margin explosions and
    tiny-negative revenue_per_share rounding artifacts must NOT convict, while
    the genuine garbage still does."""
    from gefion.quality.rules import check_bounds
    cat = catalog.load_default()
    # near-zero-revenue margin explosions are real — must pass
    assert check_bounds(cat.metrics["operating_margin"], 4592.41) is None
    assert check_bounds(cat.metrics["operating_margin"], 1105.13) is None
    assert check_bounds(cat.metrics["profit_margin"], 1654.37) is None
    # tiny-negative revenue_per_share (rounding around zero revenue) — must pass
    assert check_bounds(cat.metrics["revenue_per_share"], -0.001) is None
    assert check_bounds(cat.metrics["revenue_per_share"], -0.492) is None
    # but genuine garbage still convicts
    assert check_bounds(cat.metrics["beta"], -503341.44) is not None
    assert check_bounds(cat.metrics["revenue_per_share"], -440.75) is not None
    # cross-sectional threshold raised to quiet heavy-tailed fundamentals
    assert cat.defaults["robust_z_threshold"] >= 20


def test_shipped_catalog_covers_initial_scope():
    """T003's deliverable: the repo catalog covers the twelve fundamentals
    ratio metrics + vix, with derivations where trusted recomputes exist."""
    cat = catalog.load_default()
    expected = {"beta", "book_value", "dividend_yield", "eps", "ev_to_ebitda",
                "forward_pe", "operating_margin", "pe_ratio", "peg_ratio",
                "profit_margin", "return_on_equity", "revenue_per_share", "vix"}
    assert expected <= set(cat.metrics)
    assert cat.metrics["dividend_yield"].derivation is not None
    assert cat.metrics["pe_ratio"].derivation is not None
    assert cat.metrics["vix"].entity_table == "macro_series"
    # every bounded metric carries its definitional argument
    assert all(m.why for m in cat.metrics.values())
    # the universe block is populated (T003)
    assert "ZVZZT" in cat.universe["test_tickers"]


# --- series_range detector (issue #136) --------------------------------------------

SERIES_RANGE = """
    defaults:
      tolerance_factor: 10
      spike_factor: 100
      robust_z_threshold: 10
    metrics:
      adjusted_close:
        entity_table: stocks
        table: stock_ohlcv
        column: adjusted_close
        series_range: {max_ratio: 1.0e6}
        why: Serial reverse-split restatements span magnitude cliffs.
    universe:
      test_tickers: []
      selectors: {}
"""


def test_series_range_parses(tmp_path):
    cat = _load(tmp_path, SERIES_RANGE)
    m = cat.metrics["adjusted_close"]
    assert m.series_range == 1.0e6
    assert m.bounds is None
    assert m.table == "stock_ohlcv"


def test_series_range_requires_why(tmp_path):
    bad = SERIES_RANGE.replace(
        "        why: Serial reverse-split restatements span magnitude cliffs.\n", "")
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, bad)
    assert "why" in str(exc.value)


def test_series_range_max_ratio_must_be_numeric(tmp_path):
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, SERIES_RANGE.replace("{max_ratio: 1.0e6}",
                                             "{max_ratio: lots}"))
    assert "adjusted_close" in str(exc.value)


def test_series_range_refuses_unknown_keys(tmp_path):
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, SERIES_RANGE.replace("{max_ratio: 1.0e6}",
                                             "{max_ratio: 1.0e6, surprise: 2}"))
    assert "surprise" in str(exc.value)


def test_series_range_and_bounds_are_mutually_exclusive(tmp_path):
    """series_range metrics scan SQL aggregates, never rows — a bounds stanza
    on the same metric would silently demand the per-row scan path."""
    bad = SERIES_RANGE.replace(
        "series_range: {max_ratio: 1.0e6}",
        "series_range: {max_ratio: 1.0e6}\n        bounds: {min: 0, max: 100}")
    with pytest.raises(catalog.CatalogError) as exc:
        _load(tmp_path, bad)
    assert "series_range" in str(exc.value) and "bounds" in str(exc.value)


def test_shipped_catalog_covers_adjusted_close():
    """Issue #136: the repo catalog watches adjusted_close dynamic range."""
    cat = catalog.load_default()
    m = cat.metrics["adjusted_close"]
    assert m.entity_table == "stocks"
    assert m.table == "stock_ohlcv" and m.column == "adjusted_close"
    assert m.series_range == 1.0e6
    assert m.why
