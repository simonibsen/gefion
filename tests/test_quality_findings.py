"""Findings-ledger tests (008, T006/T008 — US1).

TDD: written FIRST. Part 1 (schema): the approved DDL — verdict CHECK, the
idempotence UNIQUE, both indexes, and DOUBLE PRECISION observed/expected (the
ledger must not overflow on the garbage it convicts — the #79 lesson applied
to ourselves). Part 2 (API): idempotent upserts, filtered listing, survival of
entity deletion (audit exception), supersede-never-erase resolution.
"""
import os
from datetime import date

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
    with c.cursor() as cur:
        cur.execute("DELETE FROM data_quality_findings WHERE context LIKE 'qftest%'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QFT%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM data_quality_findings WHERE context LIKE 'qftest%'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QFT%'")
    c.close()


# --- Part 1: schema (T006) ---------------------------------------------------------

def test_findings_table_shape(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_name = 'data_quality_findings'""")
        cols = dict(cur.fetchall())
    assert cols["observed"] == "double precision"
    assert cols["expected"] == "double precision"
    assert cols["detail"] == "jsonb"
    assert cols["entity_table"] == "text"


def test_findings_verdict_check_and_unique(conn):
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """INSERT INTO data_quality_findings
                       (entity_table, entity_id, metric, date, rule, verdict, context)
                   VALUES ('stocks', 1, 'beta', '2026-01-01', 'definitional_bound',
                           'guilty', 'qftest')""")
        cur.execute(
            """INSERT INTO data_quality_findings
                   (entity_table, entity_id, metric, date, rule, verdict, context)
               VALUES ('stocks', 1, 'beta', '2026-01-01', 'definitional_bound',
                       'trash', 'qftest')""")
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                """INSERT INTO data_quality_findings
                       (entity_table, entity_id, metric, date, rule, verdict, context)
                   VALUES ('stocks', 1, 'beta', '2026-01-01', 'definitional_bound',
                           'trash', 'qftest')""")


def test_findings_hold_unbounded_garbage(conn):
    """DOUBLE PRECISION: the ledger stores what it convicts, however large."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO data_quality_findings
                   (entity_table, entity_id, metric, date, rule, verdict,
                    observed, expected, context)
               VALUES ('stocks', 2, 'beta', '2026-01-01', 'definitional_bound',
                       'trash', -1e15, 50, 'qftest')
               RETURNING observed""")
        assert cur.fetchone()[0] == -1e15


def test_findings_indexes_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT indexname FROM pg_indexes
               WHERE tablename = 'data_quality_findings'""")
        names = {r[0] for r in cur.fetchall()}
    assert "data_quality_findings_metric_verdict_idx" in names
    assert "data_quality_findings_entity_idx" in names


# --- Part 2: ledger API (T008) -------------------------------------------------------

def _result(observed=-503341.44, rule="definitional_bound", verdict="trash",
            expected=-50.0):
    from gefion.quality.rules import RuleResult
    return RuleResult(rule=rule, verdict=verdict, observed=observed,
                      expected=expected, detail={"bounds": [-50, 50]})


def test_record_findings_upserts_idempotently(conn):
    from gefion.quality import findings
    key = dict(entity_table="stocks", entity_id=990001, metric="beta",
               date=date(2026, 7, 8))
    n1 = findings.record_findings(conn, [dict(**key, result=_result())],
                                  context="qftest-run1")
    assert n1 == 1
    # re-validation refreshes (new observed), never duplicates
    n2 = findings.record_findings(conn, [dict(**key, result=_result(observed=-1.0e6))],
                                  context="qftest-run2")
    assert n2 == 1
    rows = findings.list_findings(conn, metric="beta", entity_table="stocks",
                                  entity_id=990001)
    assert len(rows) == 1
    assert rows[0]["observed"] == -1.0e6
    assert rows[0]["context"] == "qftest-run2"


def test_list_findings_filters(conn):
    from gefion.quality import findings
    base = dict(entity_table="stocks", entity_id=990002, date=date(2026, 7, 8))
    findings.record_findings(conn, [
        dict(**base, metric="beta", result=_result()),
        dict(**base, metric="pe_ratio",
             result=_result(rule="cross_sectional_outlier", verdict="suspect")),
    ], context="qftest-filters")
    trash = findings.list_findings(conn, entity_id=990002, verdict="trash")
    assert [r["metric"] for r in trash] == ["beta"]
    both = findings.list_findings(conn, entity_id=990002)
    assert len(both) == 2


def test_findings_survive_entity_delete(conn):
    """Audit exception (007/issue #76): deleting the flagged entity keeps the
    finding."""
    from gefion.entities import deletion
    from gefion.quality import findings
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QFT1', 'X') "
                    "RETURNING id")
        sid = cur.fetchone()[0]
    findings.record_findings(conn, [dict(entity_table="stocks", entity_id=sid,
                                         metric="beta", date=date(2026, 7, 8),
                                         result=_result())],
                             context="qftest-delete")
    deletion.execute_delete(conn, "stocks", "QFT1")
    rows = findings.list_findings(conn, entity_table="stocks", entity_id=sid)
    assert len(rows) == 1  # the accounting outlives the artifact


def test_resolution_supersedes_never_erases(conn):
    from gefion.quality import findings
    findings.record_findings(conn, [dict(entity_table="stocks", entity_id=990003,
                                         metric="beta", date=date(2026, 7, 8),
                                         result=_result())],
                             context="qftest-resolve")
    row = findings.list_findings(conn, entity_id=990003)[0]
    findings.resolve_finding(conn, row["id"], reason="bound widened after review")
    resolved = findings.list_findings(conn, entity_id=990003,
                                      include_resolved=True)[0]
    assert resolved["resolved_at"] is not None
    assert "widened" in resolved["resolution"]
    # default listing hides resolved findings; nothing was deleted
    assert findings.list_findings(conn, entity_id=990003) == []
    with pytest.raises(findings.FindingError):
        findings.resolve_finding(conn, row["id"], reason="")  # reason required
