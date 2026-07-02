"""Tests for probation and auto-demotion of promoted experiment artifacts.

FR-027: promoted artifacts get a probation window during which they are
auto-demoted if model performance degrades. Promotion (cycle or apply)
stamps probation_until; `gefion experiment probation-check` evaluates
candidates and demotes measurable degradation.

TDD: Tests written first, before implementation.
"""
import os
import pathlib
from datetime import date, timedelta
from unittest.mock import patch

import pytest

DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


class TestPinballLoss:
    """The realized-performance metric must be a correct pinball loss."""

    def test_perfect_median_prediction_scores_zero(self):
        from gefion.experiments.probation import pinball_loss

        assert pinball_loss(actual=0.05, predicted=0.05, quantile=0.5) == pytest.approx(0.0)

    def test_underprediction_weighted_by_quantile(self):
        from gefion.experiments.probation import pinball_loss

        # actual above prediction: loss = q * (actual - predicted)
        assert pinball_loss(actual=0.10, predicted=0.0, quantile=0.9) == pytest.approx(0.09)

    def test_overprediction_weighted_by_complement(self):
        from gefion.experiments.probation import pinball_loss

        # actual below prediction: loss = (1 - q) * (predicted - actual)
        assert pinball_loss(actual=0.0, predicted=0.10, quantile=0.9) == pytest.approx(0.01)


class TestDegradationDecision:
    """Demotion requires measurable, sufficient evidence of degradation."""

    def test_degraded_beyond_tolerance(self):
        from gefion.experiments.probation import is_degraded

        assert is_degraded(realized_loss=0.05, baseline_loss=0.03,
                           n_samples=100, tolerance=0.25, min_samples=30) is True

    def test_within_tolerance_is_not_degraded(self):
        from gefion.experiments.probation import is_degraded

        assert is_degraded(realized_loss=0.035, baseline_loss=0.03,
                           n_samples=100, tolerance=0.25, min_samples=30) is False

    def test_insufficient_samples_never_demotes(self):
        from gefion.experiments.probation import is_degraded

        assert is_degraded(realized_loss=0.9, baseline_loss=0.03,
                           n_samples=5, tolerance=0.25, min_samples=30) is False

    def test_missing_baseline_never_demotes(self):
        from gefion.experiments.probation import is_degraded

        assert is_degraded(realized_loss=0.05, baseline_loss=None,
                           n_samples=100, tolerance=0.25, min_samples=30) is False


