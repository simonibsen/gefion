"""Fundamentals-update OVERVIEW pass must not clobber stocks.exchange (#207).

Since #192, universe-ingest (LISTING_STATUS) is the authoritative writer of
stocks.exchange. The fundamentals-update current-state write must touch only
name/sector/industry/updated_at.
"""
import os

import psycopg
import pytest

from gefion.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    connection = create_connection()
    connection.autocommit = True

    schema.create_stocks_table(connection)

    with connection.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = 'EXCH_TEST_A'")

    yield connection

    with connection.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = 'EXCH_TEST_A'")
    connection.close()


def test_update_stock_current_state_preserves_exchange(conn):
    from gefion.cli import _update_stock_current_state

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol, name, sector, industry, exchange) "
            "VALUES ('EXCH_TEST_A', 'Old Name', 'Old Sector', 'Old Industry', 'NASDAQ') "
            "RETURNING id"
        )
        stock_id = cur.fetchone()[0]

        _update_stock_current_state(
            cur, stock_id, name="New Name", sector="Technology", industry="Software"
        )

        cur.execute(
            "SELECT name, sector, industry, exchange FROM stocks WHERE id = %s",
            (stock_id,),
        )
        row = cur.fetchone()

    assert row[0] == "New Name"
    assert row[1] == "Technology"
    assert row[2] == "Software"
    assert row[3] == "NASDAQ"
