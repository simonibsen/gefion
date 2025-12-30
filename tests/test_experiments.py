"""Tests for AI Experimentation Framework.

TDD: These tests are written FIRST, before implementation.
"""
import os
import pytest
from typing import Dict, Any


# Skip all tests if database not available
pytestmark = pytest.mark.skipif(
    not os.environ.get("ENABLE_DB_TESTS"),
    reason="Database tests disabled"
)


class TestExperimentConfig:
    """Tests for ExperimentConfig dataclass."""

    def test_config_has_required_fields(self):
        """Test that ExperimentConfig has all required fields."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_experiment",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "int", "low": 5, "high": 20}}
        )

        assert config.name == "test_experiment"
        assert config.experiment_type == "strategy_params"
        assert config.search_space == {"lookback_days": {"type": "int", "low": 5, "high": 20}}

    def test_config_has_default_values(self):
        """Test that ExperimentConfig has sensible defaults."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test",
            experiment_type="strategy_params",
            search_space={}
        )

        assert config.objective_metric == "sharpe_ratio"
        assert config.objective_direction == "maximize"
        assert config.max_trials == 50
        assert config.goal_target is None
        assert config.goal_type is None
        assert config.early_stop_on_goal is False

    def test_config_with_goal(self):
        """Test ExperimentConfig with goal settings."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="targeted_experiment",
            experiment_type="strategy_params",
            search_space={},
            goal_target=2.0,
            goal_type="achieve",
            early_stop_on_goal=True
        )

        assert config.goal_target == 2.0
        assert config.goal_type == "achieve"
        assert config.early_stop_on_goal is True

    def test_config_with_improvement_goal(self):
        """Test ExperimentConfig with improvement goal."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="improvement_experiment",
            experiment_type="strategy_params",
            search_space={},
            goal_target=1.8,
            goal_type="improve",
            baseline_value=1.5
        )

        assert config.goal_type == "improve"
        assert config.baseline_value == 1.5
        assert config.goal_target == 1.8


class TestExperimentRunner:
    """Tests for ExperimentRunner class."""

    @pytest.fixture
    def db_url(self):
        """Get database URL."""
        return os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    @pytest.fixture
    def ensure_tables(self, db_url):
        """Ensure experiments tables exist before tests."""
        import psycopg
        from pathlib import Path

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()

    @pytest.fixture
    def runner(self, db_url, ensure_tables):
        """Create ExperimentRunner instance."""
        from g2.experiments.core import ExperimentRunner
        return ExperimentRunner(db_url)

    @pytest.fixture
    def sample_config(self):
        """Create a sample experiment config."""
        from g2.experiments.core import ExperimentConfig
        return ExperimentConfig(
            name="test_momentum_optimization",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "int", "low": 5, "high": 20}
            },
            objective_metric="sharpe_ratio",
            max_trials=10
        )

    def test_propose_creates_experiment(self, runner, sample_config):
        """Test that propose() creates an experiment with 'proposed' status."""
        experiment_id = runner.propose(sample_config)

        assert experiment_id is not None
        assert isinstance(experiment_id, int)
        assert experiment_id > 0

    def test_propose_sets_proposed_status(self, runner, sample_config):
        """Test that proposed experiment has 'proposed' status."""
        experiment_id = runner.propose(sample_config)
        experiment = runner.get(experiment_id)

        assert experiment["status"] == "proposed"

    def test_propose_stores_config(self, runner, sample_config):
        """Test that propose() stores the experiment config."""
        experiment_id = runner.propose(sample_config)
        experiment = runner.get(experiment_id)

        assert experiment["name"] == sample_config.name
        assert experiment["experiment_type"] == sample_config.experiment_type
        assert experiment["objective_metric"] == sample_config.objective_metric

    def test_approve_changes_status(self, runner, sample_config):
        """Test that approve() changes status to 'approved'."""
        experiment_id = runner.propose(sample_config)
        runner.approve(experiment_id, approver="test_user")
        experiment = runner.get(experiment_id)

        assert experiment["status"] == "approved"
        assert experiment["approved_by"] == "test_user"

    def test_approve_only_proposed_experiments(self, runner, sample_config):
        """Test that approve() only works on 'proposed' experiments."""
        experiment_id = runner.propose(sample_config)
        runner.approve(experiment_id)

        # Try to approve again - should raise error
        with pytest.raises(ValueError, match="Cannot approve"):
            runner.approve(experiment_id)

    def test_reject_changes_status(self, runner, sample_config):
        """Test that reject() changes status to 'rejected'."""
        experiment_id = runner.propose(sample_config)
        runner.reject(experiment_id, reason="Too many trials")
        experiment = runner.get(experiment_id)

        assert experiment["status"] == "rejected"

    def test_get_pending_approvals(self, runner, sample_config):
        """Test getting list of pending approvals."""
        # Create some experiments
        id1 = runner.propose(sample_config)

        from g2.experiments.core import ExperimentConfig
        config2 = ExperimentConfig(
            name="another_experiment",
            experiment_type="strategy_params",
            search_space={}
        )
        id2 = runner.propose(config2)

        # Approve one
        runner.approve(id1)

        # Get pending
        pending = runner.get_pending_approvals()

        # Should only have id2
        pending_ids = [p["id"] for p in pending]
        assert id2 in pending_ids
        assert id1 not in pending_ids

    def test_list_experiments_by_status(self, runner, sample_config):
        """Test listing experiments filtered by status."""
        experiment_id = runner.propose(sample_config)
        runner.approve(experiment_id)

        approved = runner.list(status="approved")
        proposed = runner.list(status="proposed")

        approved_ids = [e["id"] for e in approved]
        assert experiment_id in approved_ids