@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled")
class TestProbationChecks:
    """run_probation_checks against the test database."""

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

    def _make_probation_experiment(self, conn, *, days_left: int, applied: bool = True,
                                   best_score: float = 0.03,
                                   objective: str = "quantile_loss"):
        """Insert an experiment on probation plus its promoted feature rows."""
        from psycopg.types.json import Json

        results = {"best_params": {}}
        if applied:
            results["applied"] = {"model_name": "exp_probation_model",
                                  "model_version": "applied-test",
                                  "applied_at": "2026-06-25T00:00:00+00:00"}
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiments
                    (name, experiment_type, config, results, status, best_score,
                     objective_metric, promoted_at, probation_until)
                VALUES ('probation-test-exp', 'feature_engineering', %s, %s,
                        'completed', %s, %s, NOW(),
                        NOW() + make_interval(days => %s::int))
                RETURNING id
                """,
                (Json({"feature_config": {"function_name": "prob_test",
                                          "function_body": "def compute(df): ..."}}),
                 Json(results), best_score, objective, days_left),
            )
            exp_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO feature_functions (name, version, status, language, function_body)
                VALUES ('exp_prob_test', '1', 'active', 'python', 'def compute(df): ...')
                ON CONFLICT DO NOTHING
                """
            )
            cur.execute(
                """
                INSERT INTO feature_definitions (name, function_name, active)
                VALUES ('exp_prob_test', 'exp_prob_test', TRUE)
                ON CONFLICT (name) DO UPDATE SET active = TRUE
                """
            )
        return exp_id

    @pytest.fixture
    def cleanup(self, conn):
        yield
        with conn.cursor() as cur:
            cur.execute("DELETE FROM experiments WHERE name = 'probation-test-exp'")
            cur.execute("DELETE FROM feature_definitions WHERE name = 'exp_prob_test'")
            cur.execute("DELETE FROM feature_functions WHERE name = 'exp_prob_test'")

    def test_degraded_experiment_is_demoted(self, conn, db_url, cleanup):
        """Measurably degraded performance during probation demotes the artifact."""
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=5)
        with patch.object(probation, "_realized_quantile_loss", return_value=(0.08, 100)):
            summary = probation.run_probation_checks(db_url=db_url)

        assert exp_id in [d["experiment_id"] for d in summary["demoted"]]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT demoted_at, results->'probation'->>'status' FROM experiments WHERE id = %s",
                (exp_id,),
            )
            demoted_at, status = cur.fetchone()
            assert demoted_at is not None
            assert status == "demoted"
            cur.execute("SELECT status FROM feature_functions WHERE name = 'exp_prob_test'")
            assert cur.fetchone()[0] == "demoted"
            cur.execute("SELECT active FROM feature_definitions WHERE name = 'exp_prob_test'")
            assert cur.fetchone()[0] is False

    def test_healthy_experiment_passes_after_window(self, conn, db_url, cleanup):
        """Probation expiring without degradation marks the experiment passed."""
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=-1)
        with patch.object(probation, "_realized_quantile_loss", return_value=(0.03, 100)):
            summary = probation.run_probation_checks(db_url=db_url)

        assert exp_id in [p["experiment_id"] for p in summary["passed"]]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT demoted_at, results->'probation'->>'status' FROM experiments WHERE id = %s",
                (exp_id,),
            )
            demoted_at, status = cur.fetchone()
            assert demoted_at is None
            assert status == "passed"

    def test_healthy_experiment_stays_on_probation_inside_window(self, conn, db_url, cleanup):
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=5)
        with patch.object(probation, "_realized_quantile_loss", return_value=(0.03, 100)):
            summary = probation.run_probation_checks(db_url=db_url)

        checked_ids = [c["experiment_id"] for c in summary["monitoring"]]
        assert exp_id in checked_ids
        with conn.cursor() as cur:
            cur.execute("SELECT demoted_at FROM experiments WHERE id = %s", (exp_id,))
            assert cur.fetchone()[0] is None

    def test_unapplied_experiment_is_skipped_not_demoted(self, conn, db_url, cleanup):
        """No applied model means no measurable performance — never demote blindly."""
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=5, applied=False)
        summary = probation.run_probation_checks(db_url=db_url)

        assert exp_id in [s["experiment_id"] for s in summary["skipped"]]
        with conn.cursor() as cur:
            cur.execute("SELECT demoted_at FROM experiments WHERE id = %s", (exp_id,))
            assert cur.fetchone()[0] is None

    def test_insufficient_outcomes_do_not_demote(self, conn, db_url, cleanup):
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=5)
        with patch.object(probation, "_realized_quantile_loss", return_value=(0.9, 3)):
            summary = probation.run_probation_checks(db_url=db_url)

        with conn.cursor() as cur:
            cur.execute("SELECT demoted_at FROM experiments WHERE id = %s", (exp_id,))
            assert cur.fetchone()[0] is None

    def test_demoted_experiment_not_rechecked(self, conn, db_url, cleanup):
        """Demotion is terminal — the next run must not touch the experiment."""
        from gefion.experiments import probation

        exp_id = self._make_probation_experiment(conn, days_left=5)
        with patch.object(probation, "_realized_quantile_loss", return_value=(0.08, 100)):
            probation.run_probation_checks(db_url=db_url)
            summary2 = probation.run_probation_checks(db_url=db_url)

        touched = [x["experiment_id"]
                   for key in ("demoted", "passed", "monitoring", "skipped")
                   for x in summary2[key]]
        assert exp_id not in touched


