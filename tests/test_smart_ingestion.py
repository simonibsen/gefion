"""Tests for smart ingestion - bulk pre-filtering of up-to-date symbols."""
import os
from datetime import date, timedelta

import pytest
import psycopg

from gefion.db import schema
from gefion.db.ingest import upsert_stock, insert_stock_ohlcv, filter_symbols_needing_update
from gefion.ingest.universe import _expected_market_date


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
    """Test connection with guaranteed close.

    Closing in a finally block rolls back any open transaction even when the
    test fails mid-way. A leaked idle-in-transaction connection here used to
    deadlock the cleanup fixture (issue #29).
    """
    connection = require_db()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the tables this module uses before each test.

    Deliberately NOT `DROP SCHEMA public CASCADE`: that destroys
    schema_migrations, feature seeds, and views for every test module that
    runs afterwards. TRUNCATE ... CASCADE clears stocks and its dependents
    while leaving the schema intact. lock_timeout turns a leaked-lock
    deadlock into a fast failure instead of a hung CI job (issue #29).
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


def test_filter_symbols_no_existing_data(conn):
    """Symbols with no price data should not be filtered out."""
    # Create symbols but no price data
    upsert_stock(conn, "AAPL")
    upsert_stock(conn, "MSFT")
    upsert_stock(conn, "GOOGL")

    # All symbols should need updates (no price data exists)
    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT", "GOOGL"]
    )

    assert set(symbols_needing_update) == {"AAPL", "MSFT", "GOOGL"}


def test_filter_symbols_up_to_date(conn):
    """Symbols with data up to expected market date should be filtered out."""
    target_date = _expected_market_date()

    # AAPL has data up to target_date - should be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_ohlcv(conn, aapl_id, [
        {"date": target_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT has no data - should NOT be filtered out
    upsert_stock(conn, "MSFT")

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT"], target_date
    )

    # Only MSFT should need updates
    assert symbols_needing_update == ["MSFT"]


def test_filter_symbols_stale_data(conn):
    """Symbols with stale data should not be filtered out."""
    old_date = date.today() - timedelta(days=5)

    # AAPL has old data - should NOT be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_ohlcv(conn, aapl_id, [
        {"date": old_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT has no data - should NOT be filtered out
    upsert_stock(conn, "MSFT")

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT"]
    )

    # Both should need updates
    assert set(symbols_needing_update) == {"AAPL", "MSFT"}


def test_filter_symbols_mixed(conn):
    """Mixed scenario: some up-to-date, some stale, some missing."""
    target_date = _expected_market_date()
    old_date = date.today() - timedelta(days=10)

    # AAPL: up-to-date - should be filtered out
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_ohlcv(conn, aapl_id, [
        {"date": target_date, "close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000}
    ])

    # MSFT: stale data - should NOT be filtered out
    msft_id = upsert_stock(conn, "MSFT")
    insert_stock_ohlcv(conn, msft_id, [
        {"date": old_date, "close": 250.0, "open": 249.0, "high": 251.0, "low": 248.0, "volume": 2000000}
    ])

    # GOOGL: no data - should NOT be filtered out
    upsert_stock(conn, "GOOGL")

    # TSLA: not in database yet - should NOT be filtered out
    # (filter function should handle symbols that don't exist in stocks table)

    symbols_needing_update = filter_symbols_needing_update(
        conn, ["AAPL", "MSFT", "GOOGL", "TSLA"], target_date
    )

    # AAPL should be filtered out, others should remain
    assert set(symbols_needing_update) == {"MSFT", "GOOGL", "TSLA"}


def test_filter_symbols_empty_list(conn):
    """Empty symbol list should return empty list."""
    symbols_needing_update = filter_symbols_needing_update(conn, [])

    assert symbols_needing_update == []


def test_filter_new_rows_from_api_response(conn):
    """Only insert rows newer than latest existing date."""
    from gefion.db.ingest import filter_new_rows
    # Insert existing data up to 5 days ago
    old_date = date.today() - timedelta(days=5)
    aapl_id = upsert_stock(conn, "AAPL")
    insert_stock_ohlcv(conn, aapl_id, [
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


def test_filter_new_rows_no_existing_data(conn):
    """When no existing data, all API rows are new."""
    from gefion.db.ingest import filter_new_rows
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


def test_filter_new_rows_since_date(conn):
    """filter_new_rows with since_date discards rows before the cutoff."""
    from gefion.db.ingest import filter_new_rows
    aapl_id = upsert_stock(conn, "AAPL")

    # API returns rows spanning 2025-01-01 to 2025-01-10
    api_response = [
        {"date": date(2025, 1, i), "close": 150.0, "open": 149.0,
         "high": 151.0, "low": 148.0, "volume": 1000000}
        for i in range(1, 11)
    ]

    # Only keep rows since 2025-01-06
    new_rows = filter_new_rows(conn, aapl_id, api_response, since_date=date(2025, 1, 6))

    # Should only include Jan 6-10 = 5 rows
    assert len(new_rows) == 5
    dates = [r["date"] for r in new_rows]
    assert all(d >= date(2025, 1, 6) for d in dates)


def test_filter_new_rows_since_and_target_date(conn):
    """filter_new_rows respects both since_date and target_date bounds."""
    from gefion.db.ingest import filter_new_rows
    aapl_id = upsert_stock(conn, "AAPL")

    api_response = [
        {"date": date(2025, 1, i), "close": 150.0, "open": 149.0,
         "high": 151.0, "low": 148.0, "volume": 1000000}
        for i in range(1, 11)
    ]

    # Keep rows between Jan 4 and Jan 7 inclusive
    new_rows = filter_new_rows(
        conn, aapl_id, api_response,
        since_date=date(2025, 1, 4), target_date=date(2025, 1, 7)
    )

    assert len(new_rows) == 4  # Jan 4, 5, 6, 7
    dates = [r["date"] for r in new_rows]
    assert min(dates) == date(2025, 1, 4)
    assert max(dates) == date(2025, 1, 7)


