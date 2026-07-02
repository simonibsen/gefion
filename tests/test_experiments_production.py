"""Tests for applying experiment winners to production.

The apply flow takes a promoted experiment through:
dataset rebuild -> retrain -> predict -> backtest, recording artifacts.

TDD: Tests written first, before implementation.
"""
import json
import os
import pathlib
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def _make_experiment(**overrides):
    """A loaded-experiment dict as production._load_experiment returns it."""
    exp = {
        "id": 42,
        "name": "vol-asym-feature",
        "experiment_type": "feature_engineering",
        "status": "completed",
        "cycle_id": 7,
        "fdr_survived": True,
        "promoted_at": "2026-06-01T00:00:00+00:00",
        "config": {
            "algorithm": "lightgbm",
            "horizon_days": 7,
            "quantiles": [0.1, 0.5, 0.9],
            "dataset_uri": "datasets/nasdaq_cycle-7/manifest.json",
            "feature_config": {"function_name": "vol_asym", "function_body": "def compute(df): ..."},
        },
        "results": {"best_params": {"learning_rate": 0.05, "n_estimators": 200}},
        "baseline_value": None,
        "objective_metric": "quantile_loss",
    }
    exp.update(overrides)
    return exp


def _manifest():
    return {
        "name": "nasdaq",
        "format": "parquet",
        "universe": {"exchange": "NASDAQ", "limit": 50},
        "horizons": [7],
    }


class TestApplyValidation:
    """apply_experiment must refuse experiments that aren't proven winners."""

    def test_rejects_incomplete_experiment(self):
        from gefion.experiments.production import apply_experiment, ApplyError

        exp = _make_experiment(status="running")
        with patch("gefion.experiments.production._load_experiment", return_value=exp):
            with pytest.raises(ApplyError, match="completed"):
                apply_experiment(42)

    def test_rejects_fdr_failed_cycle_experiment(self):
        from gefion.experiments.production import apply_experiment, ApplyError

        exp = _make_experiment(fdr_survived=False, promoted_at=None)
        with patch("gefion.experiments.production._load_experiment", return_value=exp):
            with pytest.raises(ApplyError, match="FDR"):
                apply_experiment(42)

    def test_rejects_unknown_experiment(self):
        from gefion.experiments.production import apply_experiment, ApplyError

        with patch("gefion.experiments.production._load_experiment", return_value=None):
            with pytest.raises(ApplyError, match="not found"):
                apply_experiment(999)

    def test_rejects_unsupported_type(self):
        from gefion.experiments.production import apply_experiment, ApplyError

        exp = _make_experiment(experiment_type="label_engineering")
        with patch("gefion.experiments.production._load_experiment", return_value=exp):
            with pytest.raises(ApplyError, match="type"):
                apply_experiment(42)

    def test_allows_standalone_completed_experiment(self):
        """Manual experiments (no cycle) have no FDR gate; completed is enough."""
        from gefion.experiments.production import apply_experiment

        exp = _make_experiment(cycle_id=None, fdr_survived=None, promoted_at=None,
                               experiment_type="hyperparameter")
        with patch("gefion.experiments.production._load_experiment", return_value=exp), \
             patch("gefion.experiments.production._load_manifest", return_value=_manifest()), \
             patch("gefion.experiments.production._max_price_date", return_value=date(2026, 4, 2)), \
             patch("gefion.experiments.production._db_conn"), \
             patch("gefion.experiments.production._run_cli", return_value={"status": "ok"}) as run_cli, \
             patch("gefion.experiments.production._record_artifacts"):
            result = apply_experiment(42)

        assert result["status"] == "ok"
        assert run_cli.called