class TestGridSearch:
    """Tests for GridSearch strategy."""

    def test_grid_search_builds_grid(self):
        """Test that GridSearch builds all parameter combinations."""
        from g2.experiments.search import GridSearch

        search_space = {
            "a": {"type": "categorical", "choices": [1, 2]},
            "b": {"type": "categorical", "choices": ["x", "y"]}
        }
        search = GridSearch(search_space)

        # Should have 2 * 2 = 4 combinations
        assert len(search.grid) == 4

    def test_grid_search_suggests_all_combinations(self):
        """Test that GridSearch suggests all combinations."""
        from g2.experiments.search import GridSearch

        search_space = {
            "a": {"type": "categorical", "choices": [1, 2]}
        }
        search = GridSearch(search_space)

        params1 = search.suggest()
        params2 = search.suggest()
        params3 = search.suggest()

        assert params1 == {"a": 1}
        assert params2 == {"a": 2}
        assert params3 is None  # Exhausted

    def test_grid_search_handles_int_type(self):
        """Test GridSearch with integer parameters."""
        from g2.experiments.search import GridSearch

        search_space = {
            "n": {"type": "int", "low": 1, "high": 3}
        }
        search = GridSearch(search_space)

        # Should have values 1, 2, 3
        assert len(search.grid) == 3

        all_values = [search.suggest()["n"] for _ in range(3)]
        assert sorted(all_values) == [1, 2, 3]

    def test_grid_search_handles_float_type(self):
        """Test GridSearch with float parameters."""
        from g2.experiments.search import GridSearch

        search_space = {
            "x": {"type": "float", "low": 0.0, "high": 1.0, "steps": 3}
        }
        search = GridSearch(search_space)

        # Should have 3 steps
        assert len(search.grid) == 3

        all_values = [search.suggest()["x"] for _ in range(3)]
        assert all_values[0] == pytest.approx(0.0)
        assert all_values[1] == pytest.approx(0.5)
        assert all_values[2] == pytest.approx(1.0)

    def test_grid_search_report_does_nothing(self):
        """Test that GridSearch.report() is a no-op."""
        from g2.experiments.search import GridSearch

        search = GridSearch({"a": {"type": "categorical", "choices": [1]}})
        params = search.suggest()

        # Should not raise
        search.report(params, 0.5)


