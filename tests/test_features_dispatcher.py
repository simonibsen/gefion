"""Tests for feature computation dispatcher integration."""
import os
from datetime import date, timedelta

import pytest
import psycopg

from g2.db import schema
from g2.db.ingest import upsert_stock, insert_stock_ohlcv
from g2.features.dispatcher import compute_features


def require_db():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
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


def test_compute_indicators_with_sufficient_data():
    """Test that indicators are computed when sufficient price data exists."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    # Create stock with 250 days of price data (enough for all indicators)
    aapl_id = upsert_stock(conn, "AAPL")

    price_rows = []
    base_price = 150.0
    for i in range(250):
        day = date.today() - timedelta(days=250 - i)
        price_rows.append({
            "date": day,
            "open": base_price + i * 0.1,
            "high": base_price + i * 0.1 + 2.0,
            "low": base_price + i * 0.1 - 2.0,
            "close": base_price + i * 0.1 + 1.0,
            "volume": 1000000 + i * 1000,
        })

    insert_stock_ohlcv(conn, aapl_id, price_rows)

    # Create feature definitions for indicators
    with conn.cursor() as cur:
        # RSI_14 - needs 14+ days
        cur.execute("""
            INSERT INTO feature_definitions
            (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES
            ('indicator_rsi_14', 'indicator',
             '{"type": "rsi", "window": 14, "column": "rsi_14"}'::jsonb,
             'stock_ohlcv', 'close', 'computed_features', 'value', true)
        """)

        # SMA_50 - needs 50+ days
        cur.execute("""
            INSERT INTO feature_definitions
            (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES
            ('indicator_sma_50', 'indicator',
             '{"type": "sma50", "window": 50, "column": "sma_50"}'::jsonb,
             'stock_ohlcv', 'close', 'computed_features', 'value', true)
        """)

        # SMA_200 - needs 200+ days
        cur.execute("""
            INSERT INTO feature_definitions
            (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES
            ('indicator_sma_200', 'indicator',
             '{"type": "sma200", "window": 200, "column": "sma_200"}'::jsonb,
             'stock_ohlcv', 'close', 'computed_features', 'value', true)
        """)

    conn.commit()

    # Compute features
    result = compute_features(
        conn,
        data_id=aapl_id,
        incremental=False,
        update_existing=False,
    )

    # Verify results
    assert result['summary']['total_inserted'] > 0, \
        f"Expected features to be computed, got 0 inserts. Result: {result}"

    # Check computed_features table
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT feature_id)
            FROM computed_features
            WHERE data_id = %s
        """, (aapl_id,))
        feature_count = cur.fetchone()[0]

    # Should have computed all 3 indicators
    assert feature_count == 3, \
        f"Expected 3 features (RSI, SMA_50, SMA_200), got {feature_count}"

    conn.close()


def test_compute_indicators_with_insufficient_data():
    """Test that indicators are skipped when insufficient price data exists."""
    conn = require_db()
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    # Create stock with only 30 days of price data (not enough for SMA_200)
    aapl_id = upsert_stock(conn, "AAPL")

    price_rows = []
    base_price = 150.0
    for i in range(30):
        day = date.today() - timedelta(days=30 - i)
        price_rows.append({
            "date": day,
            "open": base_price + i * 0.1,
            "high": base_price + i * 0.1 + 2.0,
            "low": base_price + i * 0.1 - 2.0,
            "close": base_price + i * 0.1 + 1.0,
            "volume": 1000000 + i * 1000,
        })

    insert_stock_ohlcv(conn, aapl_id, price_rows)

    # Create feature definitions
    with conn.cursor() as cur:
        # RSI_14 - needs 14+ days (should work)
        cur.execute("""
            INSERT INTO feature_definitions
            (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES
            ('indicator_rsi_14', 'indicator',
             '{"type": "rsi", "window": 14, "column": "rsi_14"}'::jsonb,
             'stock_ohlcv', 'close', 'computed_features', 'value', true)
        """)

        # SMA_200 - needs 200+ days (should NOT work)
        cur.execute("""
            INSERT INTO feature_definitions
            (name, function_name, params, source_table, source_column, store_table, store_column, active)
            VALUES
            ('indicator_sma_200', 'indicator',
             '{"type": "sma200", "window": 200, "column": "sma_200"}'::jsonb,
             'stock_ohlcv', 'close', 'computed_features', 'value', true)
        """)

    conn.commit()

    # Compute features
    result = compute_features(
        conn,
        data_id=aapl_id,
        incremental=False,
        update_existing=False,
    )

    # Should have computed RSI_14 (has enough data), but not SMA_200
    # RSI_14 should produce results for ~16+ rows (after warmup period)
    assert result['summary']['total_inserted'] > 0, \
        f"Expected RSI_14 to be computed, got 0 inserts. Result: {result}"

    # Check computed_features table
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fd.name, COUNT(*)
            FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE cf.data_id = %s
            GROUP BY fd.name
        """, (aapl_id,))
        features = dict(cur.fetchall())

    # Should have computed RSI_14 but not SMA_200
    assert 'indicator_rsi_14' in features, \
        f"Expected RSI_14 to be computed, got features: {features}"
    assert 'indicator_sma_200' not in features, \
        f"Expected SMA_200 to be skipped, got features: {features}"

    conn.close()
