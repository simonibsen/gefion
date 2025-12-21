"""
Tests for query result caching functionality.
"""
import os
from datetime import date, timedelta

import psycopg
import pytest

from g2.db import schema
from g2.db.cache import (
    prefetch_stock_ids,
    prefetch_latest_prices,
    prefetch_feature_ids,
    StockMetadataCache,
)
from g2.db.ingest import upsert_stock, insert_stock_ohlcv


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def setup_tables(conn):
    """Setup minimal tables for caching tests."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS computed_features CASCADE;")
        cur.execute("DROP TABLE IF EXISTS feature_definitions CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")

        cur.execute("""
            CREATE TABLE stocks (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE
            );
        """)

        cur.execute("""
            CREATE TABLE stock_ohlcv (
                id BIGSERIAL PRIMARY KEY,
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                open NUMERIC(18,6),
                high NUMERIC(18,6),
                low NUMERIC(18,6),
                close NUMERIC(18,6),
                adjusted_close NUMERIC(18,6),
                dividend_amount NUMERIC(18,6),
                split_coefficient NUMERIC(18,6),
                volume BIGINT,
                source TEXT,
                UNIQUE (data_id, date)
            );
        """)

        cur.execute("""
            CREATE TABLE feature_definitions (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                function_name TEXT NOT NULL
            );
        """)

    yield


def test_prefetch_stock_ids(conn):
    """Test pre-fetching stock IDs for multiple symbols in one query."""
    # Create test stocks
    stock1_id = upsert_stock(conn, "AAPL")
    stock2_id = upsert_stock(conn, "GOOGL")
    stock3_id = upsert_stock(conn, "MSFT")

    # Pre-fetch all at once
    result = prefetch_stock_ids(conn, ["AAPL", "GOOGL", "MSFT", "NONEXISTENT"])

    assert result["AAPL"] == stock1_id
    assert result["GOOGL"] == stock2_id
    assert result["MSFT"] == stock3_id
    assert "NONEXISTENT" not in result


def test_prefetch_stock_ids_empty(conn):
    """Test prefetch with empty symbol list."""
    result = prefetch_stock_ids(conn, [])
    assert result == {}


def test_prefetch_latest_prices(conn):
    """Test pre-fetching latest price dates for multiple stocks."""
    # Create stocks and add prices
    stock1_id = upsert_stock(conn, "STOCK1")
    stock2_id = upsert_stock(conn, "STOCK2")
    stock3_id = upsert_stock(conn, "STOCK3")  # No prices

    base_date = date(2020, 1, 1)

    # Add prices for stock1
    insert_stock_ohlcv(conn, stock1_id, [{
        "date": base_date,
        "close": 100.0,
        "adjusted_close": 100.0,
        "volume": 1000000,
    }], update_existing=False)

    # Add prices for stock2 (later date)
    insert_stock_ohlcv(conn, stock2_id, [{
        "date": base_date + timedelta(days=10),
        "close": 200.0,
        "adjusted_close": 200.0,
        "volume": 2000000,
    }], update_existing=False)

    # Pre-fetch latest dates
    result = prefetch_latest_prices(conn, [stock1_id, stock2_id, stock3_id])

    assert result[stock1_id] == base_date
    assert result[stock2_id] == base_date + timedelta(days=10)
    assert result[stock3_id] is None  # No prices


def test_prefetch_feature_ids(conn):
    """Test pre-fetching feature IDs for multiple features."""
    # Create test features
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_definitions (name, function_name)
            VALUES ('rsi_14', 'indicator'), ('macd', 'indicator'), ('sma_20', 'indicator')
            RETURNING id;
        """)
        ids = [row[0] for row in cur.fetchall()]

    # Pre-fetch all at once
    result = prefetch_feature_ids(conn, ["rsi_14", "macd", "sma_20", "nonexistent"])

    assert result["rsi_14"] == ids[0]
    assert result["macd"] == ids[1]
    assert result["sma_20"] == ids[2]
    assert "nonexistent" not in result


