"""Corroboration tiers + macro family proof (008, T021 — US5).

TDD: written FIRST. The backfill runs the suspect-only tiers where full
history/cross-section is at hand: an episodic spike earns a suspect finding, a
persistent extreme earns nothing, and a macro value violating its catalog
bounds convicts through the identical ledger (SC-307).
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

    def _cleanup(cur):
        cur.execute("DELETE FROM data_quality_findings WHERE context LIKE '%backfill%' "
                    "AND entity_id IN (SELECT id FROM stocks WHERE symbol LIKE 'QCB%')")
        cur.execute("DELETE FROM data_quality_findings WHERE entity_table='macro_series' "
                    "AND entity_id IN (SELECT id FROM macro_series WHERE name LIKE 'qcbtest%')")
        cur.execute("DELETE FROM macro_series_values WHERE series_id IN "
                    "(SELECT id FROM macro_series WHERE name LIKE 'qcbtest%')")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qcbtest%'")
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QCB%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QCB%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def test_backfill_temporal_spike_is_suspect(conn):
    """A spike WITHIN the (loose) definitional envelope is not bounds-trash but
    is an episodic suspect — ROE's envelope is deliberately loose (near-zero
    equity is real), so a 5000 spike between -6 neighbors passes bounds yet
    reverts, earning a suspect finding."""
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB1', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        for d, roe in ((date(2026, 1, 1), -6.1), (date(2026, 1, 2), 5000.0),
                       (date(2026, 1, 3), -6.0)):
            cur.execute("INSERT INTO stocks_fundamentals (data_id, date, "
                        "return_on_equity) VALUES (%s, %s, %s)", (sid, d, roe))
    backfill.run(conn, entity_table="stocks", metric="return_on_equity")
    rows = findings.list_findings(conn, entity_table="stocks", entity_id=sid,
                                  metric="return_on_equity")
    spikes = [r for r in rows if r["rule"] == "temporal_spike"]
    assert spikes and spikes[0]["verdict"] == "suspect"


def test_backfill_persistent_extreme_is_not_flagged(conn):
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB2', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        for d, roe in ((date(2026, 1, 1), -6.1), (date(2026, 1, 2), -6.2),
                       (date(2026, 1, 3), -6.0)):
            cur.execute("INSERT INTO stocks_fundamentals (data_id, date, "
                        "return_on_equity) VALUES (%s, %s, %s)", (sid, d, roe))
    backfill.run(conn, entity_table="stocks", metric="return_on_equity")
    rows = findings.list_findings(conn, entity_table="stocks", entity_id=sid)
    assert rows == []  # persistent degenerate reality, no spike, no bound breach


def test_macro_bounds_convict_through_identical_machinery(conn):
    """SC-307: a macro value violating its catalog bounds convicts via the
    same ledger and shows in the same db-health section."""
    from gefion.macro import catalog as mcatalog
    from gefion.quality import backfill, catalog as qcatalog, findings
    with conn.cursor() as cur:
        sid = mcatalog.ensure_series(conn, "qcbtest_vix", provider="fred:VIXCLS",
                                     kind="index", cadence="daily")
        for d, v in ((date(2026, 1, 5), -3.0), (date(2026, 1, 6), 16.0)):
            cur.execute("INSERT INTO macro_series_values (series_id, date, value) "
                        "VALUES (%s, %s, %s)", (sid, d, v))
    cat = qcatalog.load_default()
    cat.metrics["vix"].series = "qcbtest_vix"
    summary = backfill.run(conn, entity_table="macro_series", cat=cat)
    assert summary["findings"]["created"] >= 1
    rows = findings.list_findings(conn, entity_table="macro_series", entity_id=sid,
                                  metric="vix")
    assert rows and rows[0]["verdict"] == "trash"
    assert rows[0]["observed"] == -3.0
