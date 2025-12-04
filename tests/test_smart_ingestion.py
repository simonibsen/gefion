"""Tests for smart ingestion - bulk pre-filtering of up-to-date symbols."""
import os
from datetime import date, timedelta

import pytest
import psycopg

from g2.db import schema
from g2.db.ingest import upsert_stock, insert_stock_prices, filter_symbols_needing_update
from g2.ingest.universe import _expected_market_date


def require_db():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture(autouse=True)
def clean_db():
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.close()
    yield


def test_filter_symbols_no_existing_data():
    """Symbols with no price data should not be filtered out."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    # Create symbols but no price data
    upsert_stock(conn, "AAPL")
    upsert_stock(conn, "MSFT")
    upsert_stock(conn, "GOOGL")

    # All symbols should need updates (no price data exists)
    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT", "GOOGL"]
    )

    assert set(symbols_needing_update) == {"AAPL", "MSFT", "GOOGL"}
    conn.close()


def test_filter_symbols_up_to_date():
    """Symbols with data up to expected market date should be filtered out."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    target_date = _expected_market_date()

    # AAPL has data up to today - should be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_prices(conn, aapl_id, [
        {"date": target_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT has no data - should NOT be filtered out
    upsert_stock(conn, "MSFT")

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT"]
    )

    # Only MSFT should need updates
    assert symbols_needing_update == ["MSFT"]
    conn.close()


def test_filter_symbols_stale_data():
    """Symbols with stale data should not be filtered out."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    old_date = date.today() - timedelta(days=5)

    # AAPL has old data - should NOT be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_prices(conn, aapl_id, [
        {"date": old_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT has no data - should NOT be filtered out
    upsert_stock(conn, "MSFT")

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT"]
    )

    # Both should need updates
    assert set(symbols_needing_update) == {"AAPL", "MSFT"}
    conn.close()


def test_filter_symbols_mixed():
    """Mixed scenario: some up-to-date, some stale, some missing."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    target_date = _expected_market_date()
    old_date = date.today() - timedelta(days=10)

    # AAPL: up-to-date - should be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_prices(conn, aapl_id, [
        {"date": target_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT: stale data - should NOT be filtered out
    msft_id = upsert_stock(conn, "MSFT")
    insert_stock_prices(conn, msft_id, [
        {"date": old_date, "close": 250.0, "open": 249.0, "high": 251.0, "low": 248.0, "volume": 2000000}
    ])

    # GOOGL: no data - should NOT be filtered out
    upsert_stock(conn, "GOOGL")

    # TSLA: not in database yet - should NOT be filtered out
    # (filter function should handle symbols that don't exist in stocks table)

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT", "GOOGL", "TSLA"]
    )

    # AAPL should be filtered out, others should remain
    assert set(symbols_needing_update) == {"MSFT", "GOOGL", "TSLA"}
    conn.close()


def test_filter_symbols_empty_list():
    """Empty symbol list should return empty list."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    symbols_needing_update = filter_symbols_needing_update(conn, [])

    assert symbols_needing_update == []
    conn.close()


def test_filter_new_rows_from_api_response():
    """Only insert rows newer than latest existing date."""
    from g2.db.ingest import filter_new_rows
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    # Insert existing data up to 5 days ago
    old_date = date.today() - timedelta(days=5)
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_prices(conn, aapl_id, [
        {"date": old_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # API returns 100 days, but only 5 are new (today through today-4)
    # Existing data is from 5 days ago, so days 0-4 are new
    api_response = []
    for i in range(100):
        day = date.today() - timedelta(days=i)
        api_response.append({
            "date": day,
            "close": 150.0 + i,
            "open": 149.0,
            "high": 151.0,
            "low": 148.0,
            "volume": 1000000
        })

    # Filter to only new rows (newer than 5 days ago)
    new_rows = filter_new_rows(conn, aapl_id, api_response)

    # Should have 5 new rows (days 0-4, since day 5 already exists)
    assert len(new_rows) == 5
    # Newest row should be today
    assert new_rows[0]["date"] == date.today()
    # Oldest new row should be 4 days ago (day 5 is the existing one)
    assert new_rows[-1]["date"] == date.today() - timedelta(days=4)
    conn.close()


def test_filter_new_rows_no_existing_data():
    """When no existing data, all API rows are new."""
    from g2.db.ingest import filter_new_rows
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    aapl_id = upsert_stock(conn, "AAPL")

    # API returns 100 days, no existing data
    api_response = []
    for i in range(100):
        day = date.today() - timedelta(days=i)
        api_response.append({
            "date": day,
            "close": 150.0 + i,
            "open": 149.0,
            "high": 151.0,
            "low": 148.0,
            "volume": 1000000
        })

    new_rows = filter_new_rows(conn, aapl_id, api_response)

    # All 100 rows should be new
    assert len(new_rows) == 100
    conn.close()