@pytest.mark.skipif(not DB_TESTS_ENABLED, reason="Database tests disabled")
class TestCyclePromotionStampsProbation:
    """_promote_fdr_survivors must open the probation window (FR-027)."""

    def test_promote_sets_probation_until(self):
        import psycopg
        from psycopg.types.json import Json
        from gefion.db import schema
        from gefion.experiments.cycle_runner import CycleRunner

        db_url = schema.test_db_url()
        try:
            conn = psycopg.connect(db_url)
        except psycopg.OperationalError:
            pytest.skip("DB not available")
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiments (name, experiment_type, config, status)
                VALUES ('promote-probation-test', 'feature_engineering', %s, 'completed')
                RETURNING id
                """,
                (Json({"feature_config": {"function_name": "promo_test",
                                          "function_body": "def compute(df): ..."}}),),
            )
            exp_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO feature_functions (name, version, status, language, function_body)
                VALUES ('exp_promo_test', '1', 'experimental', 'python', 'def compute(df): ...')
                """
            )
        try:
            runner = CycleRunner(db_url)
            promoted = runner._promote_fdr_survivors(cycle_id=0, survivor_ids=[exp_id])
            assert promoted == 1

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT promoted_at, probation_until FROM experiments WHERE id = %s",
                    (exp_id,),
                )
                promoted_at, probation_until = cur.fetchone()
            assert promoted_at is not None
            assert probation_until is not None
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM experiments WHERE id = %s", (exp_id,))
                cur.execute("DELETE FROM feature_functions WHERE name = 'exp_promo_test'")
                cur.execute("DELETE FROM feature_definitions WHERE name = 'exp_promo_test'")
            conn.close()


class TestProbationCLI:
    """`gefion experiment probation-check` command."""

    def test_command_exists(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["experiment", "probation-check", "--help"])
        assert result.exit_code == 0

    def test_invokes_checks(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        summary = {"checked": 0, "demoted": [], "passed": [], "monitoring": [], "skipped": []}
        with patch("gefion.experiments.probation.run_probation_checks",
                   return_value=summary) as checks:
            result = CliRunner().invoke(app, ["experiment", "probation-check", "--json"])

        assert checks.called
        assert result.exit_code == 0


class TestProbationWiring:
    """Probation must run automatically and be visible in the UI."""

    def test_data_update_runs_probation_check(self):
        """New data arriving is when performance can be re-measured."""
        source = pathlib.Path("src/gefion/cli.py").read_text()
        idx = source.index("def _update_all_impl(")
        # The probation hook must appear within the data-update implementation
        assert "run_probation_checks" in source[idx:idx + 40000]

    def test_experiments_ui_shows_probation_status(self):
        source = pathlib.Path("src/gefion/ui/views/experiments.py").read_text()
        assert "probation_until" in source
        assert "demoted_at" in source

    def test_results_view_labels_probation_states(self):
        source = pathlib.Path("src/gefion/ui/views/experiments.py").read_text()
        assert "On probation" in source
        assert "Demoted" in source


class TestDemoteCLI:
    """`gefion experiment demote` — manual demotion with a recorded reason."""

    def test_command_exists(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["experiment", "demote", "--help"])
        assert result.exit_code == 0

    def test_requires_reason(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["experiment", "demote", "--id", "1"])
        assert result.exit_code != 0

    def test_invokes_demote(self):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gefion.cli import app

        with patch("gefion.experiments.probation.demote_experiment",
                   return_value=True) as demote:
            result = CliRunner().invoke(
                app, ["experiment", "demote", "--id", "41",
                      "--reason", "manual review", "--json"])

        assert demote.called
        assert demote.call_args.args[:2] == (41, "manual review")
        assert result.exit_code == 0

    def test_already_demoted_reports_cleanly(self):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gefion.cli import app

        with patch("gefion.experiments.probation.demote_experiment",
                   return_value=False):
            result = CliRunner().invoke(
                app, ["experiment", "demote", "--id", "41",
                      "--reason", "manual review"])

        assert result.exit_code == 0
        assert "already" in result.stdout.lower()
