import os
from datetime import date

import psycopg
import pytest

from g2.db import schema
from g2.db.ingest import trim_stock_ohlcv, upsert_stock


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


def test_trim_stock_ohlcv_date_and_symbol():
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)

    s1 = upsert_stock(conn, "AAA")
    s2 = upsert_stock(conn, "BBB")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stock_ohlcv (data_id, date) VALUES (%s, %s), (%s, %s), (%s, %s);
            INSERT INTO stock_ohlcv (data_id, date) VALUES (%s, %s), (%s, %s);
            """,
            (
                s1,
                date(2023, 1, 1),
                s1,
                date(2023, 6, 1),
                s1,
                date(2024, 1, 1),
                s2,
                date(2023, 1, 1),
                s2,
                date(2024, 1, 1),
            ),
        )
    deleted = trim_stock_ohlcv(conn, before=date(2023, 6, 1), after=date(2023, 12, 31), symbols=["AAA"])
    # Should delete AAA before 2023-06-01 and after 2023-12-31 (two rows), BBB untouched
    assert deleted == 2
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stock_ohlcv;")
        total = cur.fetchone()[0]
    assert total == 3
    conn.close()
