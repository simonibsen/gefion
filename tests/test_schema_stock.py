import os

import pytest
import psycopg

from g2.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:  # pragma: no cover - infra guard
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean_db(conn):
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield


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


def test_stock_prices_hypertable(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'stock_prices';
            """
        )
        assert cur.fetchone() is not None

        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'stock_prices'
            ORDER BY ordinal_position;
            """
        )
        cols = [r[0] for r in cur.fetchall()]

        assert ["id", "data_id", "date", "open", "high", "low", "close", "adjusted_close", "volume", "source"] == cols

        cur.execute(
            """
            SELECT constraint_type, constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'stock_prices'
            ORDER BY constraint_type;
            """
        )
        constraints = [r[0] for r in cur.fetchall()]
        assert "PRIMARY KEY" in constraints


def test_stock_prices_unique_per_stock_and_date(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stocks (symbol) VALUES ('IBM') RETURNING id;
            """
        )
        stock_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO stock_prices (data_id, date) VALUES (%s, '2023-01-01');
            INSERT INTO stock_prices (data_id, date) VALUES (%s, '2023-01-02');
            """,
            (stock_id, stock_id),
        )

        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                """
                INSERT INTO stock_prices (data_id, date) VALUES (%s, '2023-01-01');
                """,
                (stock_id,),
            )
