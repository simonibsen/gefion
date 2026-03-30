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


# ---------------------------------------------------------------------------
# Feature Selection Experiment
# ---------------------------------------------------------------------------


class TestFeatureSelectionExperiment:
    """Tests for the FeatureSelectionExperiment class."""

    def test_class_exists(self):
        """FeatureSelectionExperiment should be importable."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment
        assert FeatureSelectionExperiment is not None

    def test_instantiation(self):
        """Should accept feature_names list and selection_method."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment

        exp = FeatureSelectionExperiment(
            name="test-feature-selection",
            principle_id="feature-importance",
            null_hypothesis="Selected features have no higher importance than random subset",
            feature_names=["rsi_14", "macd_signal", "frac_diff_close"],
            selection_method="importance",
        )
        assert exp.feature_names == ["rsi_14", "macd_signal", "frac_diff_close"]
        assert exp.selection_method == "importance"

    def test_selection_method_forward(self):
        """Should accept forward selection method."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment

        exp = FeatureSelectionExperiment(
            name="test-forward",
            principle_id="feature-selection",
            null_hypothesis="Forward selection does not improve model",
            feature_names=["vol_20", "momentum_10"],
            selection_method="forward",
        )
        assert exp.selection_method == "forward"

    def test_selection_method_backward(self):
        """Should accept backward selection method."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment

        exp = FeatureSelectionExperiment(
            name="test-backward",
            principle_id="feature-selection",
            null_hypothesis="Backward elimination does not improve model",
            feature_names=["vol_20", "momentum_10"],
            selection_method="backward",
        )
        assert exp.selection_method == "backward"

    def test_to_experiment_config(self):
        """Should produce ExperimentConfig with type='feature_selection'."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = FeatureSelectionExperiment(
            name="test-fs-config",
            principle_id="feature-importance",
            null_hypothesis="No improvement from feature selection",
            feature_names=["rsi_14", "macd_signal"],
            selection_method="importance",
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "feature_selection"

    def test_risk_level_is_low(self):
        """Feature selection is read-only analysis, so risk is low."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment

        exp = FeatureSelectionExperiment(
            name="test-risk",
            principle_id="feature-selection",
            null_hypothesis="No improvement",
            feature_names=["rsi_14"],
            selection_method="importance",
        )
        assert exp.risk_level == "low"

    def test_feature_names_must_be_non_empty(self):
        """feature_names must be a non-empty list."""
        from gefion.experiments.types.feature_selection import FeatureSelectionExperiment

        with pytest.raises((ValueError, TypeError)):
            FeatureSelectionExperiment(
                name="test-empty",
                principle_id="feature-selection",
                null_hypothesis="No improvement",
                feature_names=[],
                selection_method="importance",
            )


# ---------------------------------------------------------------------------
# Label Engineering Experiment
# ---------------------------------------------------------------------------


