import os
import numpy as np
import psycopg
import pytest
from datetime import date

from g2.db import schema
from g2.db.ingest import insert_computed_features, ensure_feature_definitions, ensure_store_targets

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


def test_insert_computed_features_accepts_numpy_int():
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
            "name": "indicator_adx_14",
            "function_name": "indicator",
            "params": {"indicator": "adx"},
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
    fid = fid_map["indicator_adx_14"]
    rows = [{"date": date(2025, 1, 1), "adx_14": 10.0}]
    inserted = insert_computed_features(conn, data_id=np.int64(1), rows=rows, feature_map={"adx_14": fid})
    assert inserted == 1
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM computed_features;")
        cnt = cur.fetchone()[0]
    assert cnt == 1
    conn.close()
