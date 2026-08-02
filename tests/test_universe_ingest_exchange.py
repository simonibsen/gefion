"""Tests for exchange persistence during universe ingest (issue #192).

`universe-ingest --exchange NYSE` must record the exchange on the stocks row
at registration (and keep it correct on upsert), instead of leaving it NULL
until a separate `data listing-meta` pass. The exchange/name/asset_type are
authoritative from the listing row and known at ingest time.
"""
import os

import pytest
import psycopg

from gefion.db import schema
from gefion.db.ingest import upsert_stock
from gefion.ingest import universe


def require_db():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture
def conn():
    connection = require_db()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def clean_db():
    """Reset stocks (and dependents) before each test.

    TRUNCATE ... CASCADE keeps the schema intact while clearing rows, matching
    the pattern used by test_smart_ingestion.
    """
    connection = require_db()
    connection.autocommit = True
    try:
        schema.create_stocks_table(connection)
        schema.create_stock_ohlcv_table(connection)
        with connection.cursor() as cur:
            cur.execute("SET lock_timeout = '10s';")
            cur.execute("TRUNCATE stocks RESTART IDENTITY CASCADE;")
    finally:
        connection.close()
    yield


def _fetch_stock(conn, symbol):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT exchange, name, asset_type, status, updated_at "
            "FROM stocks WHERE symbol = %s",
            (symbol,),
        )
        return cur.fetchone()


def test_upsert_stock_persists_exchange_on_new_row(conn):
    """A new stock row records exchange/name/asset_type/status + updated_at."""
    stock_id = upsert_stock(
        conn,
        "NYSETEST",
        status="Active",
        exchange="NYSE",
        name="NYSE Test Co",
        asset_type="Stock",
    )
    conn.commit()
    assert stock_id > 0

    exchange, name, asset_type, status, updated_at = _fetch_stock(conn, "NYSETEST")
    assert exchange == "NYSE"
    assert name == "NYSE Test Co"
    assert asset_type == "Stock"
    assert status == "Active"
    assert updated_at is not None


def test_upsert_stock_populates_exchange_on_existing_null_row(conn):
    """An existing NULL-exchange row gets exchange populated on upsert."""
    # Register the symbol the old way: no metadata, exchange stays NULL.
    upsert_stock(conn, "UPSERTSYM")
    conn.commit()
    exchange, _, _, _, updated_at = _fetch_stock(conn, "UPSERTSYM")
    assert exchange is None
    assert updated_at is None

    # Re-ingest with the exchange in hand from the listing row.
    upsert_stock(conn, "UPSERTSYM", status="Active", exchange="NYSE", asset_type="Stock")
    conn.commit()

    exchange, _, asset_type, status, updated_at = _fetch_stock(conn, "UPSERTSYM")
    assert exchange == "NYSE"
    assert asset_type == "Stock"
    assert status == "Active"
    assert updated_at is not None


def test_upsert_stock_does_not_clobber_existing_name(conn):
    """An existing name is preserved on upsert (listing-meta consistency)."""
    upsert_stock(conn, "NAMESYM", status="Active", exchange="NYSE", name="Original Name")
    conn.commit()

    # A later ingest passes a different name; the existing one must win, matching
    # update_listing_metadata's COALESCE(name, %s) semantics.
    upsert_stock(conn, "NAMESYM", status="Active", exchange="NYSE", name="Different Name")
    conn.commit()

    _, name, _, _, _ = _fetch_stock(conn, "NAMESYM")
    assert name == "Original Name"


def test_upsert_stock_without_metadata_leaves_row_untouched(conn):
    """Backwards-compat: the plain 2-arg call does not stamp updated_at."""
    upsert_stock(conn, "PLAINSYM")
    conn.commit()
    exchange, name, asset_type, status, updated_at = _fetch_stock(conn, "PLAINSYM")
    assert exchange is None
    assert name is None
    assert asset_type is None
    assert status is None
    assert updated_at is None


class _FakeClient:
    """Minimal stand-in for AlphaVantageClient (no network)."""

    def fetch_daily_adjusted(self, symbol, outputsize="compact"):
        return {
            "Time Series (Daily)": {
                "2025-01-02": {
                    "1. open": "10.0",
                    "2. high": "11.0",
                    "3. low": "9.5",
                    "4. close": "10.5",
                    "5. adjusted close": "10.5",
                    "6. volume": "1000",
                    "7. dividend amount": "0.0",
                    "8. split coefficient": "1.0",
                }
            }
        }


def test_ingest_prices_for_symbols_persists_exchange(conn):
    """End-to-end: ingest write path stamps exchange from listing_meta."""
    listing_meta = {
        "FAKENYSE": {"exchange": "NYSE", "name": "Fake NYSE Co", "asset_type": "Stock"},
    }
    inserted = universe.ingest_prices_for_symbols(
        db_url=schema.test_db_url(),
        client=_FakeClient(),
        symbols=["FAKENYSE"],
        max_workers=1,
        writer_workers=1,
        timeframe="full",
        listing_meta=listing_meta,
    )
    assert inserted >= 1

    exchange, name, asset_type, status, updated_at = _fetch_stock(conn, "FAKENYSE")
    assert exchange == "NYSE"
    assert name == "Fake NYSE Co"
    assert asset_type == "Stock"
    assert status == "Active"
    assert updated_at is not None
