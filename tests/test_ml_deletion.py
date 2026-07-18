"""Per-model ML artifact deletion (#76 audit door).

TDD: written FIRST. Policy (DEVELOPMENT.md § Deletion policy): dry-run by
default reporting the full blast radius; --confirm to execute; dependency
order (owned artifacts before the model row); active models are a --force
gate; training runs/datasets are reusable INPUTS and are never deleted;
audit ledgers (discovery pre-registrations naming the model) are reported,
never mutated.
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
                    "(SELECT id FROM feature_definitions WHERE name LIKE '%qmd_model%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE '%qmd_model%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE '%qmd_model%'")
        cur.execute("DELETE FROM model_performance WHERE model_id IN "
                    "(SELECT id FROM ml_models WHERE name LIKE 'qmd_model%')")
        cur.execute("DELETE FROM prediction_outcomes WHERE model_id IN "
                    "(SELECT id FROM ml_models WHERE name LIKE 'qmd_model%')")
        cur.execute("DELETE FROM predictions WHERE model_id IN "
                    "(SELECT id FROM ml_models WHERE name LIKE 'qmd_model%')")
        cur.execute("DELETE FROM ml_models WHERE name LIKE 'qmd_model%'")
        cur.execute("DELETE FROM stocks WHERE symbol = 'QMD1'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _make_model(conn, name="qmd_model", version="v1", active=False,
                predictions=3, signals=False):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QMD1', 'X') "
                    "ON CONFLICT (symbol) DO UPDATE SET name = 'X' RETURNING id")
        sid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ml_models (name, version, algorithm, active, "
            "artifact_uri) VALUES (%s, %s, 'lightgbm', %s, 'memory://test') "
            "RETURNING id",
            (name, version, active))
        mid = cur.fetchone()[0]
        for i in range(predictions):
            cur.execute(
                """INSERT INTO predictions (model_id, data_id, prediction_date,
                       horizon_days, prediction_type, prediction_values)
                   VALUES (%s, %s, %s, 30, 'quantile', '{}'::jsonb)""",
                (mid, sid, date(2026, 1, 5 + i)))
        cur.execute(
            """INSERT INTO prediction_outcomes (data_id, model_id,
                   prediction_date, outcome_date, horizon_days, actual_return)
               VALUES (%s, %s, %s, %s, 30, 0.01)""",
            (sid, mid, date(2026, 1, 5), date(2026, 2, 4)))
        cur.execute(
            """INSERT INTO model_performance (model_id, model_name,
                   horizon_days) VALUES (%s, %s, 30)""", (mid, name))
        if signals:
            feat = f"pred_q50_h30__{name}_{version}"
            cur.execute(
                """INSERT INTO feature_functions (name, version, status,
                       enabled, language, function_body, scope)
                   VALUES (%s, 'v1', 'active', TRUE, 'python',
                           '# materialized', 'materialized')""", (feat,))
            cur.execute(
                """INSERT INTO feature_definitions (name, function_name,
                       source_table, source_column, active, entity_table)
                   VALUES (%s, %s, 'stock_ohlcv', 'close', TRUE, 'stocks')
                   RETURNING id""", (feat, feat))
            fid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO computed_features (data_id, date, feature_id, value) "
                "VALUES (%s, %s, %s, 0.5)", (sid, date(2026, 1, 5), fid))
    return mid


class TestPlan:
    def test_plan_reports_blast_radius_changing_nothing(self, conn):
        from gefion.ml import deletion
        _make_model(conn, predictions=3, signals=True)
        plan = deletion.plan_model_delete(conn, "qmd_model", "v1")
        assert plan["model"]["name"] == "qmd_model"
        assert plan["predictions"] == 3
        assert plan["prediction_outcomes"] == 1
        assert plan["model_performance"] == 1
        assert plan["materialized_signals"] == ["pred_q50_h30__qmd_model_v1"]
        assert plan["active"] is False
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM predictions WHERE model_id = "
                        "(SELECT id FROM ml_models WHERE name='qmd_model')")
            assert cur.fetchone()[0] == 3   # dry-run changed nothing

    def test_unknown_model_refuses(self, conn):
        from gefion.ml import deletion
        with pytest.raises(ValueError, match="qmd_model_missing"):
            deletion.plan_model_delete(conn, "qmd_model_missing", "v9")


class TestExecute:
    def test_execute_cascades_owned_artifacts_in_order(self, conn):
        from gefion.ml import deletion
        _make_model(conn, predictions=3, signals=True)
        result = deletion.execute_model_delete(conn, "qmd_model", "v1")
        assert result["predictions"] == 3
        assert result["materialized_signals"] == 1
        with conn.cursor() as cur:
            for table in ("ml_models", "predictions", "prediction_outcomes",
                          "model_performance"):
                cur.execute(
                    f"SELECT count(*) FROM {table} " +
                    ("WHERE name = 'qmd_model'" if table == "ml_models" else
                     "WHERE model_id IN (SELECT id FROM ml_models "
                     "WHERE name='qmd_model')"))
                assert cur.fetchone()[0] == 0, table
            # materialized signal family fully removed (values, def, function)
            cur.execute("SELECT count(*) FROM feature_definitions "
                        "WHERE name LIKE '%qmd_model%'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM feature_functions "
                        "WHERE name LIKE '%qmd_model%'")
            assert cur.fetchone()[0] == 0

    def test_active_model_refuses_without_force(self, conn):
        from gefion.ml import deletion
        _make_model(conn, active=True)
        with pytest.raises(ValueError, match="active"):
            deletion.execute_model_delete(conn, "qmd_model", "v1")
        # --force opens the gate
        deletion.execute_model_delete(conn, "qmd_model", "v1", force=True)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ml_models WHERE name='qmd_model'")
            assert cur.fetchone()[0] == 0

    def test_training_inputs_never_deleted(self, conn):
        """ml_runs/ml_datasets are reusable inputs, not owned artifacts."""
        from gefion.ml import deletion
        import inspect
        source = inspect.getsource(deletion)
        assert "DELETE FROM ml_runs" not in source
        assert "DELETE FROM ml_datasets" not in source


class TestSurfaces:
    def test_cli_command_exists_dry_run_default(self):
        from typer.testing import CliRunner
        from gefion.cli import app
        r = CliRunner().invoke(app, ["ml", "delete-model", "--help"])
        assert r.exit_code == 0
        assert "--confirm" in r.output and "--force" in r.output

    def test_mcp_tool_exists(self):
        from pathlib import Path
        import gefion
        server = (Path(gefion.__file__).parent.parent.parent /
                  "mcp-server" / "server.py").read_text()
        assert 'name="ml_delete_model"' in server
        assert 'name == "ml_delete_model"' in server
