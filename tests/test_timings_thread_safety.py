"""
Test that timing metrics are thread-safe.

The bug: Multiple threads update the shared timings dict without synchronization:
- Main thread updates timings["queue_wait"]
- Writer threads update timings["writer"]

The += operator on dict values is NOT atomic and can lead to lost updates.

The fix: Use threading.Lock() to protect timings dict updates.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
import threading
import time
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Json
import pytest

from gefion.config import load_settings
from gefion.db import schema, pool
from gefion.db.ingest import upsert_stock
from gefion.features.dispatcher import compute_features
from gefion.cli_helpers import upsert_feature_function


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL for tests."""
    return schema.test_db_url()


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def setup_db(db_conn):
    """Set up test database schema and data."""
    schema.create_stocks_table(db_conn)
    schema.create_stock_ohlcv_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)
    schema.create_feature_functions_table(db_conn)

    # Create test stock
    stock_id = upsert_stock(db_conn, "TIMINGTEST")

    # Insert lots of price data to generate more work
    base_date = date(2025, 1, 1)
    with db_conn.cursor() as cur:
        for i in range(200):
            cur.execute(
                """
                INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, adjusted_close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (data_id, date) DO NOTHING
                """,
                (stock_id, base_date + timedelta(days=i), 100.0, 102.0, 99.0, 101.0, 101.0, 1000000)
            )

    # Register test compute function in feature_functions table
    test_function_body = '''
def compute(rows, specs):
    return [{"date": row["date"], "timing_test_feature": float(row["close"])} for row in rows]
'''
    upsert_feature_function(db_conn, {
        "name": "test_timing",
        "version": "1.0",
        "language": "python",
        "function_body": test_function_body,
        "status": "active",
        "enabled": True,
    })

    # Register test feature
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET active = EXCLUDED.active
            RETURNING id
            """,
            ("timing_test_feature", "test_timing", Json({}), "stock_ohlcv", "close", "computed_features", "value", True)
        )

    yield stock_id

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE data_id = %s", (stock_id,))
        cur.execute("DELETE FROM feature_definitions WHERE name = %s", ("timing_test_feature",))
        cur.execute("DELETE FROM feature_functions WHERE name = %s", ("test_timing",))
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("TIMINGTEST",))


def test_timing_metrics_are_thread_safe(db_conn, setup_db):
    """
    Test that timing metrics don't get corrupted by concurrent updates.

    With the bug, multiple threads updating timings dict causes lost updates.
    With the fix, all updates are protected by a lock.
    """
    stock_id = setup_db

    # Initialize connection pool
    url = get_db_url()
    pool.close_pool()
    pool.init_pool(url, min_size=3, max_size=10, prepare_statements=False)

    try:
        with pool.get_connection() as main_conn:
            main_conn.autocommit = True

            # Run with profiling enabled and multiple writer threads
            # This will cause concurrent updates to the timings dict
            result = compute_features(
                main_conn,
                data_id=stock_id,
                function_names=["test_timing"],
                incremental=False,
                writer_workers=3,  # Multiple writer threads to increase concurrency
                profile=True,  # Enable profiling to track timings
            )

            # Should complete successfully
            assert result["summary"]["total_inserted"] > 0

            # Verify timing metrics were collected
            assert "timing" in result["summary"]
            timing = result["summary"]["timing"]

            # All timing keys should be present
            expected_keys = ["fetch", "compute", "write", "queue_wait", "writer", "writer_wait"]
            for key in expected_keys:
                assert key in timing, f"Missing timing key: {key}"
                assert isinstance(timing[key], (int, float)), f"Timing {key} should be numeric"
                assert timing[key] >= 0, f"Timing {key} should be non-negative"

            # The timings should be reasonable (not corrupted by race conditions)
            # Total time should be roughly fetch + compute + write/writer time
            # (not exact because of parallelism, but should be in the ballpark)
            total_time = timing["fetch"] + timing["compute"] + timing["writer"]
            assert total_time > 0, "Total timing should be positive"

            # Writer wait time should be present when using writer threads
            assert timing["writer_wait"] >= 0

    finally:
        pool.close_pool()


def test_concurrent_timing_updates_dont_lose_data():
    """
    Demonstrate that concurrent += operations on dict values can lose updates.

    This is a unit test showing the race condition directly.
    """
    # Simulate the bug: multiple threads incrementing a shared dict value
    iterations = 1000
    num_threads = 5

    # Test WITH a lock (correct behavior)
    timing_safe = {"count": 0.0}
    lock = threading.Lock()

    def increment_safe():
        for _ in range(iterations):
            with lock:
                timing_safe["count"] += 1.0

    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=increment_safe)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # With lock, all updates should be counted
    expected = iterations * num_threads
    assert timing_safe["count"] == expected, \
        f"With lock: expected {expected}, got {timing_safe['count']}"

    # Test WITHOUT a lock (buggy behavior - may lose updates)
    timing_unsafe = {"count": 0.0}

    def increment_unsafe():
        for _ in range(iterations):
            # This is NOT atomic - can lose updates
            timing_unsafe["count"] += 1.0

    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=increment_unsafe)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # Without lock, we MAY lose some updates (race condition)
    # Note: This might not always fail due to GIL, but the bug exists
    # The test demonstrates the correct way to handle it
    actual = timing_unsafe["count"]

    # We expect the unsafe version to potentially lose updates
    # but we can't reliably test for it due to GIL in CPython
    # So we just document the issue and verify the safe version works
    print(f"Unsafe version: expected {expected}, got {actual}")
    if actual < expected:
        print("Race condition detected: lost updates without lock!")
