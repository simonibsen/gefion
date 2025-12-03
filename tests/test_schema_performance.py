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


def test_stock_prices_brin_and_chunk(conn):
    schema.create_stocks_table(conn)
    schema.create_stock_prices_table(conn)
    with conn.cursor() as cur:
        names = _index_names(cur, "stock_prices")
        assert any("BRIN" in idxdef for _, idxdef in names)
        cur.execute(
            """
            SELECT chunk_time_interval
            FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'stock_prices';
            """
        )
        interval = cur.fetchone()[0]
        assert interval is not None


def test_computed_features_brin_and_chunk(conn):
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    with conn.cursor() as cur:
        names = _index_names(cur, "computed_features")
        assert any("BRIN" in idxdef for _, idxdef in names)
        cur.execute(
            """
            SELECT chunk_time_interval
            FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'computed_features';
            """
        )
        interval = cur.fetchone()[0]
        assert interval is not None
