"""
Performance tests for batch insert operations.

These tests verify that our insert operations use efficient batch patterns
rather than row-by-row operations.
"""
import os
import time
from datetime import date, timedelta

import psycopg
import pytest

from g2.db import schema
from g2.db.ingest import insert_stock_prices, upsert_stock


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
    """Setup minimal tables without TimescaleDB for performance testing."""
    with conn.cursor() as cur:
        # Clean existing
        cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")

        # Create stocks table
        cur.execute("""
            CREATE TABLE stocks (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE
            );
        """)

        # Create stock_prices table WITHOUT hypertable for simpler testing
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
    yield


def test_insert_stock_prices_is_batched(conn):
    """Test that insert_stock_prices uses batch inserts, not row-by-row."""

    stock_id = upsert_stock(conn, "TEST")

    # Generate 1000 rows of test data
    base_date = date(2020, 1, 1)
    rows = []
    for i in range(1000):
        rows.append({
            "date": base_date + timedelta(days=i),
            "open": 100.0 + i * 0.1,
            "high": 102.0 + i * 0.1,
            "low": 98.0 + i * 0.1,
            "close": 101.0 + i * 0.1,
            "adjusted_close": 101.0 + i * 0.1,
            "volume": 1000000 + i * 1000,
            "source": "test",
        })

    # Time the insert
    start = time.time()
    inserted = insert_stock_prices(conn, stock_id, rows, update_existing=False)
    elapsed = time.time() - start

    assert inserted == 1000

    # Batch insert of 1000 rows should complete in < 1 second on any reasonable system
    # Row-by-row would typically take 3-10+ seconds
    assert elapsed < 1.0, f"Insert took {elapsed:.2f}s - likely using row-by-row pattern instead of batching"

    # Verify data was actually inserted
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stock_prices WHERE data_id = %s;", (stock_id,))
        count = cur.fetchone()[0]
    assert count == 1000


def test_insert_stock_prices_batch_with_update(conn):
    """Test that batch insert works with update_existing=True."""

    stock_id = upsert_stock(conn, "TEST2")

    # Insert initial data
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

    inserted = insert_stock_prices(conn, stock_id, rows, update_existing=False)
    assert inserted == 100

    # Update with new prices
    updated_rows = []
    for i in range(100):
        updated_rows.append({
            "date": base_date + timedelta(days=i),
            "close": 200.0 + i,  # Changed value
            "adjusted_close": 200.0 + i,
            "volume": 2000000,  # Changed value
            "source": "test_updated",
        })

    start = time.time()
    inserted = insert_stock_prices(conn, stock_id, updated_rows, update_existing=True)
    elapsed = time.time() - start

    assert inserted == 100
    assert elapsed < 0.5, f"Update insert took {elapsed:.2f}s - likely not batched"

    # Verify data was updated
    with conn.cursor() as cur:
        cur.execute(
            "SELECT close, source FROM stock_prices WHERE data_id = %s AND date = %s;",
            (stock_id, base_date)
        )
        close, source = cur.fetchone()
    assert close == 200.0
    assert source == "test_updated"


def test_insert_stock_prices_handles_large_batches(conn):
    """Test that insert handles large datasets efficiently."""

    stock_id = upsert_stock(conn, "LARGE")

    # Generate 5000 rows
    base_date = date(2000, 1, 1)
    rows = []
    for i in range(5000):
        rows.append({
            "date": base_date + timedelta(days=i),
            "close": 100.0 + i * 0.01,
            "adjusted_close": 100.0 + i * 0.01,
            "volume": 1000000,
            "source": "test",
        })

    start = time.time()
    inserted = insert_stock_prices(conn, stock_id, rows, update_existing=False)
    elapsed = time.time() - start

    assert inserted == 5000
    # 5000 rows should complete in < 3 seconds with batching
    # Row-by-row would take 15-50+ seconds
    assert elapsed < 3.0, f"Large insert took {elapsed:.2f}s - performance issue detected"
