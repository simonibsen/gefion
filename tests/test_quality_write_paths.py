"""Write-path validation tests (008, T010 — US1).

TDD: written FIRST. The covered write paths (fundamentals-update, macro
ingest) validate as they store: garbage lands verbatim AND convicts into the
ledger; validation never blocks or fails a write (FR-303) — an internal
validation error is counted in the summary, not raised.
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
        cur.execute("DELETE FROM data_quality_findings WHERE entity_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QWP%')")
        cur.execute("DELETE FROM data_quality_findings WHERE entity_table = "
                    "'macro_series' AND entity_id IN "
                    "(SELECT id FROM macro_series WHERE name LIKE 'qwptest%')")
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_qwptest%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_qwptest%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qwptest%'")
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QWP%')")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QWP%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QWP%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _seed_stock(cur, symbol, close=25.0):
    cur.execute("INSERT INTO stocks (symbol, name) VALUES (%s, 'X') RETURNING id",
                (symbol,))
    sid = cur.fetchone()[0]
    d = date.today() - timedelta(days=1)
    cur.execute(
        """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
           VALUES (%s, %s, %s, %s, %s, %s, 1000) ON CONFLICT DO NOTHING""",
        (sid, d, close, close, close, close))
    return sid


def _overview(symbol, **fields):
    base = {"Symbol": symbol, "Name": "X", "Sector": "Tech",
            "Industry": "Software", "Exchange": "NASDAQ"}
    base.update(fields)
    return base


# --- fundamentals-update -------------------------------------------------------------

def test_garbage_lands_verbatim_and_convicts(conn):
    from gefion.cli import _write_fundamentals_results
    from gefion.quality import findings
    with conn.cursor() as cur:
        sid = _seed_stock(cur, "QWP1")
    results = [(sid, "QWP1", _overview("QWP1", Beta="-503341.44"), None, False)]
    summary = _write_fundamentals_results(conn, results)
    assert summary["write_errors"] == 0
    assert summary["updated"] == 1
    assert summary["quality_findings"] >= 1
    with conn.cursor() as cur:  # verbatim storage
        cur.execute("SELECT beta FROM stocks_fundamentals WHERE data_id = %s", (sid,))
        assert float(cur.fetchone()[0]) == -503341.44
    rows = findings.list_findings(conn, entity_id=sid, metric="beta")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "trash"
    assert rows[0]["rule"] == "definitional_bound"
    assert rows[0]["observed"] == -503341.44


def test_degenerate_but_real_is_not_convicted(conn):
    """SC-301's other half: the shell company stays unflagged."""
    from gefion.cli import _write_fundamentals_results
    from gefion.quality import findings
    with conn.cursor() as cur:
        sid = _seed_stock(cur, "QWP2")
    results = [(sid, "QWP2",
                _overview("QWP2", ReturnOnEquityTTM="-6.15",
                          OperatingMarginTTM="-1724.0", Beta="-0.692"),
                None, False)]
    summary = _write_fundamentals_results(conn, results)
    assert summary["updated"] == 1
    assert summary["quality_findings"] == 0
    assert findings.list_findings(conn, entity_id=sid) == []


def test_cross_field_contradiction_convicts_in_bounds_values(conn):
    """A yield of 1.5 passes the definitional envelope but contradicts
    dividend-per-share / close by ~750x — trash by construction."""
    from gefion.cli import _write_fundamentals_results
    from gefion.quality import findings
    with conn.cursor() as cur:
        sid = _seed_stock(cur, "QWP3", close=250.0)
    results = [(sid, "QWP3",
                _overview("QWP3", DividendYield="1.5", DividendPerShare="0.5"),
                None, False)]
    _write_fundamentals_results(conn, results)
    rows = findings.list_findings(conn, entity_id=sid, metric="dividend_yield")
    assert len(rows) == 1
    assert rows[0]["rule"] == "cross_field"
    assert rows[0]["verdict"] == "trash"
    assert abs(rows[0]["expected"] - 0.002) < 1e-9


def test_validation_error_never_blocks_the_write(conn, monkeypatch):
    """FR-303: an exception inside validation is counted, never raised."""
    from gefion import cli as cli_module
    from gefion.quality import rules
    with conn.cursor() as cur:
        sid = _seed_stock(cur, "QWP4")

    def boom(*args, **kwargs):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(rules, "check_bounds", boom)
    results = [(sid, "QWP4", _overview("QWP4", Beta="1.1"), None, False)]
    summary = cli_module._write_fundamentals_results(conn, results)
    assert summary["updated"] == 1          # the write landed
    assert summary["write_errors"] == 0
    assert summary["quality_findings_errors"] >= 1


# --- macro ingest ---------------------------------------------------------------------

def test_macro_ingest_validates_against_the_series_stanza(conn):
    """A VIX of -3 stores verbatim and convicts via the same ledger — but the
    catalog stanza is keyed by series name, so a series the catalog doesn't
    know is simply not validated."""
    from gefion.macro import ingest
    from gefion.quality import catalog as qcatalog
    from gefion.quality import findings

    # point the vix stanza at our disposable series name for the test
    cat = qcatalog.load_default()
    cat.metrics["vix"].series = "qwptest_vix"
    rows = [{"date": date(2026, 1, 5), "value": -3.0},
            {"date": date(2026, 1, 6), "value": 16.0}]
    summary = ingest.ingest_series(
        conn, "qwptest_vix", provider="fred:VIXCLS", kind="index",
        cadence="daily", fetch=lambda provider, full: rows,
        quality_catalog=cat)
    assert summary["values_upserted"] == 2
    assert summary["quality_findings"] == 1
    with conn.cursor() as cur:  # verbatim storage
        cur.execute(
            """SELECT count(*) FROM macro_series_values v
               JOIN macro_series s ON s.id = v.series_id
               WHERE s.name = 'qwptest_vix' AND v.value = -3.0""")
        assert cur.fetchone()[0] == 1
    rows_f = findings.list_findings(conn, metric="vix",
                                    entity_table="macro_series")
    assert len(rows_f) == 1
    assert rows_f[0]["verdict"] == "trash"
    assert rows_f[0]["observed"] == -3.0