class TestApplyOrchestration:
    """apply_experiment must drive the pipeline stages in order."""

    def _apply(self, exp=None, run_cli_side_effect=None):
        from gefion.experiments.production import apply_experiment

        exp = exp or _make_experiment()
        events = []
        run_cli = MagicMock(side_effect=run_cli_side_effect,
                            return_value={"status": "ok"})
        if run_cli_side_effect is None:
            run_cli.side_effect = None
        with patch("gefion.experiments.production._load_experiment", return_value=exp), \
             patch("gefion.experiments.production._load_manifest", return_value=_manifest()), \
             patch("gefion.experiments.production._max_price_date", return_value=date(2026, 4, 2)), \
             patch("gefion.experiments.production._db_conn"), \
             patch("gefion.experiments.production._run_cli", run_cli), \
             patch("gefion.experiments.production._record_artifacts") as record:
            result = apply_experiment(
                42, on_progress=lambda phase, msg, detail=None: events.append(phase)
            )
        return result, run_cli, events, record

    def test_stages_run_in_pipeline_order(self):
        """dataset-build -> train -> predict -> backtest, in that order."""
        result, run_cli, _, _ = self._apply()

        stage_cmds = [" ".join(call.args[0]) for call in run_cli.call_args_list]
        dataset_idx = next(i for i, c in enumerate(stage_cmds) if "dataset-build" in c)
        train_idx = next(i for i, c in enumerate(stage_cmds) if " train " in f" {c} ")
        predict_idx = next(i for i, c in enumerate(stage_cmds) if "predict" in c)
        backtest_idx = next(i for i, c in enumerate(stage_cmds) if "backtest" in c)
        assert dataset_idx < train_idx < predict_idx < backtest_idx

    def test_hyperparameter_apply_skips_dataset_rebuild(self):
        """No new features means the experiment's dataset can be reused."""
        exp = _make_experiment(experiment_type="hyperparameter", config={
            "algorithm": "lightgbm",
            "horizon_days": 7,
            "dataset_uri": "datasets/nasdaq_v1/manifest.json",
        })
        result, run_cli, _, _ = self._apply(exp=exp)

        stage_cmds = [" ".join(call.args[0]) for call in run_cli.call_args_list]
        assert not any("dataset-build" in c for c in stage_cmds)

    def test_best_params_passed_to_training(self):
        """Winner hyperparameters must reach the retrain call."""
        result, run_cli, _, _ = self._apply()

        train_cmd = next(
            " ".join(call.args[0]) for call in run_cli.call_args_list
            if " train " in f" {' '.join(call.args[0])} "
        )
        assert "--learning-rate" in train_cmd
        assert "0.05" in train_cmd
        assert "--n-estimators" in train_cmd
        assert "200" in train_cmd

    def test_backtest_uses_ml_signal_with_new_model(self):
        result, run_cli, _, _ = self._apply()

        backtest_cmd = next(
            " ".join(call.args[0]) for call in run_cli.call_args_list
            if "backtest" in " ".join(call.args[0])
        )
        assert "ml_signal" in backtest_cmd
        assert "--model-name" in backtest_cmd

    def test_emits_progress_per_stage(self):
        """on_progress must receive each pipeline phase."""
        _, _, events, _ = self._apply()

        for phase in ("validate", "dataset", "train", "predict", "backtest", "complete"):
            assert phase in events, f"missing progress phase: {phase}"

    def test_records_artifacts_on_success(self):
        result, _, _, record = self._apply()

        assert record.called
        artifacts = record.call_args.args[-1]
        assert "model_name" in artifacts
        assert "model_version" in artifacts
        assert "backtest" in artifacts

    def test_halts_on_stage_failure_with_partial_results(self):
        """A failing train stage must stop the flow before predict/backtest."""
        from gefion.experiments.production import ApplyError

        def fail_on_train(cmd, **kwargs):
            if "train" in cmd:
                raise ApplyError("train blew up")
            return {"status": "ok"}

        with pytest.raises(ApplyError, match="train blew up"):
            self._apply(run_cli_side_effect=fail_on_train)

    def test_result_includes_backtest_metrics(self):
        def with_metrics(cmd, **kwargs):
            if "backtest" in cmd:
                return {"status": "ok", "metrics": {"sharpe_ratio": 1.4, "max_drawdown": -0.12}}
            return {"status": "ok"}

        result, _, _, _ = self._apply(run_cli_side_effect=with_metrics)
        assert result["backtest"]["metrics"]["sharpe_ratio"] == pytest.approx(1.4)


