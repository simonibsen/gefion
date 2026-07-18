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
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
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


# --- series dynamic range on adjusted_close (issue #136) ---------------------------

def _insert_prices(cur, sid, rows):
    for d, adj in rows:
        cur.execute("INSERT INTO stock_ohlcv (data_id, date, close, "
                    "adjusted_close) VALUES (%s, %s, %s, %s)",
                    (sid, d, min(adj, 999999.0), adj))


def test_backfill_series_range_flags_serial_reverse_splitter(conn):
    """A restated magnitude cliff (5e11 -> single digits) earns exactly ONE
    suspect finding for the whole series, dated at the max value — never a
    per-row flood, never a conviction (the restatement is internally
    consistent provider semantics, not trash)."""
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB3', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        _insert_prices(cur, sid, ((date(2020, 1, 2), 5.35e11),
                                  (date(2020, 1, 3), 2.1e8),
                                  (date(2026, 1, 5), 7.0)))
    summary = backfill.run(conn, entity_table="stocks", metric="adjusted_close")
    rows = findings.list_findings(conn, entity_table="stocks", entity_id=sid,
                                  metric="adjusted_close")
    assert len(rows) == 1
    assert rows[0]["rule"] == "series_dynamic_range"
    assert rows[0]["verdict"] == "suspect"
    assert rows[0]["date"] == date(2020, 1, 2)  # the most-restated point
    assert summary["by_rule"].get("series_dynamic_range", 0) >= 1


def test_backfill_series_range_passes_normal_history(conn):
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB4', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        _insert_prices(cur, sid, ((date(2020, 1, 2), 400.0),
                                  (date(2026, 1, 5), 2.0)))
    backfill.run(conn, entity_table="stocks", metric="adjusted_close")
    assert findings.list_findings(conn, entity_table="stocks",
                                  entity_id=sid) == []


def test_backfill_series_range_ignores_nonpositive_floor(conn):
    """A stray zero must not fabricate an infinite ratio — the floor is the
    smallest POSITIVE value."""
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB5', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        _insert_prices(cur, sid, ((date(2020, 1, 2), 0.0),
                                  (date(2020, 1, 3), 90.0),
                                  (date(2026, 1, 5), 110.0)))
    backfill.run(conn, entity_table="stocks", metric="adjusted_close")
    assert findings.list_findings(conn, entity_table="stocks",
                                  entity_id=sid) == []


def test_backfill_series_range_reconciles_after_restatement_heals(conn):
    """Issue-85 semantics extend to the new rule: when the provider re-restates
    and the cliff no longer reproduces, the finding is superseded on the next
    scan — resolved, never deleted."""
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCB6', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        _insert_prices(cur, sid, ((date(2020, 1, 2), 5.0e10),
                                  (date(2026, 1, 5), 6.0)))
    backfill.run(conn, entity_table="stocks", metric="adjusted_close")
    assert findings.list_findings(conn, entity_table="stocks", entity_id=sid,
                                  metric="adjusted_close")
    with conn.cursor() as cur:
        cur.execute("UPDATE stock_ohlcv SET adjusted_close = 12.0 "
                    "WHERE data_id = %s AND date = %s", (sid, date(2020, 1, 2)))
    backfill.run(conn, entity_table="stocks", metric="adjusted_close")
    unresolved = findings.list_findings(conn, entity_table="stocks",
                                        entity_id=sid, metric="adjusted_close")
    assert unresolved == []  # list_findings defaults to unresolved only
    kept = findings.list_findings(conn, entity_table="stocks", entity_id=sid,
                                  metric="adjusted_close", include_resolved=True)
    assert kept and kept[0]["resolved_at"] is not None  # superseded, not deleted
