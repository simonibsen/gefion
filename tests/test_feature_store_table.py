import os
import psycopg
import pytest
from datetime import date

from gefion.db import schema
from gefion.db.ingest import ensure_feature_definitions, ensure_store_targets, trim_stock_ohlcv


DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def require_db():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture(scope="module", autouse=True)
def _restore_db_after_module():
    """Restore canonical test DB state after this module's destructive cleanup
    (issue #29). Without this, every module alphabetically after this one runs
    against a gutted schema until something else re-inits — found when new
    fundamentals tests were the first in that dead zone to need a
    schema.sql-created table."""
    yield
    if DB_TESTS_ENABLED:
        from conftest import restore_test_db
        restore_test_db()


@pytest.fixture(autouse=True)
def clean_db():
    # Skip early if DB tests not enabled - before any DB operations
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        # Drop tables but keep extension intact
        cur.execute("""
            DO $$ DECLARE
                r RECORD;
            BEGIN
                FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                END LOOP;
            END $$;
        """)
        # Ensure TimescaleDB extension is available
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            pass
    conn.close()
    yield


def test_store_table_created_and_used():
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    defs = [
        {
            "name": "custom_feature",
            "function_name": "custom_fx",
            "params": {"window": 5},
            "source_table": "stock_ohlcv",
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
    # insert and trim through trim_stock_ohlcv to ensure no conflict
    conn.close()
