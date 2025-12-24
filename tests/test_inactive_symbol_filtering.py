"""
Tests for filtering out Inactive symbols from data updates and feature computation.

Requirements:
1. filter_symbols_needing_update() should exclude Inactive symbols
2. filter_symbols_needing_features() should exclude Inactive symbols
3. Universe ingest should not register symbols with Inactive status
"""
import os
import pytest
import psycopg
from datetime import date, timedelta
from g2.db import schema
from g2.db.ingest import filter_symbols_needing_update, filter_symbols_needing_features


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        # Ensure tables exist before cleanup
        schema.create_stocks_table(conn)
        schema.create_stock_ohlcv_table(conn)
        schema.create_feature_definitions_table(conn)
        schema.create_computed_features_table(conn)
        # Clean up before tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM stock_ohlcv WHERE data_id IN (
                    SELECT id FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
                )
            """)
            cur.execute("""
                DELETE FROM computed_features WHERE data_id IN (
                    SELECT id FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
                )
            """)
            cur.execute("""
                DELETE FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
            """)
        yield conn
        # Clean up after tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM stock_ohlcv WHERE data_id IN (
                    SELECT id FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
                )
            """)
            cur.execute("""
                DELETE FROM computed_features WHERE data_id IN (
                    SELECT id FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
                )
            """)
            cur.execute("""
                DELETE FROM stocks WHERE symbol LIKE 'INACTIVE_TEST_%'
            """)


@pytest.fixture
def setup_test_stocks(db_conn):
    """Create test stocks with different statuses."""
    schema.create_stocks_table(db_conn)
    schema.create_stock_ohlcv_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    # Create test stocks
    with db_conn.cursor() as cur:
        # Active stock with old price data
        cur.execute("""
            INSERT INTO stocks (symbol, status)
            VALUES (%s, %s)
            RETURNING id
        """, ("INACTIVE_TEST_ACTIVE", "Active"))
        active_id = cur.fetchone()[0]

        # Insert old price data
        old_date = date.today() - timedelta(days=10)
        cur.execute("""
            INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (active_id, old_date, 100.0, 105.0, 99.0, 102.0, 1000000))

        # Inactive stock with old price data
        cur.execute("""
            INSERT INTO stocks (symbol, status)
            VALUES (%s, %s)
            RETURNING id
        """, ("INACTIVE_TEST_DELISTED", "Inactive"))
        inactive_id = cur.fetchone()[0]

        # Insert old price data
        cur.execute("""
            INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (inactive_id, old_date, 50.0, 52.0, 49.0, 51.0, 500000))

        # Stock with NULL status (should be included)
        cur.execute("""
            INSERT INTO stocks (symbol, status)
            VALUES (%s, %s)
            RETURNING id
        """, ("INACTIVE_TEST_NULL", None))
        null_id = cur.fetchone()[0]

        # Insert old price data
        cur.execute("""
            INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (null_id, old_date, 75.0, 77.0, 74.0, 76.0, 750000))

    db_conn.commit()
    return {"active_id": active_id, "inactive_id": inactive_id, "null_id": null_id}


def test_filter_symbols_needing_update_excludes_inactive(db_conn, setup_test_stocks):
    """Test that filter_symbols_needing_update() excludes Inactive symbols."""
    target_date = date.today()

    symbols = ["INACTIVE_TEST_ACTIVE", "INACTIVE_TEST_DELISTED", "INACTIVE_TEST_NULL"]

    result = filter_symbols_needing_update(db_conn, symbols, target_date)

    # Should include Active and NULL status symbols, but NOT Inactive
    assert "INACTIVE_TEST_ACTIVE" in result
    assert "INACTIVE_TEST_NULL" in result
    assert "INACTIVE_TEST_DELISTED" not in result  # ⭐ Inactive excluded


def test_filter_symbols_needing_features_excludes_inactive(db_conn, setup_test_stocks):
    """Test that filter_symbols_needing_features() excludes Inactive symbols."""
    target_date = date.today()

    symbols = ["INACTIVE_TEST_ACTIVE", "INACTIVE_TEST_DELISTED", "INACTIVE_TEST_NULL"]

    # Test with function_name parameter (indicator features)
    result = filter_symbols_needing_features(db_conn, symbols, target_date, function_name='indicator')

    # Should include Active and NULL status symbols, but NOT Inactive
    assert "INACTIVE_TEST_ACTIVE" in result
    assert "INACTIVE_TEST_NULL" in result
    assert "INACTIVE_TEST_DELISTED" not in result  # ⭐ Inactive excluded

    # Test without function_name parameter (all features)
    result_all = filter_symbols_needing_features(db_conn, symbols, target_date, function_name=None)

    # Should also exclude Inactive symbols when checking all features
    assert "INACTIVE_TEST_ACTIVE" in result_all
    assert "INACTIVE_TEST_NULL" in result_all
    assert "INACTIVE_TEST_DELISTED" not in result_all  # ⭐ Inactive excluded


def test_filter_excludes_inactive_with_no_data(db_conn):
    """Test that Inactive symbols are excluded even if they have no data at all."""
    schema.create_stocks_table(db_conn)
    schema.create_stock_ohlcv_table(db_conn)

    # Create an Inactive stock with NO price data
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO stocks (symbol, status)
            VALUES (%s, %s)
        """, ("INACTIVE_TEST_NODATA", "Inactive"))

    db_conn.commit()

    target_date = date.today()
    symbols = ["INACTIVE_TEST_NODATA"]

    result = filter_symbols_needing_update(db_conn, symbols, target_date)

    # Should NOT include Inactive symbol even though it has no data
    assert "INACTIVE_TEST_NODATA" not in result

    # Clean up
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("INACTIVE_TEST_NODATA",))
    db_conn.commit()