@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled")
class TestApplyDatabase:
    """_load_experiment and _record_artifacts against the test database."""

    @pytest.fixture
    def conn(self):
        import psycopg
        from gefion.db import schema

        try:
            connection = psycopg.connect(schema.test_db_url())
        except psycopg.OperationalError:
            pytest.skip("DB not available")
        connection.autocommit = True
        yield connection
        connection.close()

    @pytest.fixture
    def db_url(self):
        from gefion.db import schema
        return schema.test_db_url()

    @pytest.fixture
    def promoted_experiment(self, conn):
        from psycopg.types.json import Json

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiment_cycles (name, holdout_start_date, holdout_end_date)
                VALUES ('apply-test-cycle', %s, %s) RETURNING id
                """,
                (date.today() - timedelta(days=42), date.today()),
            )
            cycle_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO experiments
                    (name, experiment_type, config, results, status, cycle_id,
                     fdr_survived, promoted_at)
                VALUES ('apply-test-exp', 'feature_engineering', %s, %s,
                        'completed', %s, TRUE, NOW())
                RETURNING id
                """,
                (Json({"algorithm": "lightgbm", "horizon_days": 7}),
                 Json({"best_params": {"max_depth": 4}}),
                 cycle_id),
            )
            exp_id = cur.fetchone()[0]
        yield exp_id
        with conn.cursor() as cur:
            cur.execute("DELETE FROM experiments WHERE id = %s", (exp_id,))
            cur.execute("DELETE FROM experiment_cycles WHERE id = %s", (cycle_id,))

    def test_load_experiment_returns_fields(self, promoted_experiment, db_url):
        from gefion.experiments.production import _load_experiment

        exp = _load_experiment(promoted_experiment, db_url=db_url)

        assert exp["experiment_type"] == "feature_engineering"
        assert exp["status"] == "completed"
        assert exp["fdr_survived"] is True
        assert exp["results"]["best_params"] == {"max_depth": 4}

    def test_load_unknown_experiment_returns_none(self, db_url):
        from gefion.experiments.production import _load_experiment

        assert _load_experiment(-1, db_url=db_url) is None

    def test_record_artifacts_stores_applied_and_probation(self, conn, promoted_experiment, db_url):
        from gefion.experiments.production import _record_artifacts

        artifacts = {"model_name": "exp42_lightgbm", "model_version": "applied-20260702",
                     "backtest": {"metrics": {"sharpe_ratio": 1.1}}}
        _record_artifacts(promoted_experiment, artifacts, db_url=db_url)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT results->'applied', probation_until FROM experiments WHERE id = %s",
                (promoted_experiment,),
            )
            applied, probation_until = cur.fetchone()

        assert applied["model_name"] == "exp42_lightgbm"
        assert probation_until is not None