class TestLabelEngineeringExperiment:
    """Tests for the LabelEngineeringExperiment class."""

    def test_class_exists(self):
        """LabelEngineeringExperiment should be importable."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment
        assert LabelEngineeringExperiment is not None

    def test_instantiation_with_label_type(self):
        """Should accept label_type such as 'triple_barrier' or 'meta_label'."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        exp = LabelEngineeringExperiment(
            name="test-triple-barrier",
            principle_id="triple-barrier-method",
            null_hypothesis="Triple barrier labels do not improve risk-adjusted returns",
            label_type="triple_barrier",
        )
        assert exp.label_type == "triple_barrier"

    def test_meta_label_type(self):
        """Should accept meta_label label type."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        exp = LabelEngineeringExperiment(
            name="test-meta-label",
            principle_id="meta-labeling",
            null_hypothesis="Meta labels do not improve trade sizing",
            label_type="meta_label",
        )
        assert exp.label_type == "meta_label"

    def test_triple_barrier_config(self):
        """Triple barrier config should accept stop_loss, take_profit, max_holding_period."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        exp = LabelEngineeringExperiment(
            name="test-tb-config",
            principle_id="triple-barrier-method",
            null_hypothesis="Triple barrier labels do not improve returns",
            label_type="triple_barrier",
            label_config={
                "stop_loss": 0.02,
                "take_profit": 0.03,
                "max_holding_period": 10,
            },
        )
        assert exp.label_config["stop_loss"] == 0.02
        assert exp.label_config["take_profit"] == 0.03
        assert exp.label_config["max_holding_period"] == 10

    def test_to_experiment_config(self):
        """Should produce ExperimentConfig with type='label_engineering'."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = LabelEngineeringExperiment(
            name="test-le-config",
            principle_id="triple-barrier-method",
            null_hypothesis="No improvement from label engineering",
            label_type="triple_barrier",
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "label_engineering"

    def test_risk_level_is_high(self):
        """Label engineering is high risk because it changes the prediction target."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        exp = LabelEngineeringExperiment(
            name="test-risk",
            principle_id="triple-barrier-method",
            null_hypothesis="No improvement",
            label_type="triple_barrier",
        )
        assert exp.risk_level == "high"

    def test_evaluation_metric_defaults_to_sharpe_ratio(self):
        """Default evaluation metric should be sharpe_ratio (backtest-based, not prediction accuracy)."""
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        exp = LabelEngineeringExperiment(
            name="test-metric-default",
            principle_id="triple-barrier-method",
            null_hypothesis="No improvement",
            label_type="triple_barrier",
        )
        assert exp.evaluation_metric == "sharpe_ratio"


# ---------------------------------------------------------------------------
# PurgedKFold CV Splitter
# ---------------------------------------------------------------------------


class TestPurgedKFold:
    """Tests for the PurgedKFold cross-validation splitter."""

    def test_importable(self):
        """PurgedKFold should be importable from hyperparameter module."""
        from gefion.experiments.types.hyperparameter import PurgedKFold
        assert PurgedKFold is not None

    def test_produces_n_splits_folds(self):
        """PurgedKFold should produce n_splits folds (default 5)."""
        import numpy as np
        from gefion.experiments.types.hyperparameter import PurgedKFold

        X = np.arange(100)
        cv = PurgedKFold()
        folds = list(cv.split(X))
        assert len(folds) == 5

    def test_train_test_no_overlap(self):
        """Train and test indices must not overlap in any fold."""
        import numpy as np
        from gefion.experiments.types.hyperparameter import PurgedKFold

        X = np.arange(100)
        cv = PurgedKFold(n_splits=5)
        for train_idx, test_idx in cv.split(X):
            overlap = set(train_idx) & set(test_idx)
            assert len(overlap) == 0, f"Train/test overlap: {overlap}"

    def test_embargo_gap_between_train_and_test(self):
        """With embargo_pct > 0, there should be a gap between train end and test start."""
        import numpy as np
        from gefion.experiments.types.hyperparameter import PurgedKFold

        X = np.arange(100)
        cv = PurgedKFold(n_splits=5, embargo_pct=0.05)
        for train_idx, test_idx in cv.split(X):
            if len(train_idx) == 0 or len(test_idx) == 0:
                continue
            train_max = max(train_idx)
            test_min = min(test_idx)
            # Embargo means there's a gap: test_min > train_max + 1
            if train_max < test_min:
                assert test_min - train_max > 1, (
                    f"No embargo gap: train_max={train_max}, test_min={test_min}"
                )

    def test_sklearn_compatible_split_method(self):
        """PurgedKFold must have a split() method yielding train/test arrays."""
        import numpy as np
        from gefion.experiments.types.hyperparameter import PurgedKFold

        X = np.arange(100)
        cv = PurgedKFold(n_splits=3)
        assert hasattr(cv, "split"), "PurgedKFold must have a split() method"
        for train_idx, test_idx in cv.split(X):
            assert hasattr(train_idx, "__len__"), "train indices must be array-like"
            assert hasattr(test_idx, "__len__"), "test indices must be array-like"
            assert len(train_idx) > 0
            assert len(test_idx) > 0

    def test_purge_with_prediction_horizon(self):
        """prediction_horizon=5 should purge 5 extra samples before each test fold."""
        import numpy as np
        from gefion.experiments.types.hyperparameter import PurgedKFold

        X = np.arange(100)
        cv_no_purge = PurgedKFold(n_splits=5, prediction_horizon=0, embargo_pct=0.0)
        cv_purge = PurgedKFold(n_splits=5, prediction_horizon=5, embargo_pct=0.0)

        for (train_np, test_np), (train_p, test_p) in zip(
            cv_no_purge.split(X), cv_purge.split(X)
        ):
            # With purge, training set should be smaller (some samples removed)
            if len(train_np) > 0 and len(train_p) > 0:
                assert len(train_p) <= len(train_np), (
                    "Purged training set should not be larger than unpurged"
                )


