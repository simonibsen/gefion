import os

import pytest
import psycopg

from gefion.db import schema


@pytest.fixture(scope="module", autouse=True)
def _restore_after_module():
    """This module DROPS every table (destructive by design). Per the house
    rule in conftest.restore_test_db's docstring, destroyers MUST restore the
    canonical db-init state on the way out — later modules (and the next
    session's db-init) must never see a gutted database."""
    yield
    from conftest import restore_test_db
    restore_test_db()


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


def test_stocks_table_exists(conn):
    schema.create_stocks_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'stocks'
            ORDER BY ordinal_position;
            """
        )
        cols = cur.fetchall()
    assert [c[0] for c in cols][:2] == ["id", "symbol"]


def test_stock_ohlcv_hypertable(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'stock_ohlcv';
            """
        )
        assert cur.fetchone() is not None

        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'stock_ohlcv'
            ORDER BY ordinal_position;
            """
        )
        cols = [r[0] for r in cur.fetchall()]

        assert [
            "id",
            "data_id",
            "date",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "dividend_amount",
            "split_coefficient",
            "volume",
            "source",
        ] == cols

        cur.execute(
            """
            SELECT constraint_type, constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'stock_ohlcv'
            ORDER BY constraint_type;
            """
        )
        constraints = [r[0] for r in cur.fetchall()]
        assert "PRIMARY KEY" in constraints


def test_stock_ohlcv_unique_per_stock_and_date(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stocks (symbol) VALUES ('IBM') RETURNING id;
            """
        )
        stock_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stock_ohlcv (data_id, date) VALUES (%s, '2023-01-01')",
            (stock_id,),
        )
        cur.execute(
            "INSERT INTO stock_ohlcv (data_id, date) VALUES (%s, '2023-01-02')",
            (stock_id,),
        )

        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                """
                INSERT INTO stock_ohlcv (data_id, date) VALUES (%s, '2023-01-01');
                """,
                (stock_id,),
            )