def test_stock_metadata_cache_basic(conn):
    """Test basic StockMetadataCache usage."""
    # Create test data
    stock1_id = upsert_stock(conn, "CACHE1")
    stock2_id = upsert_stock(conn, "CACHE2")

    # Initialize and load cache
    cache = StockMetadataCache()
    cache.load_stocks(conn, ["CACHE1", "CACHE2", "NOTEXIST"])

    # Check cached lookups
    assert cache.get_stock_id("CACHE1") == stock1_id
    assert cache.get_stock_id("CACHE2") == stock2_id
    assert cache.get_stock_id("NOTEXIST") is None


def test_stock_metadata_cache_add_new(conn):
    """Test adding newly created stocks to cache."""
    cache = StockMetadataCache()
    cache.load_stocks(conn, [])

    # Cache miss
    assert cache.get_stock_id("NEWSTOCK") is None

    # Create stock and add to cache
    new_id = upsert_stock(conn, "NEWSTOCK")
    cache.add_stock("NEWSTOCK", new_id)

    # Now in cache
    assert cache.get_stock_id("NEWSTOCK") == new_id


def test_stock_metadata_cache_with_prices(conn):
    """Test caching latest price dates."""
    # Setup
    stock1_id = upsert_stock(conn, "PRICE1")
    stock2_id = upsert_stock(conn, "PRICE2")

    base_date = date(2021, 1, 1)
    insert_stock_ohlcv(conn, stock1_id, [{
        "date": base_date,
        "close": 100.0,
        "adjusted_close": 100.0,
        "volume": 1000000,
    }], update_existing=False)

    # Load cache
    cache = StockMetadataCache()
    cache.load_stocks(conn, ["PRICE1", "PRICE2"])
    cache.load_latest_prices(conn, [stock1_id, stock2_id])

    # Check cached dates
    assert cache.get_latest_date(stock1_id) == base_date
    assert cache.get_latest_date(stock2_id) is None  # No prices


def test_stock_metadata_cache_with_features(conn):
    """Test caching feature IDs."""
    # Create features
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_definitions (name, function_name)
            VALUES ('feature_a', 'test'), ('feature_b', 'test')
            RETURNING name, id;
        """)
        feature_mapping = {row[0]: row[1] for row in cur.fetchall()}

    # Load cache
    cache = StockMetadataCache()
    cache.load_features(conn, ["feature_a", "feature_b"])

    # Check cached IDs
    assert cache.get_feature_id("feature_a") == feature_mapping["feature_a"]
    assert cache.get_feature_id("feature_b") == feature_mapping["feature_b"]
    assert cache.get_feature_id("nonexistent") is None


def test_stock_metadata_cache_clear(conn):
    """Test clearing cache."""
    stock_id = upsert_stock(conn, "CLEAR_TEST")

    cache = StockMetadataCache()
    cache.load_stocks(conn, ["CLEAR_TEST"])

    assert cache.get_stock_id("CLEAR_TEST") == stock_id

    # Clear cache
    cache.clear()

    assert cache.get_stock_id("CLEAR_TEST") is None


def test_caching_reduces_query_count(conn):
    """Test that caching reduces the number of database queries."""
    import time

    # Create 100 test stocks
    symbols = [f"PERF{i}" for i in range(100)]
    for sym in symbols:
        upsert_stock(conn, sym)

    # Method 1: Individual queries (uncached)
    start = time.time()
    ids_uncached = {}
    for sym in symbols:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE symbol = %s;", (sym,))
            result = cur.fetchone()
            if result:
                ids_uncached[sym] = result[0]
    time_uncached = time.time() - start

    # Method 2: Single batch query (cached)
    start = time.time()
    ids_cached = prefetch_stock_ids(conn, symbols)
    time_cached = time.time() - start

    # Verify same results
    assert ids_uncached == ids_cached

    # Cached should be significantly faster
    print(f"\nUncached (100 queries): {time_uncached:.3f}s")
    print(f"Cached (1 query): {time_cached:.3f}s")
    print(f"Speed-up: {time_uncached / time_cached:.1f}x")

    assert time_cached < time_uncached / 5, \
        f"Cached lookup should be much faster (got {time_uncached / time_cached:.1f}x)"
