"""Tests for CycleRunner — autonomous experiment cycle orchestration.

TDD: Tests written FIRST, before implementation.
"""
import inspect
from unittest.mock import patch, MagicMock, call
from pathlib import Path

import pytest


class TestCycleRunnerClass:
    """Verify CycleRunner exists and has the right interface."""

    def test_class_exists(self):
        from gefion.experiments.cycle_runner import CycleRunner
        assert CycleRunner is not None

    def test_has_run_cycle_method(self):
        from gefion.experiments.cycle_runner import CycleRunner
        assert hasattr(CycleRunner, "run_cycle")
        sig = inspect.signature(CycleRunner.run_cycle)
        assert "cycle_id" in sig.parameters

    def test_has_observability_span(self):
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner.run_cycle)
        assert "create_span" in src

    def test_has_default_search_spaces(self):
        from gefion.experiments.cycle_runner import DEFAULT_SEARCH_SPACES
        assert "hyperparameter" in DEFAULT_SEARCH_SPACES
        assert "model_comparison" in DEFAULT_SEARCH_SPACES
        assert "feature_engineering" in DEFAULT_SEARCH_SPACES
        assert "label_engineering" in DEFAULT_SEARCH_SPACES


class TestCycleRunnerOrchestration:
    """Test the discover → propose → approve → run → evaluate flow."""

    def _make_runner(self):
        from gefion.experiments.cycle_runner import CycleRunner
        return CycleRunner("postgresql://test:test@localhost/test")

    def _mock_cycle_row(self, **overrides):
        """Return a mock cycle DB row."""
        defaults = {
            "id": 1,
            "name": "test-cycle",
            "holdout_start_date": "2026-03-01",
            "holdout_end_date": "2026-03-27",
            "fdr_rate": 0.10,
            "max_experiments": 5,
            "compute_budget_seconds": 7200,
            "status": "proposed",
            "config": {
                "allowed_types": ["hyperparameter", "model_comparison"],
                "auto_approve": True,
                "dataset_uri": "datasets/baseline_v2/manifest.json",
                "horizon_days": 7,
                "algorithm": "lightgbm",
                "max_trials_per_experiment": 5,
                "search_method": "bayesian",
                "max_parallel": 1,
            },
        }
        defaults.update(overrides)
        return defaults

    def _mock_hypotheses(self):
        return [
            {"principle_id": "p1", "experiment_type": "hyperparameter",
             "feasibility": "ready", "description": "Tune LR"},
            {"principle_id": "p2", "experiment_type": "model_comparison",
             "feasibility": "ready", "description": "Compare models"},
            {"principle_id": "p3", "experiment_type": "feature_engineering",
             "feasibility": "ready", "description": "New feature"},
            {"principle_id": "p4", "experiment_type": "strategy_optimization",
             "feasibility": "blocked", "description": "Blocked one"},
        ]

    def test_run_cycle_filters_by_allowed_types(self):
        """Only hypotheses matching allowed_types should become experiments."""
        runner = self._make_runner()
        cycle = self._mock_cycle_row()
        hypotheses = self._mock_hypotheses()

        with patch.object(runner, "_load_cycle", return_value=cycle), \
             patch.object(runner, "_run_discovery", return_value=hypotheses), \
             patch.object(runner, "_preflight_check", return_value=[]), \
             patch.object(runner, "_propose_experiment", return_value=1) as mock_propose, \
             patch.object(runner, "_run_experiments", return_value=[]), \
             patch.object(runner, "_evaluate_cycle", return_value={}), \
             patch.object(runner, "_update_cycle_status"):
            runner.run_cycle(1)

        # Only hyperparameter and model_comparison are allowed
        assert mock_propose.call_count == 2
        proposed_types = [c[0][0]["experiment_type"] for c in mock_propose.call_args_list]
        assert "hyperparameter" in proposed_types
        assert "model_comparison" in proposed_types
        assert "feature_engineering" not in proposed_types

    def test_run_cycle_respects_max_experiments(self):
        """Should stop proposing after max_experiments."""
        runner = self._make_runner()
        cycle = self._mock_cycle_row(max_experiments=1)
        # Allow all types so we have more hypotheses than max
        cycle["config"]["allowed_types"] = ["hyperparameter", "model_comparison", "feature_engineering"]
        hypotheses = self._mock_hypotheses()

        with patch.object(runner, "_load_cycle", return_value=cycle), \
             patch.object(runner, "_run_discovery", return_value=hypotheses), \
             patch.object(runner, "_preflight_check", return_value=[]), \
             patch.object(runner, "_propose_experiment", return_value=1) as mock_propose, \
             patch.object(runner, "_run_experiments", return_value=[]), \
             patch.object(runner, "_evaluate_cycle", return_value={}), \
             patch.object(runner, "_update_cycle_status"):
            runner.run_cycle(1)

        assert mock_propose.call_count == 1

    def test_run_cycle_auto_approves(self):
        """With auto_approve=True, experiments should be approved automatically."""
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner.run_cycle)
        assert "auto_approve" in src
        assert "approve" in src

    def test_run_cycle_applies_fdr(self):
        """Cycle evaluation must use apply_fdr."""
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner)
        assert "apply_fdr" in src

    def test_run_cycle_checks_resources(self):
        """Should check system resources before running experiments."""
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner)
        assert "preflight" in src.lower() or "safety" in src.lower()

    def test_preflight_check_exists(self):
        """CycleRunner must have _preflight_check method."""
        from gefion.experiments.cycle_runner import CycleRunner
        assert hasattr(CycleRunner, "_preflight_check")

    def test_preflight_validates_dataset(self):
        """_preflight_check should catch missing datasets."""
        from gefion.experiments.cycle_runner import CycleRunner
        runner = CycleRunner("postgresql://test:test@localhost/test")
        issues = runner._preflight_check(
            dataset_uri="nonexistent/manifest.json",
            allowed_horizons=[7],
            allowed_types=["hyperparameter"],
        )
        assert len(issues) > 0
        assert any("not found" in i["message"].lower() for i in issues)