def test_filter_handles_mixed_symbols(db_conn, setup_test_stocks):
    """Test filtering with a mix of existing and non-existing symbols."""
    target_date = date.today()

    symbols = [
        "INACTIVE_TEST_ACTIVE",
        "INACTIVE_TEST_DELISTED",
        "NONEXISTENT_SYMBOL_123",  # Doesn't exist in DB
        "INACTIVE_TEST_NULL"
    ]

    result = filter_symbols_needing_update(db_conn, symbols, target_date)

    # Should include Active, NULL, and non-existent (s.id IS NULL condition)
    # But NOT Inactive
    assert "INACTIVE_TEST_ACTIVE" in result
    assert "INACTIVE_TEST_NULL" in result
    assert "NONEXISTENT_SYMBOL_123" in result  # New symbols should be included
    assert "INACTIVE_TEST_DELISTED" not in result  # Inactive excluded


def test_upsert_stock_with_status(db_conn):
    """Test that upsert_stock stores status when provided."""
    from g2.db.ingest import upsert_stock

    schema.create_stocks_table(db_conn)

    # Insert stock with Active status
    stock_id = upsert_stock(db_conn, "TEST_STATUS_ACTIVE", status="Active")

    # Verify status was stored
    with db_conn.cursor() as cur:
        cur.execute("SELECT symbol, status FROM stocks WHERE id = %s", (stock_id,))
        row = cur.fetchone()
        assert row[0] == "TEST_STATUS_ACTIVE"
        assert row[1] == "Active"

    # Update to Inactive status
    stock_id2 = upsert_stock(db_conn, "TEST_STATUS_ACTIVE", status="Inactive")
    assert stock_id == stock_id2  # Same ID

    # Verify status was updated
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM stocks WHERE id = %s", (stock_id,))
        row = cur.fetchone()
        assert row[0] == "Inactive"

    # Clean up
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("TEST_STATUS_ACTIVE",))
    db_conn.commit()


def test_upsert_stock_without_status_defaults_to_null(db_conn):
    """Test that upsert_stock without status leaves it NULL."""
    from g2.db.ingest import upsert_stock

    schema.create_stocks_table(db_conn)

    # Insert stock without status
    stock_id = upsert_stock(db_conn, "TEST_NO_STATUS")

    # Verify status is NULL
    with db_conn.cursor() as cur:
        cur.execute("SELECT symbol, status FROM stocks WHERE id = %s", (stock_id,))
        row = cur.fetchone()
        assert row[0] == "TEST_NO_STATUS"
        assert row[1] is None  # NULL status

    # Clean up
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = %s", ("TEST_NO_STATUS",))
    db_conn.commit()
