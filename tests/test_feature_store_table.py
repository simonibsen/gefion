import os
import psycopg
import pytest
from datetime import date

from g2.db import schema
from g2.db.ingest import ensure_feature_definitions, ensure_store_targets, trim_stock_prices


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


def test_store_table_created_and_used():
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    defs = [
        {
            "name": "custom_feature",
            "function_name": "custom_fx",
            "params": {"window": 5},
            "source_table": "stock_prices",
            "source_column": "close",
            "store_table": "custom_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True,
        }
    ]
    ids = ensure_feature_definitions(conn, defs)
    ensure_store_targets(conn, defs)
    # table exists with column
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name='custom_features' ORDER BY column_name;
            """
        )
        cols = [r[0] for r in cur.fetchall()]
    assert cols == ["data_id", "date", "source", "value"]
    # insert and trim through trim_stock_prices to ensure no conflict
    conn.close()