class TestCycleRunnerCLI:
    """Test CLI command for cycle-run."""

    def test_cycle_run_command_exists(self):
        from gefion.cli import experiment_app
        command_names = [cmd.name for cmd in experiment_app.registered_commands]
        assert "cycle-run" in command_names

    def test_cycle_run_accepts_cycle_id(self):
        import inspect
        from gefion.cli import experiment_cycle_run
        sig = inspect.signature(experiment_cycle_run)
        assert "cycle_id" in sig.parameters


class TestFeatureFunctionGeneration:
    """Test that CycleRunner generates function bodies for feature engineering."""

    def test_generate_function_body_exists(self):
        from gefion.experiments.cycle_runner import _generate_function_body
        assert _generate_function_body is not None

    def test_generates_body_for_variance_ratio(self):
        from gefion.experiments.cycle_runner import _generate_function_body
        body = _generate_function_body("variance-ratio-rejects-random-walk", "")
        assert body is not None
        assert "def compute(df" in body
        assert "variance" in body.lower() or "var" in body.lower()

    def test_generates_body_for_volume_principle(self):
        from gefion.experiments.cycle_runner import _generate_function_body
        body = _generate_function_body("volume-price-informed-trading", "")
        assert body is not None
        assert "def compute(df" in body
        assert "volume" in body.lower()

    def test_generates_body_for_unknown_principle(self):
        """Unknown principles should get a fallback function, not None."""
        from gefion.experiments.cycle_runner import _generate_function_body
        body = _generate_function_body("totally-unknown-principle", "")
        assert body is not None
        assert "def compute(df" in body

    def test_claude_generation_tried_first(self):
        """_generate_function_body should try Claude Code before templates."""
        import inspect
        from gefion.experiments.cycle_runner import _generate_function_body
        src = inspect.getsource(_generate_function_body)
        claude_pos = src.index("_generate_function_body_claude")
        template_pos = src.index("_generate_function_body_template")
        assert claude_pos < template_pos, "Claude generation should be tried before templates"

    def test_claude_function_validates_syntax(self):
        """Claude-generated code must be validated for syntax and compute() presence."""
        import inspect
        from gefion.experiments.cycle_runner import _generate_function_body_claude
        src = inspect.getsource(_generate_function_body_claude)
        assert "compile(" in src, "Must validate syntax"
        assert "compute" in src, "Must check for compute() function"

    def test_claude_generation_uses_claude_cli(self):
        """Should invoke claude -p for code generation."""
        import inspect
        from gefion.experiments.cycle_runner import _generate_function_body_claude
        src = inspect.getsource(_generate_function_body_claude)
        assert '"claude"' in src
        assert '"-p"' in src


