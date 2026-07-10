"""First-class regime + discovery-run deletion (issues #75/#76).

TDD: written FIRST. The `data cull` / entity-delete mold: dry-run by default
reporting the full blast radius, `--confirm` to execute in dependency order,
honest exceptions — machine-origin regimes refuse without `--force`, runs with
admissions refuse always, and the discovery ledger is never touched by regime
deletion (removing the artifact must not remove the search accounting).
"""
import os

import psycopg
import pytest
from psycopg.types.json import Json

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


def _cleanup(cur):
    cur.execute("DELETE FROM regime_labels WHERE regime_id IN "
                "(SELECT id FROM regime_definitions WHERE name LIKE 'rdel-%')")
    cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'rdel-%'")
    cur.execute("DELETE FROM experiments WHERE name LIKE 'rdel-%'")
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'rdel-%'")


@pytest.fixture
def conn():
    c = _conn()
    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


_EXPR = {"kind": "comparison", "feature": "x", "cmp": ">", "value": 0.0}
_BUCKETS = {"kind": "threshold", "labels": ["low", "high"]}


def _make_regime(conn, name, origin="human", labels=0, metadata=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO regime_definitions
                   (name, scope, expression, bucketing, origin,
                    descriptive_metadata)
               VALUES (%s, 'market', %s, %s, %s, %s) RETURNING id""",
            (name, Json(_EXPR), Json(_BUCKETS), origin,
             Json(metadata) if metadata else None))
        rid = cur.fetchone()[0]
        for i in range(labels):
            cur.execute(
                """INSERT INTO regime_labels
                       (regime_id, date, entity_id, label, dataset_version)
                   VALUES (%s, DATE '2024-01-01' + %s, 0, 'high', 'dev')""",
                (rid, i))
    return rid


def _make_experiment_with_verdicts(conn, name, regime_name):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO experiments (name, experiment_type, config, results)
               VALUES (%s, 'feature_validation', '{}',
                       %s) RETURNING id""",
            (name, Json({"by_regime": {"regime": regime_name,
                                       "verdicts": [{"bucket": "high",
                                                     "pvalue": 0.2}]}})))
        return cur.fetchone()[0]


def _make_run(conn, name, admitted=0, invalid=False):
    from gefion.regimes.discovery import ledger
    run_id = ledger.create_run(
        conn, name=name, seed=1,
        search_space={"signal_source": "features", "grading_scheme": "walk_forward",
                      "universe_filter": ["passthrough"], "atoms": [],
                      "signals": ["x"], "horizon_days": 1,
                      "label_window": 60, "align_window": 60},
        segregation={"inner_start": "2024-01-01", "inner_end": "2024-06-01",
                     "holdout_start": "2024-06-02", "holdout_end": "2024-09-01"},
        dataset_version="dev")
    cand_ids = ledger.record_candidates(conn, run_id, [
        {"candidate_hash": f"{name}-c{i}", "tier": "interaction",
         "expression": _EXPR, "provenance": {"atom_features": ["x"]}}
        for i in range(max(admitted, 1))])
    ledger.set_status(conn, run_id, "enumerated")
    for i, cid in enumerate(cand_ids):
        ledger.record_result(conn, cid, {"tests": []},
                             "admitted" if i < admitted else "rejected")
    if invalid:
        ledger.set_status(conn, run_id, "invalid")
    else:
        ledger.set_status(conn, run_id, "evaluated")
        ledger.set_status(conn, run_id, "complete")
    ledger.set_family_size(conn, run_id, len(cand_ids))
    return run_id


# --- regime delete (#75) --------------------------------------------------------------

def test_plan_reports_full_blast_radius_changing_nothing(conn):
    from gefion.regimes.deletion import plan_regime_delete
    rid = _make_regime(conn, "rdel-vol", labels=5)
    _make_experiment_with_verdicts(conn, "rdel-exp", "rdel-vol")
    plan = plan_regime_delete(conn, "rdel-vol")
    assert plan["regime"]["id"] == rid
    assert plan["labels"] == 5
    assert len(plan["experiment_references"]) == 1
    assert plan["machine_origin"] is False
    with conn.cursor() as cur:                       # nothing changed
        cur.execute("SELECT count(*) FROM regime_labels WHERE regime_id=%s", (rid,))
        assert cur.fetchone()[0] == 5


def test_execute_deletes_labels_then_definition_keeps_soft_refs(conn):
    from gefion.regimes.deletion import execute_regime_delete
    rid = _make_regime(conn, "rdel-vol", labels=3)
    exp_id = _make_experiment_with_verdicts(conn, "rdel-exp", "rdel-vol")
    result = execute_regime_delete(conn, "rdel-vol")
    assert result["labels_deleted"] == 3
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_definitions WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM regime_labels WHERE regime_id=%s", (rid,))
        assert cur.fetchone()[0] == 0
        # soft name-keyed references are reported, never mutated
        cur.execute("SELECT results->'by_regime'->>'regime' FROM experiments "
                    "WHERE id=%s", (exp_id,))
        assert cur.fetchone()[0] == "rdel-vol"
    assert [e["id"] for e in result["experiment_references"]] == [exp_id]


