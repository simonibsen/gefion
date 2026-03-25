"""
TDD tests for hyperparameter tuning with Optuna.

These tests drive the implementation of automated hyperparameter optimization
using Bayesian search with time-series cross-validation.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

import gefion.cli as cli


runner = CliRunner()


class TestTuningCLI:
    """Tests for g2 ml tune CLI command."""

    def test_tune_command_exists(self):
        """Test that ml tune command exists."""
        result = runner.invoke(cli.app, ["ml", "tune", "--help"])
        assert result.exit_code == 0
        assert "tune" in result.output.lower() or "hyperparameter" in result.output.lower()

    def test_tune_requires_dataset_name(self):
        """Test that tune requires --dataset-name."""
        result = runner.invoke(cli.app, ["ml", "tune"])
        assert result.exit_code != 0
        assert "dataset-name" in result.output.lower() or "required" in result.output.lower()

    def test_tune_requires_dataset_version(self):
        """Test that tune requires --dataset-version."""
        result = runner.invoke(cli.app, ["ml", "tune", "--dataset-name", "test"])
        assert result.exit_code != 0
        assert "dataset-version" in result.output.lower() or "required" in result.output.lower()


class TestOptunaIntegration:
    """Tests for Optuna-based hyperparameter optimization."""

    @pytest.fixture
    def sample_data(self):
        """Create sample training data."""
        np.random.seed(42)
        n_samples = 200
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
            'feature_c': np.random.randn(n_samples),
        })
        # Target with some signal from feature_a
        y = X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5
        return X, y

    def test_create_optuna_study(self):
        """Test creating an Optuna study for hyperparameter tuning."""
        try:
            import optuna
        except ImportError:
            pytest.skip("Optuna not installed")

        from gefion.ml.tuning import create_study

        study = create_study(study_name="test_study", direction="minimize")

        assert study is not None
        assert study.study_name == "test_study"

    def test_tune_xgboost_quantile_model(self, sample_data):
        """Test tuning XGBoost quantile regression hyperparameters."""
        try:
            import optuna
            import xgboost as xgb
            xgb.XGBRegressor(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        from gefion.ml.tuning import tune_quantile_model

        X, y = sample_data

        result = tune_quantile_model(
            X=X,
            y=y,
            algorithm="xgboost",
            n_trials=5,  # Small number for testing
            quantile=0.5,
        )

        # Should return best parameters
        assert "best_params" in result
        assert "best_score" in result
        assert "n_trials" in result

        # Best params should include typical XGBoost hyperparameters
        best_params = result["best_params"]
        assert "n_estimators" in best_params or "max_depth" in best_params

    def test_tune_lightgbm_quantile_model(self, sample_data):
        """Test tuning LightGBM quantile regression hyperparameters."""
        try:
            import optuna
            import lightgbm as lgb
            lgb.LGBMRegressor(n_estimators=1, verbose=-1)
        except (ImportError, Exception) as e:
            pytest.skip(f"LightGBM not available: {e}")

        from gefion.ml.tuning import tune_quantile_model

        X, y = sample_data

        result = tune_quantile_model(
            X=X,
            y=y,
            algorithm="lightgbm",
            n_trials=5,
            quantile=0.5,
        )

        assert "best_params" in result
        assert "best_score" in result

    def test_tune_classifier(self, sample_data):
        """Test tuning classifier hyperparameters."""
        try:
            import optuna
            import xgboost as xgb
            xgb.XGBClassifier(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        from gefion.ml.tuning import tune_classifier

        X, _ = sample_data
        # Create classification target
        y = pd.Series(np.random.choice(
            ["strong_down", "weak_down", "flat", "weak_up", "strong_up"],
            size=len(X)
        ))

        result = tune_classifier(
            X=X,
            y=y,
            algorithm="xgboost",
            n_trials=5,
        )

        assert "best_params" in result
        assert "best_score" in result


class TestTimeSeriesCV:
    """Tests for time-series cross-validation in tuning."""

    @pytest.fixture
    def time_series_data(self):
        """Create time-series sample data with dates."""
        np.random.seed(42)
        n_samples = 100
        dates = pd.date_range("2024-01-01", periods=n_samples, freq="D")
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        }, index=dates)
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5, index=dates)
        return X, y

    def test_time_series_split_used(self, time_series_data):
        """Test that time-series split is used for CV (no data leakage)."""
        try:
            import optuna
        except ImportError:
            pytest.skip("Optuna not installed")

        from gefion.ml.tuning import create_time_series_cv

        X, y = time_series_data
        cv = create_time_series_cv(n_splits=3)

        # Verify it's a proper time series split
        splits = list(cv.split(X))
        assert len(splits) == 3

        # Each split should have train indices before test indices
        for train_idx, test_idx in splits:
            assert max(train_idx) < min(test_idx), "Train data must come before test data"

    def test_tune_with_time_series_cv(self, time_series_data):
        """Test tuning with time-series cross-validation."""
        try:
            import optuna
            from sklearn.ensemble import GradientBoostingRegressor
        except ImportError:
            pytest.skip("Required packages not installed")

        from gefion.ml.tuning import tune_quantile_model

        X, y = time_series_data

        result = tune_quantile_model(
            X=X,
            y=y,
            algorithm="sklearn",  # Use sklearn for faster test
            n_trials=3,
            quantile=0.5,
            cv_splits=3,
        )

        assert "best_params" in result
        assert result["cv_splits"] == 3


class TestTuningResults:
    """Tests for tuning result handling and persistence."""

    def test_save_tuning_results(self, tmp_path):
        """Test saving tuning results to file."""
        from gefion.ml.tuning import save_tuning_results

        results = {
            "best_params": {"n_estimators": 100, "max_depth": 5},
            "best_score": 0.123,
            "n_trials": 50,
            "algorithm": "xgboost",
        }

        output_path = tmp_path / "tuning_results.json"
        save_tuning_results(results, output_path)

        assert output_path.exists()

        with open(output_path) as f:
            saved = json.load(f)

        assert saved["best_params"]["n_estimators"] == 100
        assert saved["best_score"] == 0.123

    def test_load_tuning_results(self, tmp_path):
        """Test loading tuning results from file."""
        from gefion.ml.tuning import load_tuning_results, save_tuning_results

        results = {
            "best_params": {"n_estimators": 100},
            "best_score": 0.5,
        }

        output_path = tmp_path / "tuning_results.json"
        save_tuning_results(results, output_path)

        loaded = load_tuning_results(output_path)
        assert loaded["best_params"]["n_estimators"] == 100


class TestSearchSpace:
    """Tests for hyperparameter search space configuration."""

    def test_default_xgboost_search_space(self):
        """Test default search space for XGBoost."""
        from gefion.ml.tuning import get_search_space

        space = get_search_space("xgboost")

        # Should include key XGBoost hyperparameters
        assert "n_estimators" in space
        assert "max_depth" in space
        assert "learning_rate" in space

    def test_default_lightgbm_search_space(self):
        """Test default search space for LightGBM."""
        from gefion.ml.tuning import get_search_space

        space = get_search_space("lightgbm")

        assert "n_estimators" in space
        assert "max_depth" in space
        assert "learning_rate" in space

    def test_custom_search_space(self):
        """Test using custom search space."""
        try:
            import optuna
        except ImportError:
            pytest.skip("Optuna not installed")

        from gefion.ml.tuning import get_search_space

        custom_space = {
            "n_estimators": {"type": "int", "low": 50, "high": 100},
            "max_depth": {"type": "int", "low": 3, "high": 6},
        }

        space = get_search_space("xgboost", custom_space=custom_space)

        assert space["n_estimators"]["high"] == 100
        assert space["max_depth"]["high"] == 6


class TestPinballScoring:
    """Tests for pinball loss scoring in tuning."""

    def test_tune_supports_pinball_scoring(self, tmp_path):
        """tune_quantile_model should accept and use pinball scoring."""
        try:
            import optuna
        except ImportError:
            pytest.skip("Optuna not installed")

        from gefion.ml.tuning import tune_quantile_model

        np.random.seed(42)
        n_samples = 200
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5

        # Should accept scoring="pinball" (the new default)
        result = tune_quantile_model(
            X=X,
            y=y,
            algorithm="sklearn",
            n_trials=3,
            quantile=0.5,
            scoring="pinball",
        )

        assert "best_params" in result
        assert "best_score" in result
        assert result.get("scoring") == "pinball"