class TestExperimentalFeatureStorage:
    """Test that generated feature functions are stored as experimental."""

    def test_store_experimental_function_exists(self):
        from gefion.experiments.cycle_runner import CycleRunner
        assert hasattr(CycleRunner, "_store_experimental_function")

    def test_store_uses_experimental_status(self):
        """Stored functions must have status='experimental', not 'active'."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner._store_experimental_function)
        assert "'experimental'" in src or '"experimental"' in src

    def test_store_uses_upsert_feature_function(self):
        """Must use existing upsert_feature_function helper."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner._store_experimental_function)
        assert "upsert_feature_function" in src

    def test_promote_survivors_method_exists(self):
        """CycleRunner must have _promote_fdr_survivors method."""
        from gefion.experiments.cycle_runner import CycleRunner
        assert hasattr(CycleRunner, "_promote_fdr_survivors")

    def test_promote_changes_status_to_active(self):
        """Promoted functions must have status='active'."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner._promote_fdr_survivors)
        assert "'active'" in src or '"active"' in src

    def test_promote_called_after_fdr(self):
        """_promote_fdr_survivors must be called after FDR evaluation."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner.run_cycle)
        # Promote should appear after evaluate
        assert "_promote_fdr_survivors" in src

    def test_templates_are_valid_python(self):
        """All function templates must be valid Python."""
        from gefion.experiments.cycle_runner import FEATURE_FUNCTION_TEMPLATES
        for name, body in FEATURE_FUNCTION_TEMPLATES.items():
            try:
                compile(body, f"<template:{name}>", "exec")
            except SyntaxError as e:
                assert False, f"Template '{name}' has invalid Python: {e}"

    def test_templates_define_compute(self):
        """All function templates must define a compute() function."""
        from gefion.experiments.cycle_runner import FEATURE_FUNCTION_TEMPLATES
        import numpy as np
        import pandas as pd
        for name, body in FEATURE_FUNCTION_TEMPLATES.items():
            env = {"np": np, "numpy": np, "pd": pd, "pandas": pd,
                   "__builtins__": __builtins__}
            exec(body, env)
            assert callable(env.get("compute")), f"Template '{name}' must define compute()"


class TestDatasetRebuild:
    """Test dataset rebuild after new experimental features."""

    def test_rebuild_method_exists(self):
        from gefion.experiments.cycle_runner import CycleRunner
        assert hasattr(CycleRunner, "_rebuild_dataset")

    def test_rebuild_called_when_feature_engineering_present(self):
        """run_cycle should rebuild dataset when feature_engineering experiments are proposed."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner
        src = inspect.getsource(CycleRunner.run_cycle)
        assert "_rebuild_dataset" in src
        assert "Rebuilding dataset" in src


class TestCycleStartConfigFile:
    """Test cycle-start accepts --config file."""

    def test_cycle_start_accepts_config_file(self):
        import inspect
        from gefion.cli import experiment_cycle_start
        sig = inspect.signature(experiment_cycle_start)
        assert "config_file" in sig.parameters

    def test_cycle_start_help_mentions_config(self):
        from gefion.cli import experiment_cycle_start
        assert "config" in experiment_cycle_start.__doc__.lower()


class TestCycleRunnerUI:
    """Test UI has guardrail controls."""

    def test_ui_has_theme_selection(self):
        from pathlib import Path
        content = Path("src/gefion/ui/views/experiments.py").read_text()
        assert "selected_themes" in content
        assert "Research Themes" in content

    def test_ui_derives_allowed_types_from_themes(self):
        from pathlib import Path
        content = Path("src/gefion/ui/views/experiments.py").read_text()
        assert "allowed_types" in content
        assert "experiment_types" in content

    def test_ui_stores_cycle_config(self):
        from pathlib import Path
        content = Path("src/gefion/ui/views/experiments.py").read_text()
        assert "cycle_config" in content

    def test_ui_calls_cycle_run(self):
        from pathlib import Path
        content = Path("src/gefion/ui/views/experiments.py").read_text()
        assert "cycle-run" in content


class TestReusedFeatureConfig:
    """Reusing an existing experimental feature must carry its function body.

    Without the body, the evaluator can't compute the feature (it only knows
    builtin templates by name), trials silently degrade to all-NaN features,
    and _promote_fdr_survivors skips promotion. Cycle 10 hit exactly this.
    """

    def test_reused_feature_config_includes_body(self):
        """_reused_feature_config loads the stored function body from the DB."""
        import os
        import psycopg
        import pytest as _pytest
        from gefion.db import schema
        from gefion.experiments.cycle_runner import CycleRunner

        if os.getenv("ENABLE_DB_TESTS", "0") != "1":
            _pytest.skip("DB tests disabled")
        db_url = schema.test_db_url()
        try:
            conn = psycopg.connect(db_url)
        except psycopg.OperationalError:
            _pytest.skip("DB not available")
        conn.autocommit = True

        body = "import pandas as pd\n\ndef compute(df, window=10):\n    return df['close'].pct_change().rolling(window).std().fillna(0)\n"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO feature_functions (name, version, status, language, function_body)
                VALUES ('exp_reuse_body_test', '1', 'experimental', 'python', %s)
                """,
                (body,),
            )
        try:
            runner = CycleRunner(db_url)
            config = runner._reused_feature_config("exp_reuse_body_test")

            assert config["function_name"] == "reuse_body_test"
            assert config["function_body"] == body
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feature_functions WHERE name = 'exp_reuse_body_test'")
            conn.close()

    def test_reused_feature_config_without_stored_body(self):
        """A registry row with an empty body still yields a usable config."""
        import os
        import psycopg
        import pytest as _pytest
        from gefion.db import schema
        from gefion.experiments.cycle_runner import CycleRunner

        if os.getenv("ENABLE_DB_TESTS", "0") != "1":
            _pytest.skip("DB tests disabled")
        db_url = schema.test_db_url()
        try:
            conn = psycopg.connect(db_url)
        except psycopg.OperationalError:
            _pytest.skip("DB not available")
        conn.autocommit = True

        runner = CycleRunner(db_url)
        config = runner._reused_feature_config("exp_missing_from_registry")

        assert config == {"function_name": "missing_from_registry"}