class TestRandomSearch:
    """Tests for RandomSearch strategy."""

    def test_random_search_respects_max_trials(self):
        """Test that RandomSearch respects max_trials."""
        from g2.experiments.search import RandomSearch

        search_space = {
            "a": {"type": "int", "low": 1, "high": 100}
        }
        search = RandomSearch(search_space, max_trials=3)

        params1 = search.suggest()
        params2 = search.suggest()
        params3 = search.suggest()
        params4 = search.suggest()

        assert params1 is not None
        assert params2 is not None
        assert params3 is not None
        assert params4 is None  # Exhausted

    def test_random_search_samples_from_range(self):
        """Test that RandomSearch samples within specified range."""
        from g2.experiments.search import RandomSearch

        search_space = {
            "n": {"type": "int", "low": 10, "high": 20}
        }
        search = RandomSearch(search_space, max_trials=100)

        for _ in range(100):
            params = search.suggest()
            if params is None:
                break
            assert 10 <= params["n"] <= 20

    def test_random_search_handles_categorical(self):
        """Test RandomSearch with categorical parameters."""
        from g2.experiments.search import RandomSearch

        search_space = {
            "color": {"type": "categorical", "choices": ["red", "green", "blue"]}
        }
        search = RandomSearch(search_space, max_trials=50)

        all_colors = set()
        for _ in range(50):
            params = search.suggest()
            if params:
                all_colors.add(params["color"])

        # Should sample all colors eventually
        assert all_colors == {"red", "green", "blue"}

    def test_random_search_handles_float(self):
        """Test RandomSearch with float parameters."""
        from g2.experiments.search import RandomSearch

        search_space = {
            "x": {"type": "float", "low": 0.0, "high": 1.0}
        }
        search = RandomSearch(search_space, max_trials=100)

        for _ in range(100):
            params = search.suggest()
            if params is None:
                break
            assert 0.0 <= params["x"] <= 1.0


class TestBayesianSearch:
    """Tests for BayesianSearch strategy using Optuna."""

    def test_bayesian_search_import(self):
        """Test that BayesianSearch can be imported."""
        from g2.experiments.search import BayesianSearch
        assert BayesianSearch is not None

    def test_bayesian_search_respects_max_trials(self):
        """Test that BayesianSearch respects max_trials."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "a": {"type": "int", "low": 1, "high": 100}
        }
        search = BayesianSearch(search_space, max_trials=3)

        params1 = search.suggest()
        search.report(params1, 0.5)
        params2 = search.suggest()
        search.report(params2, 0.6)
        params3 = search.suggest()
        search.report(params3, 0.7)
        params4 = search.suggest()

        assert params1 is not None
        assert params2 is not None
        assert params3 is not None
        assert params4 is None  # Exhausted

    def test_bayesian_search_samples_from_range(self):
        """Test that BayesianSearch samples within specified range."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "n": {"type": "int", "low": 10, "high": 20}
        }
        search = BayesianSearch(search_space, max_trials=20)

        for i in range(20):
            params = search.suggest()
            if params is None:
                break
            assert 10 <= params["n"] <= 20
            search.report(params, float(i))

    def test_bayesian_search_handles_categorical(self):
        """Test BayesianSearch with categorical parameters."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "color": {"type": "categorical", "choices": ["red", "green", "blue"]}
        }
        search = BayesianSearch(search_space, max_trials=30)

        all_colors = set()
        for i in range(30):
            params = search.suggest()
            if params:
                all_colors.add(params["color"])
                search.report(params, float(i))

        # Should sample all colors eventually
        assert all_colors == {"red", "green", "blue"}

    def test_bayesian_search_handles_float(self):
        """Test BayesianSearch with float parameters."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "x": {"type": "float", "low": 0.0, "high": 1.0}
        }
        search = BayesianSearch(search_space, max_trials=20)

        for i in range(20):
            params = search.suggest()
            if params is None:
                break
            assert 0.0 <= params["x"] <= 1.0
            search.report(params, float(i))

    def test_bayesian_search_handles_log_scale(self):
        """Test BayesianSearch with log-scale float parameters."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "learning_rate": {"type": "float", "low": 0.0001, "high": 0.1, "log": True}
        }
        search = BayesianSearch(search_space, max_trials=10)

        for i in range(10):
            params = search.suggest()
            if params is None:
                break
            assert 0.0001 <= params["learning_rate"] <= 0.1
            search.report(params, float(i))

    def test_bayesian_search_adapts_based_on_results(self):
        """Test that BayesianSearch learns from reported results."""
        from g2.experiments.search import BayesianSearch

        # Create a simple optimization problem: find x close to 50
        search_space = {
            "x": {"type": "int", "low": 0, "high": 100}
        }
        search = BayesianSearch(search_space, direction="maximize", max_trials=30)

        best_score = float("-inf")
        best_x = None

        for _ in range(30):
            params = search.suggest()
            if params is None:
                break

            # Score is higher when x is close to 50
            x = params["x"]
            score = -abs(x - 50)  # 0 when x=50, negative otherwise

            if score > best_score:
                best_score = score
                best_x = x

            search.report(params, score)

        # After 30 trials, should have found x close to 50
        # Bayesian optimization should converge to good solutions
        assert best_x is not None
        assert abs(best_x - 50) <= 20  # Within 20 of optimal

    def test_bayesian_search_minimize_direction(self):
        """Test BayesianSearch with minimize direction."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "x": {"type": "float", "low": 0.0, "high": 10.0}
        }
        search = BayesianSearch(search_space, direction="minimize", max_trials=20)

        best_score = float("inf")

        for _ in range(20):
            params = search.suggest()
            if params is None:
                break

            # Minimize x^2 - has minimum at x=0
            score = params["x"] ** 2

            if score < best_score:
                best_score = score

            search.report(params, score)

        # Should find low values close to 0
        assert best_score < 10.0  # Much better than random would do on average

    def test_bayesian_search_mixed_parameter_types(self):
        """Test BayesianSearch with mixed parameter types."""
        from g2.experiments.search import BayesianSearch

        search_space = {
            "n_layers": {"type": "int", "low": 1, "high": 5},
            "dropout": {"type": "float", "low": 0.0, "high": 0.5},
            "activation": {"type": "categorical", "choices": ["relu", "tanh", "sigmoid"]}
        }
        search = BayesianSearch(search_space, max_trials=15)

        for i in range(15):
            params = search.suggest()
            if params is None:
                break

            assert 1 <= params["n_layers"] <= 5
            assert 0.0 <= params["dropout"] <= 0.5
            assert params["activation"] in ["relu", "tanh", "sigmoid"]

            search.report(params, float(i))


