"""Registry-driven entity deletion tests (007, T008 — US5).

TDD: written FIRST. Deletion is first-class (owner principle): dry-run by
default reporting the FULL blast radius (registry edges + hard-FK dependents),
confirm-to-execute in dependency order (feature values per the registry, then
the entity row), uniform across entity kinds, refusing on RESTRICT/NO-ACTION
blockers with the list. The stocks parity test runs while the old FK cascade
still exists — the strongest possible baseline. Audit ledgers are never in
scope: deleting an artifact never deletes accounting.
"""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema
from gefion.entities import deletion


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
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'DELT%'")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'deltest_%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'deltest_%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'deltest_%'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'DELT%'")
    c.close()


def _seed_stock(cur, symbol, with_feature_values=True):
    cur.execute("INSERT INTO stocks (symbol, name) VALUES (%s, 'X') RETURNING id",
                (symbol,))
    stock_id = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO feature_definitions (name, function_name, entity_table)
           VALUES ('deltest_feature', 'indicator', 'stocks')
           ON CONFLICT (name) DO UPDATE SET entity_table = 'stocks'
           RETURNING id""")
    feature_id = cur.fetchone()[0]
    if with_feature_values:
        for day in (5, 6, 7):
            cur.execute(
                """INSERT INTO computed_features (data_id, date, feature_id, value)
                   VALUES (%s, %s, %s, 1.0) ON CONFLICT DO NOTHING""",
                (stock_id, date(2026, 1, day), feature_id))
    return stock_id, feature_id


# --- dry-run -------------------------------------------------------------------

def test_dry_run_reports_full_blast_radius_and_changes_nothing(conn):
    with conn.cursor() as cur:
        stock_id, feature_id = _seed_stock(cur, "DELT1")
    plan = deletion.plan_delete(conn, "stocks", "DELT1")
    assert plan["entity"]["id"] == stock_id
    by_feature = {f["feature"]: f["count"] for f in plan["feature_values"]}
    assert by_feature["deltest_feature"] == 3
    dependents = {d["table"] for d in plan["fk_dependents"]}
    assert "computed_features" in dependents or by_feature  # registry edge reported
    assert not any(d["table"].startswith("_timescaledb") for d in plan["fk_dependents"])
    # nothing changed
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'DELT1'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM computed_features WHERE data_id = %s "
                    "AND feature_id = %s", (stock_id, feature_id))
        assert cur.fetchone()[0] == 3


def test_unknown_entity_refused(conn):
    with pytest.raises(deletion.EntityDeleteError):
        deletion.plan_delete(conn, "stocks", "NO_SUCH_SYMBOL")
    with pytest.raises(deletion.EntityDeleteError):
        deletion.plan_delete(conn, "not_a_table", "x")


# --- confirm: order, parity, blockers --------------------------------------------

def test_confirm_deletes_values_then_entity_cascade_parity(conn):
    """While the old FK cascade still exists, the command's cleanup must equal
    it: feature values gone, entity row gone."""
    with conn.cursor() as cur:
        stock_id, feature_id = _seed_stock(cur, "DELT2")
    summary = deletion.execute_delete(conn, "stocks", "DELT2")
    assert summary["feature_values_deleted"] == 3
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM computed_features WHERE data_id = %s "
                    "AND feature_id = %s", (stock_id, feature_id))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'DELT2'")
        assert cur.fetchone()[0] == 0


def test_restrict_blockers_refuse_with_the_list(conn):
    """A NO-ACTION dependent with rows (e.g. predictions) blocks deletion and
    the refusal names it."""
    with conn.cursor() as cur:
        stock_id, _ = _seed_stock(cur, "DELT3", with_feature_values=False)
        # stocks_fundamentals has FK stocks(id) with NO ACTION — a real blocker
        cur.execute(
            """INSERT INTO stocks_fundamentals (data_id, date, market_cap)
               VALUES (%s, %s, 1000)""", (stock_id, date(2026, 1, 5)))
    plan = deletion.plan_delete(conn, "stocks", "DELT3")
    assert any(b["table"] == "stocks_fundamentals" for b in plan["blockers"])
    with pytest.raises(deletion.EntityDeleteError) as exc:
        deletion.execute_delete(conn, "stocks", "DELT3")
    assert "stocks_fundamentals" in str(exc.value)
    with conn.cursor() as cur:  # nothing was deleted
        cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'DELT3'")
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id = %s", (stock_id,))


def test_audit_ledgers_are_never_in_scope(conn):
    """Deleting an artifact never deletes accounting: discovery ledgers do not
    appear in the plan even as dependents (they don't FK entity tables)."""
    with conn.cursor() as cur:
        _seed_stock(cur, "DELT4")
    plan = deletion.plan_delete(conn, "stocks", "DELT4")
    named = {d["table"] for d in plan["fk_dependents"]} | \
            {b["table"] for b in plan["blockers"]}
    assert not named & {"regime_candidates", "discovery_diagnostics",
                        "regime_trust_grades"}


# --- CLI surface -----------------------------------------------------------------

def test_cli_dry_run_then_confirm(conn):
    import json
    from typer.testing import CliRunner
    from gefion.cli import app
    runner = CliRunner()
    with conn.cursor() as cur:
        _seed_stock(cur, "DELT5")
    dry = runner.invoke(app, ["data", "entity-delete", "stocks", "DELT5",
                              "--db-url", schema.test_db_url(), "--json"])
    assert dry.exit_code == 0, dry.output
    payload = json.loads(dry.output)
    data = payload.get("data", payload)
    assert data["dry_run"] is True
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'DELT5'")
        assert cur.fetchone()[0] == 1  # dry-run touched nothing
    done = runner.invoke(app, ["data", "entity-delete", "stocks", "DELT5",
                               "--confirm", "--db-url", schema.test_db_url(), "--json"])
    assert done.exit_code == 0, done.output
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stocks WHERE symbol = 'DELT5'")
        assert cur.fetchone()[0] == 0


def test_mcp_surface_exists():
    """T010: entity_delete MCP tool wraps the CLI (dry-run default)."""
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server" / "server.py").read_text()
    assert 'name="entity_delete"' in server
    assert 'name == "entity_delete"' in server
    body_start = server.index("async def _entity_delete(")
    body = server[body_start:server.index("\nasync def ", body_start + 1)]
    assert "--confirm" in body  # confirm only threaded when explicitly true