class TestRebuildDatasetCmd:
    """_rebuild_dataset must produce dataset files, not just a manifest."""

    def test_rebuild_uses_shared_export_cmd(self):
        """The rebuild must reuse production.dataset_build_cmd (with --export)."""
        import inspect
        from gefion.experiments.cycle_runner import CycleRunner

        source = inspect.getsource(CycleRunner._rebuild_dataset)
        assert "dataset_build_cmd" in source


class TestEvaluateCycleFailClosed:
    """FDR survival requires a real holdout p-value (FR-020/021).

    The original implementation used best_score as a p-value proxy, so
    experiments that were never holdout-evaluated (p-value NULL) could
    "survive FDR" — cycles 10/11 promoted baseline clones this way.
    """

    @staticmethod
    def _db():
        import os
        import psycopg
        import pytest as _pytest
        from gefion.db import schema

        if os.getenv("ENABLE_DB_TESTS", "0") != "1":
            _pytest.skip("DB tests disabled")
        try:
            conn = psycopg.connect(schema.test_db_url())
        except psycopg.OperationalError:
            _pytest.skip("DB not available")
        conn.autocommit = True
        return conn, schema.test_db_url()

    @staticmethod
    def _seed_cycle(conn):
        from datetime import date, timedelta

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiment_cycles (name, holdout_start_date, holdout_end_date)
                VALUES ('fdr-closed-cycle', %s, %s) RETURNING id
                """,
                (date.today() - timedelta(days=42), date.today()),
            )
            return cur.fetchone()[0]

    @staticmethod
    def _seed_experiment(conn, name, p_value):
        from psycopg.types.json import Json

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiments
                    (name, experiment_type, config, status, best_score, holdout_p_value)
                VALUES (%s, 'feature_engineering', %s, 'completed', 0.03, %s)
                RETURNING id
                """,
                (name, Json({}), p_value),
            )
            return cur.fetchone()[0]

    def test_experiment_without_pvalue_cannot_survive(self):
        from gefion.experiments.cycle_runner import CycleRunner

        conn, db_url = self._db()
        cycle_id = self._seed_cycle(conn)
        exp_id = self._seed_experiment(conn, "fdr-closed-nopval", None)
        try:
            runner = CycleRunner(db_url)
            result = runner._evaluate_cycle(cycle_id=cycle_id, experiment_ids=[exp_id])

            assert result["survivors"] == 0
            assert exp_id not in result["survivor_ids"]
            assert exp_id in result["unevaluated_ids"]
            with conn.cursor() as cur:
                cur.execute("SELECT fdr_survived FROM experiments WHERE id = %s", (exp_id,))
                assert cur.fetchone()[0] is False
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM experiments WHERE id = %s", (exp_id,))
                cur.execute("DELETE FROM experiment_cycles WHERE id = %s", (cycle_id,))
            conn.close()

    def test_real_pvalues_gate_survival(self):
        from gefion.experiments.cycle_runner import CycleRunner

        conn, db_url = self._db()
        cycle_id = self._seed_cycle(conn)
        strong = self._seed_experiment(conn, "fdr-closed-strong", 0.001)
        weak = self._seed_experiment(conn, "fdr-closed-weak", 0.9)
        unevaluated = self._seed_experiment(conn, "fdr-closed-null", None)
        try:
            runner = CycleRunner(db_url)
            result = runner._evaluate_cycle(
                cycle_id=cycle_id, experiment_ids=[strong, weak, unevaluated]
            )

            assert result["survivor_ids"] == [strong]
            assert result["unevaluated_ids"] == [unevaluated]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, fdr_survived FROM experiments WHERE id = ANY(%s) ORDER BY id",
                    ([strong, weak, unevaluated],),
                )
                by_id = dict(cur.fetchall())
            assert by_id[strong] is True
            assert by_id[weak] is False
            assert by_id[unevaluated] is False
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM experiments WHERE id = ANY(%s)",
                    ([strong, weak, unevaluated],),
                )
                cur.execute("DELETE FROM experiment_cycles WHERE id = %s", (cycle_id,))
            conn.close()
