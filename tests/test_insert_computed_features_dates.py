import os
from datetime import date, datetime

import psycopg
import pytest

from gefion.db import schema
from gefion.db.ingest import insert_computed_features, ensure_feature_definitions, ensure_store_targets

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def require_db():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()[0]


@pytest.fixture(autouse=True)
def clean_db():
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        # Use targeted cleanup instead of DROP SCHEMA to avoid deadlocks
        if _table_exists(cur, 'computed_features'):
            cur.execute("DELETE FROM computed_features WHERE data_id = 1;")
        if _table_exists(cur, 'feature_definitions'):
            cur.execute("DELETE FROM feature_definitions WHERE name = 'indicator_rsi_14';")
        if _table_exists(cur, 'stocks'):
            cur.execute("DELETE FROM stocks WHERE symbol = 'TEST';")
    conn.close()
    yield


def test_insert_computed_features_accepts_date_strings():
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    # Create test stock
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (id, symbol) VALUES (1, 'TEST')")
    defs = [
        {
            "name": "indicator_rsi_14",
            "function_name": "indicator",
            "params": {"indicator": "rsi"},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True,
        }
    ]
    fid_map = ensure_feature_definitions(conn, defs)
    ensure_store_targets(conn, defs)
    fid = fid_map["indicator_rsi_14"]
    rows = [
        {"date": "2025-01-01", "rsi_14": 50.0},
        {"date": datetime(2025, 1, 2), "rsi_14": 55.0},
    ]
    inserted = insert_computed_features(conn, data_id=1, rows=rows, feature_map={"rsi_14": fid})
    assert inserted == 2
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM computed_features;")
        cnt = cur.fetchone()[0]
    assert cnt == 2
    conn.close()