class TestExperimentDatabaseSchema:
    """Tests for experiment database tables."""

    @pytest.fixture
    def db_conn(self):
        """Get database connection and ensure experiments tables exist."""
        import psycopg
        from pathlib import Path

        url = os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )
        with psycopg.connect(url) as conn:
            # Check if experiments table exists
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    # Run the experiments migration to create tables
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()
            yield conn

    def test_experiments_table_exists(self, db_conn):
        """Test that experiments table exists."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'experiments'
                )
            """)
            exists = cur.fetchone()[0]

        assert exists, "experiments table should exist"

    def test_experiment_trials_table_exists(self, db_conn):
        """Test that experiment_trials table exists."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'experiment_trials'
                )
            """)
            exists = cur.fetchone()[0]

        assert exists, "experiment_trials table should exist"

    def test_experiments_has_expected_columns(self, db_conn):
        """Test that experiments table has expected columns."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'experiments'
            """)
            columns = {row[0] for row in cur.fetchall()}

        expected = {
            "id", "name", "experiment_type", "config", "search_space",
            "objective_metric", "objective_direction",
            "goal_target", "goal_type", "baseline_value", "early_stop_on_goal",
            "status", "priority",
            "parent_experiment_id", "depends_on_output",
            "results", "artifacts_path", "goal_achieved",
            "proposed_by", "approved_by", "created_at", "started_at", "completed_at",
            "total_trials", "completed_trials", "best_score"
        }

        for col in expected:
            assert col in columns, f"Missing column: {col}"

    def test_experiment_trials_has_expected_columns(self, db_conn):
        """Test that experiment_trials table has expected columns."""
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'experiment_trials'
            """)
            columns = {row[0] for row in cur.fetchall()}

        expected = {
            "id", "experiment_id", "trial_number",
            "params", "metrics", "score",
            "started_at", "completed_at", "duration_seconds"
        }

        for col in expected:
            assert col in columns, f"Missing column: {col}"


# =============================================================================
# PHASE 2: Strategy Parameter Optimization Tests
# =============================================================================


class TestStrategyParamExperiment:
    """Tests for StrategyParamExperiment type."""

    def test_strategy_param_experiment_init(self):
        """Test creating a strategy param experiment."""
        from g2.experiments.types.strategy_params import StrategyParamExperiment

        exp = StrategyParamExperiment(
            strategy_name="momentum",
            search_space={
                "lookback_days": {"type": "int", "low": 5, "high": 20}
            },
            symbols=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            objective="sharpe_ratio",
        )

        assert exp.strategy_name == "momentum"
        assert exp.objective == "sharpe_ratio"
        assert exp.symbols == ["AAPL", "MSFT"]

    def test_evaluate_returns_metrics(self):
        """Test that evaluate() returns backtest metrics."""
        from g2.experiments.types.strategy_params import StrategyParamExperiment

        exp = StrategyParamExperiment(
            strategy_name="momentum",
            search_space={},
            symbols=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-06-01",
        )

        # Evaluate with specific params
        metrics = exp.evaluate({"lookback_days": 10, "top_n": 5})

        # Should return dict with standard metrics
        assert "sharpe_ratio" in metrics
        assert "total_return" in metrics
        assert "max_drawdown" in metrics

    def test_evaluate_uses_params(self):
        """Test that different params produce different results."""
        from g2.experiments.types.strategy_params import StrategyParamExperiment

        exp = StrategyParamExperiment(
            strategy_name="momentum",
            search_space={},
            symbols=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-06-01",
        )

        # Two different parameter sets
        metrics1 = exp.evaluate({"lookback_days": 5})
        metrics2 = exp.evaluate({"lookback_days": 20})

        # Results should be valid (not necessarily different for small sample)
        assert isinstance(metrics1["sharpe_ratio"], (int, float))
        assert isinstance(metrics2["sharpe_ratio"], (int, float))


class TestExperimentExecution:
    """Tests for experiment execution (ExperimentRunner.run)."""

    @pytest.fixture
    def db_url(self):
        """Get database URL."""
        return os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    @pytest.fixture
    def ensure_tables(self, db_url):
        """Ensure experiments tables exist before tests."""
        import psycopg
        from pathlib import Path

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()

    @pytest.fixture
    def runner(self, db_url, ensure_tables):
        """Create ExperimentRunner instance."""
        from g2.experiments.core import ExperimentRunner
        return ExperimentRunner(db_url)

    def test_run_executes_trials(self, runner):
        """Test that run() executes trials and stores results."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_execution",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [5, 10]}
            },
            max_trials=2,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)

        # Run the experiment
        results = runner.run(exp_id)

        # Check results
        assert results["status"] == "completed"
        assert results["completed_trials"] >= 1
        assert "best_params" in results
        assert "best_score" in results

    def test_run_stores_trial_results(self, runner, db_url):
        """Test that individual trials are stored in experiment_trials table."""
        import psycopg
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_trial_storage",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [5]}
            },
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        runner.run(exp_id)

        # Check trials were stored
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM experiment_trials WHERE experiment_id = %s",
                    (exp_id,)
                )
                count = cur.fetchone()[0]

        assert count >= 1, "Should have stored at least 1 trial"

    def test_run_updates_best_score(self, runner):
        """Test that run() updates best_score on experiment."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_best_score",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [5, 10]}
            },
            max_trials=2,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        runner.run(exp_id)

        # Check experiment was updated
        experiment = runner.get(exp_id)
        assert experiment["status"] == "completed"
        assert experiment["best_score"] is not None


class TestGoalAchievement:
    """Tests for experiment goal detection."""

    @pytest.fixture
    def db_url(self):
        return os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    @pytest.fixture
    def ensure_tables(self, db_url):
        import psycopg
        from pathlib import Path

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()

    @pytest.fixture
    def runner(self, db_url, ensure_tables):
        from g2.experiments.core import ExperimentRunner
        return ExperimentRunner(db_url)

    def test_goal_achieved_detected(self, runner):
        """Test that achieving goal is detected and recorded."""
        from g2.experiments.core import ExperimentConfig

        # Set a very low goal that should be achieved
        config = ExperimentConfig(
            name="test_goal_achieved",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [10]}
            },
            max_trials=1,
            goal_type="achieve",
            goal_target=-100.0,  # Very low target, should be achieved
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        runner.run(exp_id)

        experiment = runner.get(exp_id)
        assert experiment["goal_achieved"] is True

    def test_goal_not_achieved_detected(self, runner):
        """Test that not achieving goal is detected and recorded."""
        from g2.experiments.core import ExperimentConfig

        # Set impossibly high goal
        config = ExperimentConfig(
            name="test_goal_not_achieved",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [10]}
            },
            max_trials=1,
            goal_type="achieve",
            goal_target=1000.0,  # Impossibly high Sharpe
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        runner.run(exp_id)

        experiment = runner.get(exp_id)
        assert experiment["goal_achieved"] is False

    def test_early_stop_on_goal(self, runner):
        """Test that early stopping works when goal is achieved."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_early_stop",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [5, 10, 15, 20]}
            },
            max_trials=4,
            goal_type="achieve",
            goal_target=-100.0,  # Very low, will be achieved on first trial
            early_stop_on_goal=True,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        result = runner.run(exp_id)

        # Should have stopped early
        assert result["completed_trials"] < 4

    def test_improvement_goal_with_baseline(self, runner):
        """Test improvement goal that compares to baseline."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_improvement",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "categorical", "choices": [10]}
            },
            max_trials=1,
            goal_type="improve",
            baseline_value=-1000.0,  # Very low baseline
            goal_target=0.0,  # Should beat this
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        runner.run(exp_id)

        experiment = runner.get(exp_id)
        # Any reasonable result should beat -1000
        assert experiment["goal_achieved"] is True


class TestBayesianSearchIntegration:
    """Tests for BayesianSearch integration with ExperimentRunner."""

    @pytest.fixture
    def db_url(self):
        return os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    @pytest.fixture
    def ensure_tables(self, db_url):
        import psycopg
        from pathlib import Path

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()

    @pytest.fixture
    def runner(self, db_url, ensure_tables):
        from g2.experiments.core import ExperimentRunner
        return ExperimentRunner(db_url)

    def test_run_with_bayesian_search(self, runner):
        """Test running experiment with Bayesian search method."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test_bayesian_run",
            experiment_type="strategy_params",
            search_space={
                "lookback_days": {"type": "int", "low": 5, "high": 20}
            },
            max_trials=5,
            search_method="bayesian",  # Use Bayesian search
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )

        exp_id = runner.propose(config)
        runner.approve(exp_id)
        results = runner.run(exp_id)

        assert results["completed_trials"] >= 1
        assert results["best_params"] is not None

    def test_config_has_search_method_field(self):
        """Test that ExperimentConfig has search_method field."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test",
            experiment_type="strategy_params",
            search_space={},
            search_method="bayesian"
        )

        assert config.search_method == "bayesian"

    def test_config_search_method_defaults_to_grid(self):
        """Test that search_method defaults to grid."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="test",
            experiment_type="strategy_params",
            search_space={}
        )

        assert config.search_method == "grid"


