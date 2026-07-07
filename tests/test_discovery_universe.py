"""Universe-filter tests for agentic regime discovery (006, T007).

TDD: written FIRST. The symbol universe can never be a hidden researcher
degree of freedom (FR-121a): the chain is declared, recorded in the
pre-registration, and an unfiltered universe requires an explicit
`passthrough` declaration — never a silent fallback.
"""
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import universe


# --- chain parsing and declaration rules ------------------------------------

def test_parse_default_chain():
    chain = universe.parse_filter_chain(None)
    assert universe.describe_chain(chain) == ["test_tickers", "asset_type:common"]


def test_parse_explicit_chain():
    chain = universe.parse_filter_chain("test_tickers,asset_type:common")
    assert universe.describe_chain(chain) == ["test_tickers", "asset_type:common"]


def test_parse_passthrough_must_be_alone():
    chain = universe.parse_filter_chain("passthrough")
    assert universe.describe_chain(chain) == ["passthrough"]
    with pytest.raises(universe.UniverseError):
        universe.parse_filter_chain("passthrough,test_tickers")


def test_parse_empty_string_is_refused():
    """'' is not a declaration — unfiltered runs must SAY passthrough."""
    with pytest.raises(universe.UniverseError):
        universe.parse_filter_chain("")


def test_parse_unknown_filter_refused():
    with pytest.raises(universe.UniverseError):
        universe.parse_filter_chain("liquidity_tier:1")


def test_chain_description_roundtrips_for_preregistration():
    """describe_chain output is what lands in search_space JSON; parsing it
    back must yield the same chain (declared == recorded == applied)."""
    for spec in ("test_tickers", "asset_type:common", "passthrough",
                 "test_tickers,asset_type:common"):
        chain = universe.parse_filter_chain(spec)
        assert ",".join(universe.describe_chain(chain)) == spec


# --- built-in: test-ticker exclusion ----------------------------------------

def test_test_ticker_filter_drops_zvzzt_family():
    symbols = ["AAPL", "ZVZZT", "MSFT", "ZWZZT", "ZXZZT", "ZJZZT", "ZAZZT"]
    chain = universe.parse_filter_chain("test_tickers")
    assert universe.apply_chain(chain, symbols) == ["AAPL", "MSFT"]


def test_test_ticker_filter_keeps_real_z_names():
    symbols = ["ZM", "ZS", "ZBRA", "ZION"]
    chain = universe.parse_filter_chain("test_tickers")
    assert universe.apply_chain(chain, symbols) == symbols


def test_passthrough_is_identity():
    symbols = ["AAPL", "ZVZZT"]
    chain = universe.parse_filter_chain("passthrough")
    assert universe.apply_chain(chain, symbols) == symbols


# --- built-in: asset_type (DB-backed) ---------------------------------------

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
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'UNIVT%'")
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('UNIVT1', 'Common Co', 'Common Stock'),
               ('UNIVT2', 'Fund', 'ETF'),
               ('UNIVT3', 'Warrant Co', 'Warrant'),
               ('UNIVT4', 'No Type Co', NULL),
               ('UNIVT5', 'Listing Vocab Co', 'Stock')"""
        )
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'UNIVT%'")
    c.close()


def test_asset_type_filter_keeps_common_stock_only(conn):
    """Both real-world vocabularies count as common stock: the OVERVIEW
    endpoint says 'Common Stock', LISTING_STATUS says 'Stock' — production
    data (sloth, 2026-07-07) is entirely the latter."""
    chain = universe.parse_filter_chain("asset_type:common")
    got = universe.apply_chain(
        chain, ["UNIVT1", "UNIVT2", "UNIVT3", "UNIVT4", "UNIVT5"], conn=conn)
    assert got == ["UNIVT1", "UNIVT5"]


def test_asset_type_filter_requires_connection():
    chain = universe.parse_filter_chain("asset_type:common")
    with pytest.raises(universe.UniverseError):
        universe.apply_chain(chain, ["AAPL"])


def test_default_chain_composes(conn):
    chain = universe.parse_filter_chain(None)
    got = universe.apply_chain(chain, ["UNIVT1", "UNIVT2", "ZVZZT"], conn=conn)
    assert got == ["UNIVT1"]
