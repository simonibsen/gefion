"""
Tests for composite indexes on time-series queries.

These indexes optimize common single-stock time-series query patterns
like "get all prices for stock X between dates Y and Z".
"""
import os
from datetime import date, timedelta

import psycopg
import pytest

from g2.db import schema
from g2.db.ingest import upsert_stock, insert_stock_prices


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
    """Setup tables with composite indexes."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS computed_features CASCADE;")
        cur.execute("DROP TABLE IF EXISTS feature_definitions CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")

        # Create stocks table
        cur.execute("""
            CREATE TABLE stocks (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE
            );
        """)

        # Create stock_prices with composite index
        cur.execute("""
            CREATE TABLE stock_prices (
                id BIGSERIAL PRIMARY KEY,
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                open NUMERIC(18,6),
                high NUMERIC(18,6),
                low NUMERIC(18,6),
                close NUMERIC(18,6),
                adjusted_close NUMERIC(18,6),
                volume BIGINT,
                source TEXT,
                UNIQUE (data_id, date)
            );
        """)

        # Add composite index
        cur.execute("""
            CREATE INDEX stock_prices_data_id_date_idx
                ON stock_prices(data_id, date DESC);
        """)

        # Create feature_definitions
        cur.execute("""
            CREATE TABLE feature_definitions (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                function_name TEXT NOT NULL,
                params JSONB,
                source_table TEXT,
                source_column TEXT,
                store_table TEXT DEFAULT 'computed_features',
                store_column TEXT,
                store_type TEXT DEFAULT 'double precision',
                active BOOLEAN DEFAULT TRUE
            );
        """)

        # Create computed_features with composite index
        cur.execute("""
            CREATE TABLE computed_features (
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
                value DOUBLE PRECISION,
                source TEXT,
                PRIMARY KEY (feature_id, data_id, date)
            );
        """)

        # Add composite index
        cur.execute("""
            CREATE INDEX computed_features_feature_data_date_idx
                ON computed_features(feature_id, data_id, date DESC);
        """)

    yield


def test_stock_prices_composite_index_exists(conn):
    """Test that the composite index on stock_prices exists."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'stock_prices'
            AND indexname = 'stock_prices_data_id_date_idx';
        """)
        result = cur.fetchone()

    assert result is not None, "Composite index stock_prices_data_id_date_idx should exist"
    assert "data_id" in result[1], "Index should include data_id"
    assert "date" in result[1], "Index should include date"


def test_computed_features_composite_index_exists(conn):
    """Test that the composite index on computed_features exists."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'computed_features'
            AND indexname = 'computed_features_feature_data_date_idx';
        """)
        result = cur.fetchone()

    assert result is not None, "Composite index computed_features_feature_data_date_idx should exist"
    assert "feature_id" in result[1], "Index should include feature_id"
    assert "data_id" in result[1], "Index should include data_id"
    assert "date" in result[1], "Index should include date"


def test_composite_index_used_in_query_plan(conn):
    """Test that the composite index is used in query execution plans."""
    # Insert test data
    stock_id = upsert_stock(conn, "TEST")

    base_date = date(2020, 1, 1)
    rows = []
    for i in range(100):
        rows.append({
            "date": base_date + timedelta(days=i),
            "close": 100.0 + i,
            "adjusted_close": 100.0 + i,
            "volume": 1000000,
            "source": "test",
        })

    insert_stock_prices(conn, stock_id, rows, update_existing=False)

    # Check query plan for single-stock query
    with conn.cursor() as cur:
        cur.execute("""
            EXPLAIN (FORMAT JSON)
            SELECT date, close FROM stock_prices
            WHERE data_id = %s AND date >= %s AND date <= %s
            ORDER BY date DESC;
        """, (stock_id, base_date, base_date + timedelta(days=50)))

        plan = cur.fetchone()[0]

    # Convert plan to string for searching
    plan_str = str(plan)

    # Should use index scan (not sequential scan)
    assert "Index" in plan_str or "Bitmap" in plan_str, \
        "Query should use index, not sequential scan"


def test_composite_index_improves_range_queries(conn):
    """Test that range queries benefit from composite indexes."""
    import time

    # Insert substantial test data
    stock_id = upsert_stock(conn, "PERF")

    base_date = date(2010, 1, 1)
    rows = []
    for i in range(5000):  # 5000 days of data
        rows.append({
            "date": base_date + timedelta(days=i),
            "close": 100.0 + i * 0.1,
            "adjusted_close": 100.0 + i * 0.1,
            "volume": 1000000,
            "source": "test",
        })

    insert_stock_prices(conn, stock_id, rows, update_existing=False)

    # Query a specific date range (should use index)
    start = time.time()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, close FROM stock_prices
            WHERE data_id = %s
            AND date >= %s
            AND date <= %s
            ORDER BY date DESC;
        """, (stock_id, base_date + timedelta(days=1000), base_date + timedelta(days=2000)))

        results = cur.fetchall()
    elapsed = time.time() - start

    assert len(results) == 1001, "Should return correct number of rows"
    # With index, should be very fast even with 5000 rows
    assert elapsed < 0.1, f"Range query took {elapsed:.3f}s - should be faster with index"


def test_schema_create_includes_indexes(conn):
    """Test that schema.py functions create indexes automatically."""
    # Clean slate
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")

    # Use schema functions
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)

    # Check if composite index exists
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'stock_prices'
            AND indexname LIKE '%data_id%date%';
        """)
        indexes = cur.fetchall()

    # Should have at least one composite index on (data_id, date)
    assert len(indexes) > 0, "schema.create_stock_prices_table should create composite indexes"
