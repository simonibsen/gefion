"""Quality surfaces + backfill tests (008, T018 — US4).

TDD: written FIRST. db-health gains a data_quality section (per-metric counts
by verdict, loud on trash); the `gefion quality` group lists/inspects/resolves
findings and the catalog; the backfill flags already-stored history
idempotently while changing ZERO stored values (SC-305).
"""
import json
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
        cur.execute("DELETE FROM data_quality_findings WHERE context LIKE 'qstest%' "
                    "OR context LIKE 'quality backfill%' OR context = 'fundamentals-update'")
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QST%')")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QST%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QST%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _seed_stock_with_garbage(cur, symbol, beta):
    cur.execute("INSERT INTO stocks (symbol, name) VALUES (%s, 'X') RETURNING id",
                (symbol,))
    sid = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO stocks_fundamentals (data_id, date, beta)
           VALUES (%s, %s, %s)""", (sid, date(2026, 7, 8), beta))
    return sid


# --- backfill (SC-305: idempotent, value-preserving) --------------------------------

def test_backfill_flags_stored_garbage_without_touching_values(conn):
    from gefion.quality import backfill
    with conn.cursor() as cur:
        sid = _seed_stock_with_garbage(cur, "QST1", -503341.44)
        cur.execute("SELECT md5(CAST((SELECT array_agg(f.* ORDER BY f.data_id) "
                    "FROM stocks_fundamentals f WHERE f.data_id = %s) AS text))",
                    (sid,))
        before = cur.fetchone()[0]
    summary = backfill.run(conn, entity_table="stocks")
    assert summary["findings"]["created"] >= 1
    from gefion.quality import findings
    rows = findings.list_findings(conn, entity_id=sid, metric="beta")
    assert rows and rows[0]["verdict"] == "trash"
    # SC-305: not a single stored value changed
    with conn.cursor() as cur:
        cur.execute("SELECT md5(CAST((SELECT array_agg(f.* ORDER BY f.data_id) "
                    "FROM stocks_fundamentals f WHERE f.data_id = %s) AS text))",
                    (sid,))
        after = cur.fetchone()[0]
    assert before == after
    # idempotent: a second run creates no new findings
    summary2 = backfill.run(conn, entity_table="stocks")
    assert summary2["findings"]["created"] == 0


# --- db-health data_quality section -------------------------------------------------

def test_db_health_data_quality_section(conn):
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.quality import backfill
    with conn.cursor() as cur:
        _seed_stock_with_garbage(cur, "QST2", -503341.44)
    backfill.run(conn, entity_table="stocks")
    result = CliRunner().invoke(app, ["db-health", "--db-url", schema.test_db_url(),
                                      "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    dq = payload["data_quality"]
    assert dq["beta"]["trash"] >= 1
    warnings = " ".join(payload["warnings"])
    assert "trash" in warnings.lower() or "quality" in warnings.lower()


# --- CLI quality group ---------------------------------------------------------------

def test_cli_quality_findings_and_catalog(conn):
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.quality import backfill
    with conn.cursor() as cur:
        _seed_stock_with_garbage(cur, "QST3", -503341.44)
    backfill.run(conn, entity_table="stocks")
    runner = CliRunner()
    f = runner.invoke(app, ["quality", "findings", "--metric", "beta",
                            "--db-url", schema.test_db_url(), "--json"])
    assert f.exit_code == 0, f.output
    data = json.loads(f.output)
    assert any(r["metric"] == "beta" and r["verdict"] == "trash"
               for r in data["findings"])
    c = runner.invoke(app, ["quality", "catalog", "--db-url", schema.test_db_url(),
                            "--json"])
    assert c.exit_code == 0, c.output
    assert "beta" in json.loads(c.output)["covered"]


def test_cli_quality_resolve_requires_reason(conn):
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.quality import backfill, findings
    with conn.cursor() as cur:
        sid = _seed_stock_with_garbage(cur, "QST4", -503341.44)
    backfill.run(conn, entity_table="stocks")
    fid = findings.list_findings(conn, entity_table="stocks", entity_id=sid)[0]["id"]
    runner = CliRunner()
    bad = runner.invoke(app, ["quality", "resolve", str(fid),
                              "--db-url", schema.test_db_url()])
    assert bad.exit_code != 0  # reason required
    good = runner.invoke(app, ["quality", "resolve", str(fid), "--reason",
                               "reviewed", "--db-url", schema.test_db_url()])
    assert good.exit_code == 0, good.output
    assert findings.list_findings(conn, entity_table="stocks",
                                  entity_id=sid) == []  # resolved


def test_mcp_quality_surface_exists():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    for tool in ("quality_findings", "quality_catalog", "quality_backfill",
                 "quality_resolve"):
        assert f'name="{tool}"' in server
        assert f'name == "{tool}"' in server