def test_never_computed_regime_deletes_cleanly(conn):
    from gefion.regimes.deletion import execute_regime_delete
    _make_regime(conn, "rdel-empty", labels=0)
    result = execute_regime_delete(conn, "rdel-empty")
    assert result["labels_deleted"] == 0


def test_machine_origin_refused_without_force_ledger_untouched(conn):
    from gefion.regimes.deletion import RegimeDeleteError, execute_regime_delete
    run_id = _make_run(conn, "rdel-run", admitted=1)
    _make_regime(conn, "rdel-disc", origin="machine", labels=2,
                 metadata={"discovery_run_id": run_id})
    with pytest.raises(RegimeDeleteError) as exc:
        execute_regime_delete(conn, "rdel-disc")
    assert "--force" in str(exc.value)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_definitions WHERE name='rdel-disc'")
        assert cur.fetchone()[0] == 1
    # with force: definition + labels go; the candidate ledger survives intact
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_candidates WHERE run_id=%s",
                    (run_id,))
        ledger_before = cur.fetchone()[0]
    execute_regime_delete(conn, "rdel-disc", force=True)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_definitions WHERE name='rdel-disc'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM regime_candidates WHERE run_id=%s",
                    (run_id,))
        assert cur.fetchone()[0] == ledger_before    # accounting survives

def test_unknown_regime_refuses_honestly(conn):
    from gefion.regimes.deletion import RegimeDeleteError, plan_regime_delete
    with pytest.raises(RegimeDeleteError):
        plan_regime_delete(conn, "rdel-nope")


# --- discover delete (#76) ------------------------------------------------------------

def test_run_with_admissions_refuses_always(conn):
    from gefion.regimes.deletion import RegimeDeleteError, execute_run_delete
    run_id = _make_run(conn, "rdel-admitted", admitted=1)
    with pytest.raises(RegimeDeleteError) as exc:
        execute_run_delete(conn, run_id)
    assert "admitted" in str(exc.value).lower()      # audit trail, no force door


def test_unadmitted_run_deletes_with_cascade(conn):
    from gefion.regimes.deletion import plan_run_delete, execute_run_delete
    from gefion.regimes.discovery.ledger import record_spa_reverdict
    run_id = _make_run(conn, "rdel-noise", admitted=0)
    record_spa_reverdict(conn, run_id, {
        "p_consistent": 0.5, "p_lower": 0.4, "p_upper": 0.6, "level": 0.01,
        "passed": False, "iterations": 100, "seed": 1, "block_length": 2.0,
        "family_size": 1, "verification": {"units": 1,
                                           "max_abs_divergence": 0.0,
                                           "all_match": True}})
    plan = plan_run_delete(conn, run_id)
    assert plan["candidates"] == 1
    assert plan["spa_reverdicts"] == 1
    execute_run_delete(conn, run_id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_discovery_runs WHERE id=%s",
                    (run_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM regime_candidates WHERE run_id=%s",
                    (run_id,))
        assert cur.fetchone()[0] == 0                # DB cascade
        cur.execute("SELECT count(*) FROM spa_reverdicts WHERE run_id=%s",
                    (run_id,))
        assert cur.fetchone()[0] == 0


# --- surfaces ---------------------------------------------------------------------------

def test_cli_commands_exist_with_options():
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, ["regime", "delete", "--help"])
    assert r.exit_code == 0
    for opt in ("--confirm", "--force"):
        assert opt in r.output
    r = CliRunner().invoke(app, ["regime", "discover", "delete", "--help"])
    assert r.exit_code == 0
    assert "--confirm" in r.output


def test_cli_dry_run_by_default(conn):
    from typer.testing import CliRunner
    from gefion.cli import app
    _make_regime(conn, "rdel-dry", labels=4)
    r = CliRunner().invoke(app, ["regime", "delete", "rdel-dry",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0
    assert "4" in r.output                           # labels count reported
    assert "dry-run" in r.output.lower()
    with conn.cursor() as cur:                       # nothing deleted
        cur.execute("SELECT count(*) FROM regime_definitions "
                    "WHERE name='rdel-dry'")
        assert cur.fetchone()[0] == 1


def test_mcp_tools_exist():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    for tool in ("regime_delete", "regime_discover_delete"):
        assert f'name="{tool}"' in server
        assert f'name == "{tool}"' in server
        assert f"async def _{tool}(" in server
