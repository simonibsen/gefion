"""Fundamentals refresh: freshness from the data itself (017).

TDD: written FIRST. The old population query keyed off stocks.updated_at —
a SHARED column that listing-meta (monthly, all rows) and the skip-marker
path also bump. One listing-meta run made every stock look fresh for
--max-age days and froze the fundamentals snapshot (prod: stuck at
2026-07-07 while the weekly cron reported "up to date"). Freshness must
key off stocks_fundamentals' own MAX(date) per stock; ETFs (no OVERVIEW
fundamentals) are excluded from the population entirely.

Also pins the issue-#79 width fix on existing databases: the ratio columns
must accept provider garbage extremes (migration 20260722 aligns pre-14,6
tables with canonical schema.sql).
"""
import os
from datetime import date, timedelta

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stocks_fundamentals_table(c)
    def _wipe(cur):
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QFU%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QFU%'")

    with c.cursor() as cur:
        _wipe(cur)
    with c.cursor() as cur:
        for sym, at in (("QFU_FRESH", "Stock"), ("QFU_STALE", "Stock"),
                        ("QFU_NEVER", "Stock"), ("QFU_ETF", "ETF")):
            cur.execute(
                "INSERT INTO stocks (symbol, status, asset_type, updated_at) "
                "VALUES (%s, 'Active', %s, NOW()) RETURNING id", (sym, at))
        cur.execute("SELECT id, symbol FROM stocks WHERE symbol LIKE 'QFU%'")
        ids = {sym: i for i, sym in cur.fetchall()}
        cur.execute(
            "INSERT INTO stocks_fundamentals (data_id, date, pe_ratio) "
            "VALUES (%s, %s, 12.5)", (ids["QFU_FRESH"], date.today()))
        cur.execute(
            "INSERT INTO stocks_fundamentals (data_id, date, pe_ratio) "
            "VALUES (%s, %s, 12.5)",
            (ids["QFU_STALE"], date.today() - timedelta(days=90)))
    yield c, ids
    with c.cursor() as cur:
        _wipe(cur)
    c.close()


class TestStalePopulation:
    def test_freshness_from_fundamentals_not_updated_at(self, conn):
        """All four stocks have updated_at=NOW() (as after a listing-meta
        run) — selection must STILL find the stale and never-fetched ones."""
        c, ids = conn
        from gefion.cli import _stale_fundamentals_stocks
        rows = _stale_fundamentals_stocks(c, max_age_days=30, force=False,
                                          limit=None)
        got = {sym for _, sym in rows if sym.startswith("QFU")}
        assert got == {"QFU_STALE", "QFU_NEVER"}

    def test_etfs_never_in_population(self, conn):
        c, ids = conn
        from gefion.cli import _stale_fundamentals_stocks
        rows = _stale_fundamentals_stocks(c, max_age_days=0, force=True,
                                          limit=None)
        got = {sym for _, sym in rows if sym.startswith("QFU")}
        assert "QFU_ETF" not in got
        assert {"QFU_FRESH", "QFU_STALE", "QFU_NEVER"} <= got

    def test_never_fetched_ordered_first(self, conn):
        c, ids = conn
        from gefion.cli import _stale_fundamentals_stocks
        rows = [sym for _, sym in
                _stale_fundamentals_stocks(c, max_age_days=30, force=False,
                                           limit=None)
                if sym.startswith("QFU")]
        assert rows[0] == "QFU_NEVER"   # NULLS FIRST: oldest need first


class TestProviderExtremesStore:
    def test_issue_79_garbage_extremes_store_verbatim(self, conn):
        """Beta -503341.44 and DividendYield 1000000.0 (real provider
        output) must store — the 8,4 columns on pre-migration databases
        rejected them with numeric overflow write errors."""
        c, ids = conn
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO stocks_fundamentals (data_id, date, beta, "
                "dividend_yield) VALUES (%s, %s, %s, %s)",
                (ids["QFU_NEVER"], date.today(), -503341.44, 1000000.0))
            cur.execute(
                "SELECT beta, dividend_yield FROM stocks_fundamentals "
                "WHERE data_id = %s", (ids["QFU_NEVER"],))
            beta, dy = cur.fetchone()
        assert float(beta) == -503341.44
        assert float(dy) == 1000000.0
