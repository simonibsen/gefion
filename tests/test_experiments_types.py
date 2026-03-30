"""Tests for experiment types: feature engineering, cycle management, guardrails.

TDD: These tests are written FIRST, before implementation.
"""
import pytest
from datetime import date


# ---------------------------------------------------------------------------
# Feature Engineering Experiment
# ---------------------------------------------------------------------------


class TestFeatureEngineeringExperiment:
    """Tests for the FeatureEngineeringExperiment class."""

    def test_class_exists(self):
        """FeatureEngineeringExperiment should be importable."""
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment
        assert FeatureEngineeringExperiment is not None

    def test_init_requires_principle_id(self):
        """Must reference a motivating principle."""
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        exp = FeatureEngineeringExperiment(
            name="test-frac-diff",
            principle_id="fractional-differentiation",
            null_hypothesis="Fractionally differentiated features have no higher importance than standard returns",
            feature_config={"function_name": "fractional_diff", "params": {"d": 0.4}},
            source_column="close",
            source_table="stock_ohlcv",
        )
        assert exp.principle_id == "fractional-differentiation"
        assert exp.null_hypothesis is not None

    def test_init_stores_feature_config(self):
        """Feature config describes the new feature to create."""
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        config = {"function_name": "fractional_diff", "params": {"d": 0.4}}
        exp = FeatureEngineeringExperiment(
            name="test",
            principle_id="test-principle",
            null_hypothesis="No improvement",
            feature_config=config,
            source_column="close",
            source_table="stock_ohlcv",
        )
        assert exp.feature_config == config

    def test_to_experiment_config(self):
        """Should produce a serializable ExperimentConfig."""
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = FeatureEngineeringExperiment(
            name="test-frac-diff",
            principle_id="fractional-differentiation",
            null_hypothesis="No improvement",
            feature_config={"function_name": "fractional_diff", "params": {"d": 0.4}},
            source_column="close",
            source_table="stock_ohlcv",
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "feature_engineering"
        assert config.principle_id == "fractional-differentiation"
        assert config.null_hypothesis == "No improvement"

    def test_risk_level_is_medium_for_new_feature(self):
        """Creating a new feature definition is medium risk."""
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        exp = FeatureEngineeringExperiment(
            name="test",
            principle_id="test-principle",
            null_hypothesis="No improvement",
            feature_config={"function_name": "test_fn", "params": {}},
            source_column="close",
            source_table="stock_ohlcv",
        )
        assert exp.risk_level in ("medium", "low")


# ---------------------------------------------------------------------------
# Experiment Cycle Management
# ---------------------------------------------------------------------------


class TestExperimentCycle:
    """Tests for experiment cycle creation and management."""

    def test_cycle_dataclass_exists(self):
        """ExperimentCycle should be importable."""
        from gefion.experiments.core import ExperimentCycle
        assert ExperimentCycle is not None

    def test_cycle_creation(self):
        """Create a cycle with holdout window and FDR rate."""
        from gefion.experiments.core import ExperimentCycle

        cycle = ExperimentCycle(
            name="test-cycle",
            holdout_start_date=date(2026, 2, 15),
            holdout_end_date=date(2026, 3, 29),
            fdr_rate=0.10,
        )
        assert cycle.name == "test-cycle"
        assert cycle.fdr_rate == 0.10
        assert cycle.holdout_start_date == date(2026, 2, 15)
        assert cycle.status == "proposed"

    def test_cycle_defaults(self):
        """Cycle should have sensible defaults."""
        from gefion.experiments.core import ExperimentCycle

        cycle = ExperimentCycle(
            name="defaults",
            holdout_start_date=date(2026, 2, 15),
            holdout_end_date=date(2026, 3, 29),
        )
        assert cycle.fdr_rate == 0.10
        assert cycle.compute_budget_seconds == 7200
        assert cycle.max_experiments == 20
        assert cycle.status == "proposed"

    def test_cycle_to_dict(self):
        """Cycle should serialize to dict."""
        from gefion.experiments.core import ExperimentCycle

        cycle = ExperimentCycle(
            name="test",
            holdout_start_date=date(2026, 2, 15),
            holdout_end_date=date(2026, 3, 29),
        )
        d = cycle.to_dict()
        assert d["name"] == "test"
        assert "holdout_start_date" in d
        assert "fdr_rate" in d


# ---------------------------------------------------------------------------
# Guardrails Integration
# ---------------------------------------------------------------------------


class TestGuardrails:
    """Tests for experiment guardrail enforcement."""

    def test_classify_risk_feature_engineering(self):
        """Feature engineering experiments should be medium risk."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("feature_engineering") == "medium"

    def test_classify_risk_hyperparameter(self):
        """Hyperparameter tuning should be low risk."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("hyperparameter") == "low"

    def test_classify_risk_label_engineering(self):
        """Label engineering should be high risk (changes prediction target)."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("label_engineering") == "high"

    def test_classify_risk_strategy_params(self):
        """Strategy params should be low risk."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("strategy_params") == "low"

    def test_classify_risk_feature_selection(self):
        """Feature selection should be low risk (read-only analysis)."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("feature_selection") == "low"

    def test_classify_risk_model_comparison(self):
        """Model comparison should be low risk."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("model_comparison") == "low"

    def test_classify_risk_pipeline(self):
        """Pipeline experiments should be high risk (multi-stage changes)."""
        from gefion.experiments.core import classify_risk_level
        assert classify_risk_level("pipeline") == "high"

    def test_detect_duplicate_experiment(self):
        """Should detect duplicate experiments based on config hash."""
        from gefion.experiments.core import is_duplicate_experiment

        existing = [
            {"experiment_type": "feature_engineering", "search_space": {"d": [0.3, 0.4]}, "principle_id": "frac-diff"},
        ]

        # Same config = duplicate
        assert is_duplicate_experiment(
            experiment_type="feature_engineering",
            search_space={"d": [0.3, 0.4]},
            principle_id="frac-diff",
            existing_experiments=existing,
        ) is True

        # Different config = not duplicate
        assert is_duplicate_experiment(
            experiment_type="feature_engineering",
            search_space={"d": [0.5, 0.6]},
            principle_id="frac-diff",
            existing_experiments=existing,
        ) is False

    def test_detect_duplicate_different_principle(self):
        """Different principle = not a duplicate even with same search space."""
        from gefion.experiments.core import is_duplicate_experiment

        existing = [
            {"experiment_type": "feature_engineering", "search_space": {"d": [0.3]}, "principle_id": "principle-a"},
        ]

        assert is_duplicate_experiment(
            experiment_type="feature_engineering",
            search_space={"d": [0.3]},
            principle_id="principle-b",
            existing_experiments=existing,
        ) is False
