"""
Test that writer thread errors are properly propagated.

The bug: Writer thread errors are appended to a list but don't cause the operation to fail.
Users think writes succeeded when they partially failed.

The fix: Raise an exception if writer_errors is non-empty after draining the queue.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
from datetime import date, timedelta
from unittest.mock import patch

import psycopg
from psycopg.types.json import Json
import pytest

from g2.config import load_settings
from g2.db import schema, pool
from g2.db.ingest import upsert_stock
from g2.features.dispatcher import compute_features
from g2.cli_helpers import upsert_feature_function


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
    stock_id = upsert_stock(db_conn, "ERRORTEST")

    # Insert test price data
    base_date = date(2025, 1, 1)
    with db_conn.cursor() as cur:
        for i in range(50):
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
    return [{"date": row["date"], "error_test_feature": float(row["close"])} for row in rows]
'''
    upsert_feature_function(db_conn, {
        "name": "test_error",
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
            ("error_test_feature", "test_error", Json({}), "stock_ohlcv", "close", "computed_features", "value", True)
        )

    yield stock_id

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE data_id = %s", (stock_id,))
        cur.execute("DELETE FROM feature_definitions WHERE name = %s", ("error_test_feature",))
        cur.execute("DELETE FROM feature_functions WHERE name = %s", ("test_error",))
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("ERRORTEST",))


def test_writer_errors_are_propagated(db_conn, setup_db):
    """
    Test that writer thread errors cause compute_features to raise an exception.

    With the bug, errors are silently collected but the function returns success.
    With the fix, errors should cause an exception to be raised.
    """
    stock_id = setup_db

    # Initialize connection pool
    url = get_db_url()
    pool.close_pool()
    pool.init_pool(url, min_size=3, max_size=10, prepare_statements=False)

    try:
        with pool.get_connection() as main_conn:
            main_conn.autocommit = True

            # Patch insert_computed_features in the dispatcher module (where it's imported)
            from g2.features import dispatcher
            from g2.db import ingest
            original_insert = ingest.insert_computed_features
            call_count = [0]

            def failing_insert(*args, **kwargs):
                call_count[0] += 1
                # Fail on the first call from a writer thread
                if call_count[0] == 1:
                    raise RuntimeError("Simulated writer thread error")
                return original_insert(*args, **kwargs)

            with patch.object(dispatcher, 'insert_computed_features', side_effect=failing_insert):
                # This should raise an exception due to the writer error
                with pytest.raises(Exception) as exc_info:
                    result = compute_features(
                        main_conn,
                        data_id=stock_id,
                        function_names=["test_error"],
                        incremental=False,
                        writer_workers=2,  # Use writer threads
                        profile=False,
                    )

                # The exception should mention writer errors
                assert "writer" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()

    finally:
        pool.close_pool()


def test_compute_features_fails_on_writer_errors(db_conn, setup_db):
    """
    Test that compute_features properly reports writer thread failures.

    Even if some writes succeed, the operation should fail if any writer encounters an error.
    """
    stock_id = setup_db

    # Register test compute function in feature_functions table
    test_function_body = '''
def compute(rows, specs):
    return [{"date": row["date"], "error_test_feature": float(row["close"])} for row in rows]
'''
    upsert_feature_function(db_conn, {
        "name": "test_error2",
        "version": "1.0",
        "language": "python",
        "function_body": test_function_body,
        "status": "active",
        "enabled": True,
    })

    # Register another feature for this test
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET active = EXCLUDED.active
            """,
            ("error_test_feature2", "test_error2", Json({}), "stock_ohlcv", "close", "computed_features", "value", True)
        )

    url = get_db_url()
    pool.close_pool()
    pool.init_pool(url, min_size=3, max_size=10, prepare_statements=False)

    try:
        with pool.get_connection() as main_conn:
            main_conn.autocommit = True

            from g2.features import dispatcher
            from g2.db import ingest
            original_insert = ingest.insert_computed_features

            def always_failing_insert(*args, **kwargs):
                raise ValueError("Writer insert failed")

            with patch.object(dispatcher, 'insert_computed_features', side_effect=always_failing_insert):
                # Should raise an exception
                try:
                    result = compute_features(
                        main_conn,
                        data_id=stock_id,
                        function_names=["test_error2"],
                        incremental=False,
                        writer_workers=2,
                        profile=False,
                    )
                    # If we get here, the bug exists - errors were silently swallowed
                    pytest.fail("Expected compute_features to raise an exception due to writer errors, but it succeeded")
                except Exception as e:
                    # This is expected - writer errors should cause failure
                    assert "writer" in str(e).lower() or "error" in str(e).lower() or "failed" in str(e).lower()

    finally:
        # Cleanup
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM feature_definitions WHERE name = %s", ("error_test_feature2",))
            cur.execute("DELETE FROM feature_functions WHERE name = %s", ("test_error2",))
        pool.close_pool()
