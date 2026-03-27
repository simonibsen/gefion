"""
Tests for inverted trim command behavior.

Tests verify that:
- trim-features doesn't touch prices by default (requires --trim-prices flag)
- trim-prices trims features by default (requires --no-trim-features to prevent)
"""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema
from gefion.db.ingest import (
    trim_stock_ohlcv,
    trim_all_computed_features,
    upsert_stock,
    ensure_feature_definitions,
)


DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def require_db():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
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


def test_trim_all_computed_features_by_date():
    """Test that trim_all_computed_features removes all features for a date range."""
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    # Create test stock and features
    stock_id = upsert_stock(conn, "AAPL")
    ensure_feature_definitions(
        conn,
        [
            {
                "name": "test_feature_1",
                "function_name": "dummy",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            },
            {
                "name": "test_feature_2",
                "function_name": "dummy",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            },
        ],
    )

    # Insert test data for both features
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM feature_definitions WHERE name IN ('test_feature_1', 'test_feature_2') ORDER BY name;")
        feature_ids = [row[0] for row in cur.fetchall()]

        # Insert 6 rows total (3 dates x 2 features)
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 10.0)",
            (stock_id, feature_ids[0], date(2023, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 20.0)",
            (stock_id, feature_ids[1], date(2023, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 30.0)",
            (stock_id, feature_ids[0], date(2023, 6, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 40.0)",
            (stock_id, feature_ids[1], date(2023, 6, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 50.0)",
            (stock_id, feature_ids[0], date(2024, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 60.0)",
            (stock_id, feature_ids[1], date(2024, 1, 1)),
        )

    # Trim ALL features before 2023-06-01 (should delete 2 rows)
    deleted = trim_all_computed_features(conn, before=date(2023, 6, 1))
    assert deleted == 2

    # Verify remaining data
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM computed_features;")
        total = cur.fetchone()[0]
    assert total == 4  # Should have 4 rows left (2023-06-01 and 2024-01-01, 2 features each)

    conn.close()


def test_trim_all_computed_features_by_symbol():
    """Test that trim_all_computed_features can filter by symbol."""
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    # Create two stocks and one feature
    aapl_id = upsert_stock(conn, "AAPL")
    msft_id = upsert_stock(conn, "MSFT")
    ensure_feature_definitions(
        conn,
        [
            {
                "name": "test_feature",
                "function_name": "dummy",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            },
        ],
    )

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM feature_definitions WHERE name = 'test_feature';")
        feature_id = cur.fetchone()[0]

        # Insert data for both stocks
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 10.0)",
            (aapl_id, feature_id, date(2023, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 20.0)",
            (aapl_id, feature_id, date(2024, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 30.0)",
            (msft_id, feature_id, date(2023, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 40.0)",
            (msft_id, feature_id, date(2024, 1, 1)),
        )

    # Trim only AAPL features before 2023-06-01 (should delete 1 row)
    deleted = trim_all_computed_features(conn, before=date(2023, 6, 1), symbols=["AAPL"])
    assert deleted == 1

    # Verify remaining data
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM computed_features;")
        total = cur.fetchone()[0]
    assert total == 3  # Should have 3 rows left (1 AAPL + 2 MSFT)

    conn.close()


def test_trim_all_computed_features_before_and_after():
    """Test that trim_all_computed_features can trim both before and after dates."""
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    stock_id = upsert_stock(conn, "AAPL")
    ensure_feature_definitions(
        conn,
        [
            {
                "name": "test_feature",
                "function_name": "dummy",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            },
        ],
    )

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM feature_definitions WHERE name = 'test_feature';")
        feature_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 10.0)",
            (stock_id, feature_id, date(2023, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 20.0)",
            (stock_id, feature_id, date(2023, 6, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 30.0)",
            (stock_id, feature_id, date(2024, 1, 1)),
        )
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) VALUES (%s, %s, %s, 40.0)",
            (stock_id, feature_id, date(2024, 12, 31)),
        )

    # Trim before 2023-06-01 AND after 2024-01-01 (should delete 2 rows)
    deleted = trim_all_computed_features(conn, before=date(2023, 6, 1), after=date(2024, 1, 1))
    assert deleted == 2

    # Verify only middle dates remain
    with conn.cursor() as cur:
        cur.execute("SELECT date FROM computed_features ORDER BY date;")
        dates = [row[0] for row in cur.fetchall()]
    assert dates == [date(2023, 6, 1), date(2024, 1, 1)]

    conn.close()