class TestApplyCLI:
    """`gefion experiment apply` command."""

    def test_command_exists(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["experiment", "apply", "--help"])
        assert result.exit_code == 0

    def test_invokes_apply_and_reports(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        with patch("gefion.experiments.production.apply_experiment",
                   return_value={"status": "ok", "model_name": "m", "model_version": "v",
                                 "backtest": {"metrics": {}}}) as apply_fn:
            result = CliRunner().invoke(app, ["experiment", "apply", "--id", "42", "--json"])

        assert apply_fn.called
        assert result.exit_code == 0

    def test_apply_error_exits_nonzero(self):
        from typer.testing import CliRunner
        from gefion.cli import app
        from gefion.experiments.production import ApplyError

        with patch("gefion.experiments.production.apply_experiment",
                   side_effect=ApplyError("not promoted")):
            result = CliRunner().invoke(app, ["experiment", "apply", "--id", "42", "--json"])

        assert result.exit_code != 0


class TestApplyMCP:
    """experiment_apply MCP tool."""

    def test_tool_definition_exists(self):
        src = pathlib.Path("mcp-server/server.py").read_text()
        assert 'name="experiment_apply"' in src

    def test_handler_dispatch(self):
        src = pathlib.Path("mcp-server/server.py").read_text()
        assert 'name == "experiment_apply"' in src

    def test_handler_uses_cli(self):
        src = pathlib.Path("mcp-server/server.py").read_text()
        assert '"experiment", "apply"' in src


class TestApplyUI:
    """Apply-to-production must be one click in the Experiments view."""

    @pytest.fixture
    def view_source(self):
        path = pathlib.Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views" / "experiments.py"
        return path.read_text()

    def test_apply_button_exists(self, view_source):
        assert "Apply to Production" in view_source

    def test_apply_streams_progress(self, view_source):
        """The button must stream stage progress like the cycle launcher does."""
        assert "experiment_apply_stream" in view_source or (
            '"experiment", "apply"' in view_source and "st.status" in view_source
        )

    def test_manual_command_walkthrough_removed(self, view_source):
        """The copy-these-commands markdown is replaced by the button."""
        assert "Retrain a model** with the best settings" not in view_source


class TestBacktestWindow:
    """The predict/backtest window must anchor to available data, not the clock."""

    def test_window_ends_at_max_data_date_when_stale(self):
        from datetime import date
        from gefion.experiments.production import backtest_window

        start, end = backtest_window(max_data_date=date(2026, 4, 2),
                                     backtest_days=90, today=date(2026, 7, 2))
        assert end == date(2026, 4, 2)
        assert (end - start).days == 90

    def test_window_ends_today_when_data_is_fresh(self):
        from datetime import date
        from gefion.experiments.production import backtest_window

        start, end = backtest_window(max_data_date=date(2026, 7, 2),
                                     backtest_days=30, today=date(2026, 7, 2))
        assert end == date(2026, 7, 2)
        assert (end - start).days == 30

    def test_window_handles_missing_data_date(self):
        from datetime import date
        from gefion.experiments.production import backtest_window

        start, end = backtest_window(max_data_date=None,
                                     backtest_days=30, today=date(2026, 7, 2))
        assert end == date(2026, 7, 2)


class TestDatasetBuildCmd:
    """Dataset rebuild must reproduce the manifest's horizons and thresholds."""

    def test_cmd_uses_manifest_horizons_and_thresholds(self):
        from gefion.experiments.production import dataset_build_cmd

        manifest = {
            "name": "baseline",
            "format": "parquet",
            "universe": {"exchange": "NASDAQ"},
            "horizons_days": [7, 30],
            "label_spec": {"thresholds": {"7": {"weak": 0.02, "strong": 0.05},
                                          "30": {"weak": 0.05, "strong": 0.1}}},
        }
        cmd = dataset_build_cmd(manifest, "baseline", "applied-exp-41")

        joined = " ".join(cmd)
        assert "--horizons 7,30" in joined
        assert "--weak-thresholds 0.02,0.05" in joined
        assert "--strong-thresholds 0.05,0.1" in joined
        assert "--exchange NASDAQ" in joined
        assert "--export" in joined  # without it only a manifest is written
        assert "--force" in joined  # derived versions must be idempotent on retry

    def test_cmd_omits_thresholds_when_manifest_lacks_them(self):
        from gefion.experiments.production import dataset_build_cmd

        manifest = {"name": "baseline", "horizons_days": [7],
                    "universe": {"exchange": "NASDAQ"}}
        cmd = dataset_build_cmd(manifest, "baseline", "v-test")

        joined = " ".join(cmd)
        assert "--horizons 7" in joined
        assert "--weak-thresholds" not in joined


class TestRunCliJsonParsing:
    """CLI stages emit progress lines before the final JSON document."""

    def test_parses_last_json_line_of_streamed_output(self):
        from unittest.mock import patch, MagicMock
        from gefion.experiments.production import _run_cli

        stdout = (
            '{"status": "ok", "message": "progress", "phase": "running"}\n'
            '{"status": "ok", "metrics": {"sharpe_ratio": 1.2}}\n'
        )
        proc = MagicMock(returncode=0, stdout=stdout, stderr="")
        with patch("gefion.experiments.production.subprocess.run", return_value=proc):
            result = _run_cli(["backtest", "run"])

        assert result["metrics"]["sharpe_ratio"] == 1.2

    def test_single_json_document_still_parses(self):
        from unittest.mock import patch, MagicMock
        from gefion.experiments.production import _run_cli

        proc = MagicMock(returncode=0, stdout='{"status": "ok", "x": 1}\n', stderr="")
        with patch("gefion.experiments.production.subprocess.run", return_value=proc):
            assert _run_cli(["ml", "train"])["x"] == 1
