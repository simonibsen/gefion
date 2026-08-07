"""
Tests for --exchange filtering in `fundamentals-update` (issue #203).

`_stale_fundamentals_stocks()` must honor an `exchange` filter so that
`gefion fundamentals-update --exchange NYSE` only selects NYSE stocks
instead of silently updating every non-ETF stock.
"""
import os

import pytest

from gefion.cli import _stale_fundamentals_stocks
from gefion.cli_helpers import db_connection, init_schema_tables
from gefion.db import schema

pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run.",
)

_TEST_SYMBOLS = ["ZZEXNYSE", "ZZEXNASDAQ"]


@pytest.fixture
def seeded_stocks():
    """Seed one NYSE and one NASDAQ stock, clean up before/after."""
    url = schema.test_db_url()
    with db_connection(url) as conn:
        init_schema_tables(conn, ["stocks", "stocks_fundamentals"])

        def _clean():
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM stocks WHERE symbol = ANY(%s)",
                    (_TEST_SYMBOLS,),
                )

        _clean()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stocks (symbol, exchange, asset_type)
                VALUES
                    ('ZZEXNYSE', 'NYSE', 'Stock'),
                    ('ZZEXNASDAQ', 'NASDAQ', 'Stock')
                """
            )
        yield conn
        _clean()


def test_stale_fundamentals_stocks_filters_by_exchange(seeded_stocks):
    conn = seeded_stocks

    rows = _stale_fundamentals_stocks(
        conn, max_age_days=30, force=True, limit=None, exchange="NYSE"
    )
    symbols = {symbol for _, symbol in rows}

    assert "ZZEXNYSE" in symbols
    assert "ZZEXNASDAQ" not in symbols


def test_stale_fundamentals_stocks_without_exchange_returns_all(seeded_stocks):
    conn = seeded_stocks

    rows = _stale_fundamentals_stocks(
        conn, max_age_days=30, force=True, limit=None
    )
    symbols = {symbol for _, symbol in rows}

    assert "ZZEXNYSE" in symbols
    assert "ZZEXNASDAQ" in symbols
