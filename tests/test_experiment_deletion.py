"""Experiment deletion door (#76 audit).

TDD: written FIRST. Policy: dry-run default + --confirm; trials cascade
(existing FK); PROMOTED experiments refuse ALWAYS (no --force — they
influenced production, same class as admitted discovery runs);
regime_discovery experiments refuse and point to their own guarded door;
experimental features owned by the experiment cascade, promoted features
refuse; the cycle row is a soft reference (reported, never mutated).
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

    def _cleanup(cur):
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'exp_qed_%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'exp_qed_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'exp_qed_%'")
        cur.execute("DELETE FROM experiment_trials WHERE experiment_id IN "
                    "(SELECT id FROM experiments WHERE name LIKE 'qed_%')")
        cur.execute("UPDATE experiments SET parent_experiment_id = NULL "
                    "WHERE name LIKE 'qed_%'")
        cur.execute("DELETE FROM experiments WHERE name LIKE 'qed_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _make_experiment(conn, name="qed_one", exp_type="hyperparameter",
                     trials=2, promoted=False, feature=None,
                     feature_promoted=False):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO experiments (name, experiment_type, status,
                   config, promoted_at)
               VALUES (%s, %s, 'completed', '{}'::jsonb, %s) RETURNING id""",
            (name, exp_type, date(2026, 7, 1) if promoted else None))
        eid = cur.fetchone()[0]
        for i in range(trials):
            cur.execute(
                "INSERT INTO experiment_trials (experiment_id, trial_number, "
                "params, metrics) VALUES (%s, %s, '{}'::jsonb, '{}'::jsonb)",
                (eid, i))
        if feature:
            cur.execute(
                """INSERT INTO feature_functions (name, version, status,
                       enabled, language, function_body)
                   VALUES (%s, 'v1', 'experimental', TRUE, 'python', '# x')""",
                (feature,))
            cur.execute(
                """INSERT INTO feature_definitions (name, function_name,
                       source_table, source_column, active, is_experimental,
                       source_experiment_id, promoted_at)
                   VALUES (%s, %s, 'stock_ohlcv', 'close', FALSE, %s, %s, %s)""",
                (feature, feature, not feature_promoted, eid,
                 date(2026, 7, 1) if feature_promoted else None))
    return eid


class TestPlanAndExecute:
    def test_plan_reports_blast_radius_changing_nothing(self, conn):
        from gefion.experiments import deletion
        eid = _make_experiment(conn, trials=2, feature="exp_qed_feat")
        plan = deletion.plan_experiment_delete(conn, eid)
        assert plan["experiment"]["name"] == "qed_one"
        assert plan["trials"] == 2
        assert plan["experimental_features"] == ["exp_qed_feat"]
        assert plan["promoted"] is False
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM experiment_trials "
                        "WHERE experiment_id = %s", (eid,))
            assert cur.fetchone()[0] == 2

    def test_execute_cascades_trials_and_experimental_features(self, conn):
        from gefion.experiments import deletion
        eid = _make_experiment(conn, trials=2, feature="exp_qed_feat")
        deletion.execute_experiment_delete(conn, eid)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM experiments WHERE id = %s", (eid,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM experiment_trials "
                        "WHERE experiment_id = %s", (eid,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM feature_definitions "
                        "WHERE name = 'exp_qed_feat'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM feature_functions "
                        "WHERE name = 'exp_qed_feat'")
            assert cur.fetchone()[0] == 0

    def test_promoted_experiment_refuses_always(self, conn):
        """Production influence is an audit fact — deliberately no --force."""
        from gefion.experiments import deletion
        eid = _make_experiment(conn, name="qed_promoted", promoted=True)
        with pytest.raises(ValueError, match="promoted"):
            deletion.execute_experiment_delete(conn, eid)
        import inspect
        assert "force" not in inspect.signature(
            deletion.execute_experiment_delete).parameters

    def test_promoted_feature_refuses(self, conn):
        from gefion.experiments import deletion
        eid = _make_experiment(conn, name="qed_pf", feature="exp_qed_pf",
                               feature_promoted=True)
        with pytest.raises(ValueError, match="promoted"):
            deletion.execute_experiment_delete(conn, eid)

    def test_regime_discovery_type_points_to_its_own_door(self, conn):
        from gefion.experiments import deletion
        eid = _make_experiment(conn, name="qed_disc",
                               exp_type="regime_discovery")
        with pytest.raises(ValueError, match="regime discover delete"):
            deletion.execute_experiment_delete(conn, eid)

    def test_child_experiments_block(self, conn):
        from gefion.experiments import deletion
        parent = _make_experiment(conn, name="qed_parent")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO experiments (name, experiment_type, status,
                       config, parent_experiment_id)
                   VALUES ('qed_child', 'hyperparameter', 'proposed',
                           '{}'::jsonb, %s)""",
                (parent,))
        with pytest.raises(ValueError, match="child"):
            deletion.execute_experiment_delete(conn, parent)

    def test_unknown_experiment_refuses(self, conn):
        from gefion.experiments import deletion
        with pytest.raises(ValueError, match="999999"):
            deletion.plan_experiment_delete(conn, 999999)


class TestSurfaces:
    def test_cli_command_exists_dry_run_default(self):
        from typer.testing import CliRunner
        from gefion.cli import app
        r = CliRunner().invoke(app, ["experiment", "delete", "--help"])
        assert r.exit_code == 0
        assert "--confirm" in r.output

    def test_mcp_tool_exists(self):
        from pathlib import Path
        import gefion
        server = (Path(gefion.__file__).parent.parent.parent /
                  "mcp-server" / "server.py").read_text()
        assert 'name="experiment_delete"' in server
        assert 'name == "experiment_delete"' in server
