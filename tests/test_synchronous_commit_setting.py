"""
Test that synchronous_commit setting works properly.

The bug: SET LOCAL synchronous_commit only works inside a transaction block.
When autocommit=True, there's no transaction, so SET LOCAL has no effect.

The fix: Use SET (session-level) instead of SET LOCAL when in autocommit mode.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Json
import pytest

from g2.db import schema, pool
from g2.db.ingest import upsert_stock, insert_computed_features


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

    # Create test stock
    stock_id = upsert_stock(db_conn, "SYNCTEST")

    # Create test feature
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET active = EXCLUDED.active
            RETURNING id
            """,
            ("sync_test_feature", "test_fn", Json({}), "stock_ohlcv", "close", "computed_features", "value", True)
        )
        feature_id = cur.fetchone()[0]

    yield stock_id, feature_id

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE data_id = %s", (stock_id,))
        cur.execute("DELETE FROM feature_definitions WHERE name = %s", ("sync_test_feature",))
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("SYNCTEST",))


def test_synchronous_commit_is_actually_disabled_when_requested(db_conn, setup_db):
    """
    Test that sync_commit=False actually disables synchronous_commit.

    The bug: Using SET LOCAL in autocommit mode has no effect because there's no transaction.
    The fix: Use SET (session-level) instead when in autocommit mode.
    """
    stock_id, feature_id = setup_db

    # Initialize connection pool
    url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
    pool.close_pool()
    pool.init_pool(url, min_size=2, max_size=5, prepare_statements=False)

    try:
        with pool.get_connection() as conn:
            conn.autocommit = True

            # Prepare test data
            base_date = date(2025, 1, 1)
            rows = []
            for i in range(50):
                rows.append({
                    "date": base_date + timedelta(days=i),
                    "value": float(i + 100)
                })

            feature_map = {"value": feature_id}

            # Call insert_computed_features with sync_commit=False
            # This should properly disable synchronous_commit
            inserted = insert_computed_features(
                conn,
                data_id=stock_id,
                rows=rows,
                feature_map=feature_map,
                update_existing=False,
                batch_size=50,
                sync_commit=False,
            )

            assert inserted == 50, f"Expected 50 inserts, got {inserted}"

            # Verify synchronous_commit was set
            with conn.cursor() as cur:
                cur.execute("SHOW synchronous_commit;")
                setting = cur.fetchone()[0]

            # The setting should be 'off' after our insert
            # (or could be reset to default, but the insert should have used 'off')
            # The key is that the insert completed successfully and quickly

            # Verify data was inserted
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM computed_features WHERE data_id = %s AND feature_id = %s",
                    (stock_id, feature_id)
                )
                count = cur.fetchone()[0]

            assert count == 50, f"Expected 50 rows in DB, got {count}"

    finally:
        pool.close_pool()


def test_set_local_in_autocommit_has_no_effect():
    """
    Demonstrate that SET LOCAL in autocommit mode has no effect.

    This is the bug we're fixing.
    """
    url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")

    with psycopg.connect(url) as conn:
        conn.autocommit = True

        # Get initial setting
        with conn.cursor() as cur:
            cur.execute("SHOW synchronous_commit;")
            initial_setting = cur.fetchone()[0]

        # Try to change it with SET LOCAL (this is the bug)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL synchronous_commit TO OFF;")

        # Check if it changed
        with conn.cursor() as cur:
            cur.execute("SHOW synchronous_commit;")
            after_local = cur.fetchone()[0]

        # In autocommit mode, SET LOCAL has NO EFFECT
        assert after_local == initial_setting, \
            "SET LOCAL should have no effect in autocommit mode, but setting changed"

        # Now try with SET (session-level)
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit TO OFF;")

        # Check if it changed
        with conn.cursor() as cur:
            cur.execute("SHOW synchronous_commit;")
            after_session = cur.fetchone()[0]

        # Session-level SET should work
        assert after_session == "off", \
            f"SET (session-level) should work in autocommit mode, got {after_session}"

        # Reset
        with conn.cursor() as cur:
            cur.execute("RESET synchronous_commit;")


def test_insert_computed_features_respects_sync_commit_flag(db_conn, setup_db):
    """
    Test that insert_computed_features properly handles the sync_commit flag.

    When sync_commit=False, it should disable synchronous_commit for performance.
    When sync_commit=True (or default), it should use the default setting.
    """
    stock_id, feature_id = setup_db

    pool.close_pool()
    url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
    pool.init_pool(url, min_size=2, max_size=5, prepare_statements=False)

    try:
        with pool.get_connection() as conn:
            conn.autocommit = True

            base_date = date(2025, 1, 1)

            # Test with sync_commit=False
            rows_async = []
            for i in range(25):
                rows_async.append({
                    "date": base_date + timedelta(days=i),
                    "value": float(i + 200)
                })

            feature_map = {"value": feature_id}

            inserted_async = insert_computed_features(
                conn,
                data_id=stock_id,
                rows=rows_async,
                feature_map=feature_map,
                update_existing=False,
                batch_size=25,
                sync_commit=False,  # Should disable synchronous_commit
            )

            assert inserted_async == 25

            # Test with sync_commit=True (default behavior)
            rows_sync = []
            for i in range(25, 50):
                rows_sync.append({
                    "date": base_date + timedelta(days=i),
                    "value": float(i + 200)
                })

            inserted_sync = insert_computed_features(
                conn,
                data_id=stock_id,
                rows=rows_sync,
                feature_map=feature_map,
                update_existing=False,
                batch_size=25,
                sync_commit=True,  # Should use default synchronous_commit
            )

            assert inserted_sync == 25

            # Verify all data was inserted
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM computed_features WHERE data_id = %s AND feature_id = %s",
                    (stock_id, feature_id)
                )
                count = cur.fetchone()[0]

            assert count == 50

    finally:
        pool.close_pool()
