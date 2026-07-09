"""Research-universe hardening tests (008, T015 — US3).

TDD: written FIRST. Junk instruments never enter a research universe: NASDAQ
test tickers are excluded unconditionally, and asset_type/exchange are
declared, fail-closed selectors. The catalog is the single source of the
test-ticker list (shared vocabulary with 006's discovery chain).
"""
import os

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QUT%' OR symbol IN "
                    "('ZVZZT', 'ZWZZT')")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QUT%' OR symbol IN "
                    "('ZVZZT', 'ZWZZT')")
    c.close()


# --- the pure helper ---------------------------------------------------------------

def test_exclude_test_tickers_pure():
    from gefion.quality import universe
    kept = universe.exclude_test_tickers(["AAPL", "ZVZZT", "MSFT", "ZWZZT", "ZJZZT"])
    assert kept == ["AAPL", "MSFT"]


def test_test_ticker_list_comes_from_catalog():
    from gefion.quality import catalog, universe
    cat = catalog.load_default()
    assert "ZVZZT" in cat.universe["test_tickers"]
    # the helper honors the catalog list
    assert universe.is_test_ticker("ZVZZT")


# --- DB-backed research universe ----------------------------------------------------

def test_research_universe_excludes_test_tickers_and_fails_closed(conn):
    from gefion.quality import universe
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('QUT1', 'A', 'Common Stock'), ('QUT2', 'B', 'ETF'),
               ('QUT3', 'C', NULL), ('ZVZZT', 'Test', 'Common Stock')""")
    kept, report = universe.research_universe(
        conn, ["QUT1", "QUT2", "QUT3", "ZVZZT"], require_asset_type="common")
    assert "ZVZZT" not in kept          # test ticker gone unconditionally
    assert "QUT2" not in kept           # ETF excluded by asset_type:common
    assert "QUT3" not in kept           # NULL asset_type fails closed
    assert kept == ["QUT1"]
    assert report["test_tickers"] == 1
    assert report["asset_type_excluded"] >= 1


def test_dataset_universe_resolution_is_quality_filtered(conn):
    """The dataset build's universe resolution drops test tickers by default."""
    from gefion.ml.dataset import resolve_universe_symbols
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO stocks (symbol, name, exchange) VALUES
               ('QUT4', 'A', 'NASDAQ'), ('ZVZZT', 'Test', 'NASDAQ')""")
    symbols = resolve_universe_symbols(conn, {"exchange": "NASDAQ"})
    assert "QUT4" in symbols
    assert "ZVZZT" not in symbols       # never in a research universe
