import os

import psycopg
import pytest

from g2.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:  # pragma: no cover - infra guard
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    # Skip early if DB tests not enabled - before any DB operations
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    connection = create_connection()
    connection.autocommit = True
    with connection.cursor() as cur:
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
    yield connection
    connection.close()


def _index_names(cur, table):
    cur.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE tablename = %s;
        """,
        (table,),
    )
    return cur.fetchall()


def test_stock_ohlcv_brin_and_chunk(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    with conn.cursor() as cur:
        names = _index_names(cur, "stock_ohlcv")
        assert any("BRIN" in idxdef.upper() for _, idxdef in names)
        cur.execute(
            """
            SELECT time_interval
            FROM timescaledb_information.dimensions
            WHERE hypertable_name = 'stock_ohlcv';
            """
        )
        interval = cur.fetchone()[0]
        assert interval is not None


def test_computed_features_brin_and_chunk(conn):
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    with conn.cursor() as cur:
        names = _index_names(cur, "computed_features")
        assert any("BRIN" in idxdef.upper() for _, idxdef in names)
        cur.execute(
            """
            SELECT time_interval
            FROM timescaledb_information.dimensions
            WHERE hypertable_name = 'computed_features';
            """
        )
        interval = cur.fetchone()[0]
        assert interval is not None
