"""
Test that writer threads use separate connections, not a shared connection.

This test verifies that each writer thread gets its own database connection
from the pool, rather than sharing a single connection from the main thread,
which would violate psycopg's thread-safety requirements.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
import threading
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Json
import pytest

from g2.db import schema, pool
from g2.db.ingest import upsert_stock
from g2.features.dispatcher import compute_features
from g2.cli_helpers import upsert_feature_function


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
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
    stock_id = upsert_stock(db_conn, "TEST")

    # Insert test price data
    base_date = date(2025, 1, 1)
    with db_conn.cursor() as cur:
        for i in range(100):
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
    return [{"date": row["date"], "test_indicator": row["close"]} for row in rows]
'''
    upsert_feature_function(db_conn, {
        "name": "test_compute",
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
            ("test_indicator", "test_compute", Json({}), "stock_ohlcv", "close", "computed_features", "value", True)
        )

    yield stock_id

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE data_id = %s", (stock_id,))
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id = %s", (stock_id,))
        cur.execute("DELETE FROM feature_definitions WHERE name = %s", ("test_indicator",))
        cur.execute("DELETE FROM feature_functions WHERE name = %s", ("test_compute",))
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("TEST",))


def test_writer_threads_use_separate_connections(db_conn, setup_db):
    """
    Test that writer threads don't share a connection with the main thread.

    This test verifies the fix for issue #1: thread-unsafe connection sharing.
    Each writer thread should get its own connection from the pool.

    The current buggy code passes the main thread's connection to writer threads,
    which violates psycopg's thread-safety requirements and will cause crashes.
    """
    stock_id = setup_db

    # Track which connection objects are used by which threads
    connection_usage = {}
    usage_lock = threading.Lock()

    # Track whether we saw the main connection being used from another thread
    thread_safety_violation = {"detected": False, "details": ""}

    # Initialize connection pool
    url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
    pool.close_pool()
    pool.init_pool(url, min_size=2, max_size=5, prepare_statements=False)

    try:
        # Get a connection for the main thread
        with pool.get_connection() as main_conn:
            main_conn.autocommit = True

            # Track main thread's connection
            main_thread_id = threading.get_ident()
            main_conn_id = id(main_conn)

            with usage_lock:
                connection_usage[main_thread_id] = main_conn_id

            # Wrap the connection's cursor method to track thread usage
            original_cursor = main_conn.cursor

            def tracking_cursor(*args, **kwargs):
                thread_id = threading.get_ident()
                if thread_id != main_thread_id:
                    with usage_lock:
                        thread_safety_violation["detected"] = True
                        thread_safety_violation["details"] = (
                            f"Connection {main_conn_id} from main thread {main_thread_id} "
                            f"was used by thread {thread_id}"
                        )
                return original_cursor(*args, **kwargs)

            main_conn.cursor = tracking_cursor

            try:
                # Run compute_features with writer workers
                result = compute_features(
                    main_conn,
                    data_id=stock_id,
                    function_names=["test_compute"],
                    incremental=False,
                    writer_workers=2,  # Use 2 writer threads
                    profile=False,
                )

                # Verify data was written
                assert result["summary"]["total_inserted"] > 0, "No data was written"

                # The bug: writer threads should NOT use the main connection
                # In the current buggy code, this WILL be detected
                if thread_safety_violation["detected"]:
                    raise AssertionError(
                        f"Thread-safety violation detected! {thread_safety_violation['details']} "
                        "Writer threads must use separate connections from the pool, not the main thread's connection."
                    )

            finally:
                # Restore original cursor method
                main_conn.cursor = original_cursor

    finally:
        pool.close_pool()


def test_writer_threads_can_write_concurrently(db_conn, setup_db):
    """
    Test that multiple writer threads can write to the database concurrently.

    This verifies that the fix allows true parallel writes without connection conflicts.
    """
    stock_id = setup_db

    # Register test compute function in feature_functions table
    test_function_body = '''
def compute(rows, specs):
    result = []
    for row in rows:
        for spec in specs:
            column_name = spec.get("column", spec["name"])
            multiplier = spec.get("multiplier", 1.0)
            result.append({
                "date": row["date"],
                column_name: float(row["close"]) * multiplier
            })
    return result
'''
    upsert_feature_function(db_conn, {
        "name": "test_compute_multi",
        "version": "1.0",
        "language": "python",
        "function_body": test_function_body,
        "status": "active",
        "enabled": True,
    })

    # Register multiple features to generate more work
    with db_conn.cursor() as cur:
        for i in range(5):
            cur.execute(
                """
                INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET active = EXCLUDED.active
                """,
                (f"test_multi_{i}", "test_compute_multi", Json({"multiplier": float(i + 1)}),
                 "stock_ohlcv", "close", "computed_features", "value", True)
            )

    try:
        # Initialize connection pool
        url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
        pool.close_pool()
        pool.init_pool(url, min_size=3, max_size=10, prepare_statements=False)

        try:
            with pool.get_connection() as main_conn:
                main_conn.autocommit = True

                # Run with multiple writer threads
                result = compute_features(
                    main_conn,
                    data_id=stock_id,
                    function_names=["test_compute_multi"],
                    incremental=False,
                    writer_workers=3,
                    profile=False,
                )

                # Should successfully write all data
                if result["summary"]["total_inserted"] == 0:
                    # Print debug info if nothing was inserted
                    import pprint
                    pprint.pprint(result)

                assert result["summary"]["total_inserted"] > 0, f"No data inserted. Result: {result}"
                assert result["summary"]["total_errors"] == 0, f"Errors occurred: {result}"

        finally:
            pool.close_pool()

    finally:
        # Cleanup (delete computed features first due to foreign key constraint)
        with db_conn.cursor() as cur:
            # Get feature IDs
            for i in range(5):
                cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (f"test_multi_{i}",))
                row = cur.fetchone()
                if row:
                    feat_id = row[0]
                    cur.execute("DELETE FROM computed_features WHERE feature_id = %s", (feat_id,))
            # Now delete feature definitions
            for i in range(5):
                cur.execute("DELETE FROM feature_definitions WHERE name = %s", (f"test_multi_{i}",))
            # Delete feature function
            cur.execute("DELETE FROM feature_functions WHERE name = %s", ("test_compute_multi",))