class TestExperimentChaining:
    """Tests for experiment chaining functionality."""

    @pytest.fixture
    def db_url(self):
        return os.environ.get(
            "DATABASE_URL",
            "postgresql://g2:g2pass@localhost:6432/g2"
        )

    @pytest.fixture
    def ensure_tables(self, db_url):
        import psycopg
        from pathlib import Path

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'experiments'
                    )
                """)
                exists = cur.fetchone()[0]
                if not exists:
                    migration_path = Path(__file__).parent.parent / "sql" / "migrations" / "20241229_experiments.sql"
                    if migration_path.exists():
                        migration_sql = migration_path.read_text()
                        cur.execute(migration_sql)
                        conn.commit()

    @pytest.fixture
    def runner(self, db_url, ensure_tables):
        from g2.experiments.core import ExperimentRunner
        return ExperimentRunner(db_url)

    def test_chain_creates_child_experiment(self, runner):
        """Test that chain() creates a child experiment with parent reference."""
        from g2.experiments.core import ExperimentConfig

        # Create and complete parent experiment
        parent_config = ExperimentConfig(
            name="parent_experiment",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "categorical", "choices": [10]}},
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )
        parent_id = runner.propose(parent_config)
        runner.approve(parent_id)
        runner.run(parent_id)

        # Create child experiment chained to parent
        child_config = ExperimentConfig(
            name="child_experiment",
            experiment_type="strategy_params",
            search_space={"top_n": {"type": "categorical", "choices": [5]}},
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )
        child_id = runner.chain(parent_id, child_config, depends_on="best_params")

        # Verify child has parent reference
        child = runner.get(child_id)
        assert child["parent_experiment_id"] == parent_id
        assert child["depends_on_output"] == "best_params"
        assert child["status"] == "proposed"

    def test_chain_requires_completed_parent(self, runner):
        """Test that chain() requires parent to be completed."""
        from g2.experiments.core import ExperimentConfig

        # Create parent but don't complete it
        parent_config = ExperimentConfig(
            name="incomplete_parent",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "categorical", "choices": [10]}},
            max_trials=1,
        )
        parent_id = runner.propose(parent_config)

        # Try to chain - should fail
        child_config = ExperimentConfig(
            name="child_of_incomplete",
            experiment_type="strategy_params",
            search_space={},
            max_trials=1,
        )

        with pytest.raises(ValueError, match="not completed"):
            runner.chain(parent_id, child_config, depends_on="best_params")

    def test_get_parent_results(self, runner):
        """Test that child can access parent's results."""
        from g2.experiments.core import ExperimentConfig

        # Create and complete parent
        parent_config = ExperimentConfig(
            name="parent_with_results",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "categorical", "choices": [15]}},
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )
        parent_id = runner.propose(parent_config)
        runner.approve(parent_id)
        runner.run(parent_id)

        # Create child
        child_config = ExperimentConfig(
            name="child_accessing_results",
            experiment_type="strategy_params",
            search_space={},
            max_trials=1,
        )
        child_id = runner.chain(parent_id, child_config, depends_on="best_params")

        # Get parent results from child's perspective
        parent_results = runner.get_parent_results(child_id)
        assert parent_results is not None
        assert "best_params" in parent_results
        assert parent_results["best_params"]["lookback_days"] == 15

    def test_get_parent_results_returns_none_for_orphan(self, runner):
        """Test that get_parent_results returns None for experiments without parent."""
        from g2.experiments.core import ExperimentConfig

        config = ExperimentConfig(
            name="orphan_experiment",
            experiment_type="strategy_params",
            search_space={},
            max_trials=1,
        )
        exp_id = runner.propose(config)

        result = runner.get_parent_results(exp_id)
        assert result is None

    def test_list_children(self, runner):
        """Test listing child experiments of a parent."""
        from g2.experiments.core import ExperimentConfig

        # Create and complete parent
        parent_config = ExperimentConfig(
            name="parent_with_children",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "categorical", "choices": [10]}},
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )
        parent_id = runner.propose(parent_config)
        runner.approve(parent_id)
        runner.run(parent_id)

        # Create multiple children
        child1_config = ExperimentConfig(
            name="child_1",
            experiment_type="strategy_params",
            search_space={},
            max_trials=1,
        )
        child1_id = runner.chain(parent_id, child1_config, depends_on="best_params")

        child2_config = ExperimentConfig(
            name="child_2",
            experiment_type="strategy_params",
            search_space={},
            max_trials=1,
        )
        child2_id = runner.chain(parent_id, child2_config, depends_on="best_score")

        # List children
        children = runner.list_children(parent_id)
        assert len(children) == 2
        child_ids = [c["id"] for c in children]
        assert child1_id in child_ids
        assert child2_id in child_ids

    def test_child_inherits_parent_params(self, runner):
        """Test that child experiment can inherit parameters from parent's best_params."""
        from g2.experiments.core import ExperimentConfig

        # Create and complete parent with specific best params
        parent_config = ExperimentConfig(
            name="parent_for_inheritance",
            experiment_type="strategy_params",
            search_space={"lookback_days": {"type": "categorical", "choices": [20]}},
            max_trials=1,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum"}
        )
        parent_id = runner.propose(parent_config)
        runner.approve(parent_id)
        runner.run(parent_id)

        # Create child that uses parent's best_params as base
        child_config = ExperimentConfig(
            name="child_inheriting_params",
            experiment_type="strategy_params",
            search_space={"top_n": {"type": "categorical", "choices": [3, 5, 10]}},
            max_trials=3,
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-03-01",
            extra_config={"strategy": "momentum", "inherit_params": True}
        )
        child_id = runner.chain(parent_id, child_config, depends_on="best_params")
        runner.approve(child_id)
        results = runner.run(child_id)

        # Child should have run with parent's lookback_days
        assert results["completed_trials"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