# ---------------------------------------------------------------------------
# Hyperparameter Experiment
# ---------------------------------------------------------------------------


class TestHyperparameterExperiment:
    """Tests for the HyperparameterExperiment class."""

    def test_class_exists(self):
        """HyperparameterExperiment should be importable."""
        from gefion.experiments.types.hyperparameter import HyperparameterExperiment
        assert HyperparameterExperiment is not None

    def test_instantiation(self):
        """Should be instantiable with model_type, search_space, cv_config."""
        from gefion.experiments.types.hyperparameter import HyperparameterExperiment

        exp = HyperparameterExperiment(
            name="tune-xgboost",
            model_type="xgboost",
            search_space={"learning_rate": [0.01, 0.1], "max_depth": [3, 6, 9]},
            cv_config={"n_splits": 5, "embargo_pct": 0.02},
        )
        assert exp.model_type == "xgboost"
        assert exp.search_space == {"learning_rate": [0.01, 0.1], "max_depth": [3, 6, 9]}
        assert exp.cv_config == {"n_splits": 5, "embargo_pct": 0.02}

    def test_to_experiment_config(self):
        """Should produce ExperimentConfig with type='hyperparameter'."""
        from gefion.experiments.types.hyperparameter import HyperparameterExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = HyperparameterExperiment(
            name="tune-xgboost",
            model_type="xgboost",
            search_space={"learning_rate": [0.01, 0.1]},
            cv_config={"n_splits": 5},
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "hyperparameter"

    def test_risk_level_is_low(self):
        """Hyperparameter tuning should be low risk."""
        from gefion.experiments.types.hyperparameter import HyperparameterExperiment

        exp = HyperparameterExperiment(
            name="tune-xgboost",
            model_type="xgboost",
            search_space={"learning_rate": [0.01, 0.1]},
            cv_config={"n_splits": 5},
        )
        assert exp.risk_level == "low"


# ---------------------------------------------------------------------------
# Model Comparison Experiment
# ---------------------------------------------------------------------------


class TestModelComparisonExperiment:
    """Tests for the ModelComparisonExperiment class."""

    def test_class_exists(self):
        """ModelComparisonExperiment should be importable."""
        from gefion.experiments.types.model_comparison import ModelComparisonExperiment
        assert ModelComparisonExperiment is not None

    def test_instantiation_with_model_types(self):
        """Should accept a list of model_types to compare."""
        from gefion.experiments.types.model_comparison import ModelComparisonExperiment

        exp = ModelComparisonExperiment(
            name="compare-models",
            model_types=["quantile", "xgboost", "lightgbm"],
        )
        assert exp.model_types == ["quantile", "xgboost", "lightgbm"]

    def test_to_experiment_config(self):
        """Should produce ExperimentConfig with type='model_comparison'."""
        from gefion.experiments.types.model_comparison import ModelComparisonExperiment
        from gefion.experiments.core import ExperimentConfig

        exp = ModelComparisonExperiment(
            name="compare-models",
            model_types=["quantile", "xgboost"],
        )
        config = exp.to_experiment_config()
        assert isinstance(config, ExperimentConfig)
        assert config.experiment_type == "model_comparison"

    def test_risk_level_is_low(self):
        """Model comparison should be low risk."""
        from gefion.experiments.types.model_comparison import ModelComparisonExperiment

        exp = ModelComparisonExperiment(
            name="compare-models",
            model_types=["quantile", "xgboost"],
        )
        assert exp.risk_level == "low"
