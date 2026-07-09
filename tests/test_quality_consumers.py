"""Consumer-exclusion tests (008, T012 — US2).

TDD: written FIRST. Convicted (trash) values are kept out of the feature
store at the computation chokepoint — so every current and future consumer is
protected in one place (no distributed vigilance). Design note: stocks_
fundamentals is consumer-less today (007 feeds graph), so the live path is
macro materialization; the dispatcher generic-table exclusion future-proofs
fundamentals features. Resolved findings and suspect-tier findings never
exclude.
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
        cur.execute("DELETE FROM data_quality_findings WHERE context LIKE 'qctest%'")
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_qctest%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_qctest%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qctest%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _seed_finding(cur, entity_table, entity_id, metric, d, verdict="trash",
                  resolved=False):
    cur.execute(
        """INSERT INTO data_quality_findings
               (entity_table, entity_id, metric, date, rule, verdict,
                observed, context)
           VALUES (%s, %s, %s, %s, 'definitional_bound', %s, -999, 'qctest')
           ON CONFLICT DO NOTHING""",
        (entity_table, entity_id, metric, d, verdict),
    )
    if resolved:
        cur.execute(
            """UPDATE data_quality_findings SET resolved_at = NOW(),
                   resolution = 'x'
               WHERE entity_table=%s AND entity_id=%s AND metric=%s AND date=%s""",
            (entity_table, entity_id, metric, d))


# --- the reusable helper -----------------------------------------------------------

def test_convicted_dates_maps_metric_to_column(conn):
    from gefion.quality import exclusions
    d = date(2026, 7, 8)
    with conn.cursor() as cur:
        _seed_finding(cur, "stocks", 700001, "beta", d)
    dates = exclusions.convicted_dates(conn, "stocks_fundamentals", 700001, "beta")
    assert d in dates
    # a different column is unaffected
    assert exclusions.convicted_dates(conn, "stocks_fundamentals", 700001,
                                      "pe_ratio") == set()


def test_resolved_and_suspect_never_exclude(conn):
    from gefion.quality import exclusions
    d = date(2026, 7, 8)
    with conn.cursor() as cur:
        _seed_finding(cur, "stocks", 700002, "beta", d, verdict="suspect")
        _seed_finding(cur, "stocks", 700002, "pe_ratio", d, resolved=True)
    assert exclusions.convicted_dates(conn, "stocks_fundamentals", 700002,
                                      "beta") == set()
    assert exclusions.convicted_dates(conn, "stocks_fundamentals", 700002,
                                      "pe_ratio") == set()


# --- the live path: macro materialization -------------------------------------------

def _seed_macro(conn, name, values):
    from gefion.macro import catalog, ingest
    sid = catalog.ensure_series(conn, name, provider="fred:VIXCLS", kind="index",
                                cadence="daily")
    ingest.upsert_values(conn, sid, values)
    return sid


def test_macro_materialize_excludes_convicted_by_default(conn):
    """A VIX <= 0 is convicted at ingest; materialization must not carry it
    into computed_features by default."""
    from gefion.macro import ingest
    from gefion.quality import catalog as qcatalog
    cat = qcatalog.load_default()
    cat.metrics["vix"].series = "qctest_vix"
    vals = [{"date": date(2026, 1, 5), "value": -3.0},
            {"date": date(2026, 1, 6), "value": 16.0},
            {"date": date(2026, 1, 7), "value": 17.0}]
    summary = ingest.ingest_series(
        conn, "qctest_vix", provider="fred:VIXCLS", kind="index",
        cadence="daily", fetch=lambda p, f: vals, quality_catalog=cat)
    assert summary["quality_findings"] == 1  # the -3.0 convicted
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*), min(value) FROM computed_features cf
               JOIN feature_definitions fd ON fd.id = cf.feature_id
               WHERE fd.name = 'macro_qctest_vix'""")
        count, minval = cur.fetchone()
    assert count == 2                      # the convicted -3.0 is excluded
    assert float(minval) == 16.0
    # but the raw value is still stored verbatim (never mutated)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM macro_series_values v
               JOIN macro_series s ON s.id = v.series_id
               WHERE s.name = 'qctest_vix' AND v.value = -3.0""")
        assert cur.fetchone()[0] == 1


def test_macro_ingest_include_flagged_surface_wired():
    """T014: the opt-in reaches CLI and MCP."""
    import pathlib
    from typer.testing import CliRunner
    from gefion.cli import app
    result = CliRunner().invoke(app, ["macro", "ingest", "--help"])
    assert result.exit_code == 0
    assert "--include-flagged" in result.output
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    assert "include_flagged" in server
    assert "--include-flagged" in server  # threaded in the handler


def test_macro_materialize_include_flagged_opt_in(conn):
    from gefion.macro import ingest
    from gefion.quality import catalog as qcatalog
    cat = qcatalog.load_default()
    cat.metrics["vix"].series = "qctest_vix2"
    vals = [{"date": date(2026, 1, 5), "value": -3.0},
            {"date": date(2026, 1, 6), "value": 16.0}]
    ingest.ingest_series(
        conn, "qctest_vix2", provider="fred:VIXCLS", kind="index",
        cadence="daily", fetch=lambda p, f: vals, quality_catalog=cat,
        include_flagged=True)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM computed_features cf
               JOIN feature_definitions fd ON fd.id = cf.feature_id
               WHERE fd.name = 'macro_qctest_vix2'""")
        assert cur.fetchone()[0] == 2      # convicted value included on opt-in


# --- future-proofing: dispatcher generic-table fetch --------------------------------

def test_dispatcher_generic_fetch_drops_convicted_dates(conn):
    """A feature reading a validated (table, column) skips convicted source
    values — the future-proofing for fundamentals features."""
    from gefion.features import dispatcher
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol = 'QCTF1'")
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QCTF1', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
        d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
        for d, beta in ((d1, -503341.44), (d2, 1.1)):
            cur.execute(
                """INSERT INTO stocks_fundamentals (data_id, date, beta)
                   VALUES (%s, %s, %s)""", (sid, d, beta))
        _seed_finding(cur, "stocks", sid, "beta", d1)  # d1 convicted
    try:
        rows = dispatcher._fetch_from_generic_table(
            conn, sid, "stocks_fundamentals", "beta", start_date=None)
        got = {r["date"]: r["value"] for r in rows}
        assert d1 not in got               # convicted date dropped
        assert float(got[d2]) == 1.1
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stocks_fundamentals WHERE data_id = %s", (sid,))
            cur.execute("DELETE FROM data_quality_findings WHERE entity_id = %s "
                        "AND context = 'qctest'", (sid,))
            cur.execute("DELETE FROM stocks WHERE id = %s", (sid,))
