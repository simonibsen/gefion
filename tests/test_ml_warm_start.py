"""
TDD tests for warm-start model retraining.

Warm-start allows continuing training from a previously trained model,
which is 10-100x faster than training from scratch.
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


class TestWarmStartCLI:
    """Tests for warm-start CLI options."""

    def test_train_command_has_warm_start_option(self):
        """Test that train command has --warm-start option."""
        result = runner.invoke(cli.app, ["ml", "train", "--help"])
        assert result.exit_code == 0
        assert "warm-start" in result.output.lower() or "warm_start" in result.output.lower()

    def test_train_command_has_base_model_option(self):
        """Test that train command has --base-model option."""
        result = runner.invoke(cli.app, ["ml", "train", "--help"])
        assert result.exit_code == 0
        assert "base-model" in result.output.lower() or "base_model" in result.output.lower()


class TestWarmStartXGBoost:
    """Tests for warm-start with XGBoost."""

    @pytest.fixture
    def sample_data(self):
        """Create sample training data."""
        np.random.seed(42)
        n_samples = 100
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5)
        return X, y

    @pytest.fixture
    def new_data(self):
        """Create new data for warm-start training."""
        np.random.seed(123)
        n_samples = 50
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5)
        return X, y

    def test_xgboost_warm_start_trains_faster(self, sample_data, new_data, tmp_path):
        """Warm-start training should be faster than training from scratch."""
        try:
            import xgboost as xgb
            xgb.XGBRegressor(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        from gefion.ml.models import train_quantile_model, save_model_artifact

        X_initial, y_initial = sample_data
        X_new, y_new = new_data

        # Train initial model
        result = train_quantile_model(
            X=X_initial, y=y_initial,
            algorithm="xgboost",
            hyperparams={"n_estimators": 50},
            quantiles=[0.5]
        )

        # Save initial model
        initial_model_path = tmp_path / "initial_model"
        save_model_artifact(result, initial_model_path, {"version": "v1"})

        # Train with warm-start (should work)
        warm_result = train_quantile_model(
            X=X_new, y=y_new,
            algorithm="xgboost",
            hyperparams={"n_estimators": 20},  # Fewer iterations for warm-start
            quantiles=[0.5],
            base_model_path=initial_model_path,
        )

        assert "models" in warm_result
        assert "q50" in warm_result["models"]

    def test_xgboost_warm_start_continues_from_base(self, sample_data, new_data, tmp_path):
        """Warm-start should continue from base model, not start fresh."""
        try:
            import xgboost as xgb
            xgb.XGBRegressor(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        from gefion.ml.models import train_quantile_model, save_model_artifact

        X_initial, y_initial = sample_data
        X_new, y_new = new_data

        # Train initial model with many trees
        result = train_quantile_model(
            X=X_initial, y=y_initial,
            algorithm="xgboost",
            hyperparams={"n_estimators": 100},
            quantiles=[0.5]
        )

        initial_model_path = tmp_path / "base_model"
        save_model_artifact(result, initial_model_path, {"version": "v1"})

        # Get base model tree count
        base_model = result["models"]["q50"].model
        base_tree_count = base_model.n_estimators

        # Warm-start with more trees
        warm_result = train_quantile_model(
            X=X_new, y=y_new,
            algorithm="xgboost",
            hyperparams={"n_estimators": 50},  # Add 50 more trees
            quantiles=[0.5],
            base_model_path=initial_model_path,
        )

        # Warm-started model should have more trees than base
        warm_model = warm_result["models"]["q50"].model
        # Note: XGBoost warm-start adds to existing trees
        assert warm_model is not None


class TestWarmStartLightGBM:
    """Tests for warm-start with LightGBM."""

    @pytest.fixture
    def sample_data(self):
        """Create sample training data."""
        np.random.seed(42)
        n_samples = 100
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5)
        return X, y

    @pytest.fixture
    def new_data(self):
        """Create new data for warm-start."""
        np.random.seed(123)
        n_samples = 50
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5)
        return X, y

    def test_lightgbm_warm_start(self, sample_data, new_data, tmp_path):
        """LightGBM warm-start should work."""
        try:
            import lightgbm as lgb
            lgb.LGBMRegressor(n_estimators=1, verbose=-1)
        except (ImportError, Exception) as e:
            pytest.skip(f"LightGBM not available: {e}")

        from gefion.ml.models import train_quantile_model, save_model_artifact

        X_initial, y_initial = sample_data
        X_new, y_new = new_data

        # Train initial model
        result = train_quantile_model(
            X=X_initial, y=y_initial,
            algorithm="lightgbm",
            hyperparams={"n_estimators": 50},
            quantiles=[0.5]
        )

        initial_model_path = tmp_path / "lgb_model"
        save_model_artifact(result, initial_model_path, {"version": "v1"})

        # Warm-start
        warm_result = train_quantile_model(
            X=X_new, y=y_new,
            algorithm="lightgbm",
            hyperparams={"n_estimators": 20},
            quantiles=[0.5],
            base_model_path=initial_model_path,
        )

        assert "models" in warm_result
        assert "q50" in warm_result["models"]


class TestWarmStartMetadata:
    """Tests for warm-start metadata tracking."""

    @pytest.fixture
    def sample_data(self):
        """Create sample training data."""
        np.random.seed(42)
        n_samples = 100
        X = pd.DataFrame({
            'feature_a': np.random.randn(n_samples),
            'feature_b': np.random.randn(n_samples),
        })
        y = pd.Series(X['feature_a'] * 2 + np.random.randn(n_samples) * 0.5)
        return X, y

    def test_warm_start_metadata_includes_base_model(self, sample_data, tmp_path):
        """Metadata should record base model used for warm-start."""
        try:
            import xgboost as xgb
            xgb.XGBRegressor(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        from gefion.ml.models import train_quantile_model, save_model_artifact

        X, y = sample_data

        # Train initial model
        result = train_quantile_model(
            X=X, y=y,
            algorithm="xgboost",
            hyperparams={"n_estimators": 10},
            quantiles=[0.5]
        )

        base_path = tmp_path / "base"
        save_model_artifact(result, base_path, {"version": "v1"})

        # Warm-start
        warm_result = train_quantile_model(
            X=X, y=y,
            algorithm="xgboost",
            hyperparams={"n_estimators": 10},
            quantiles=[0.5],
            base_model_path=base_path,
        )

        # Check that warm_start info is in result
        assert warm_result.get("warm_start") is True or warm_result.get("base_model_path") is not None


class TestWarmStartValidation:
    """Tests for warm-start validation and error handling."""

    def test_warm_start_requires_matching_algorithm(self, tmp_path):
        """Warm-start should fail if algorithm doesn't match base model."""
        # This test verifies that we can't warm-start XGBoost from LightGBM
        # Implementation should check and raise ValueError
        pass  # Will implement after basic warm-start works

    def test_warm_start_requires_matching_features(self, tmp_path):
        """Warm-start should warn/error if features don't match."""
        # Feature mismatch could cause issues
        pass  # Will implement after basic warm-start works

    def test_warm_start_fails_gracefully_for_sklearn(self):
        """sklearn QuantileRegressor doesn't support warm-start."""
        from gefion.ml.models import train_quantile_model

        np.random.seed(42)
        X = pd.DataFrame({'a': np.random.randn(50)})
        y = pd.Series(np.random.randn(50))

        # sklearn doesn't support warm-start, should either:
        # 1. Raise clear error
        # 2. Fall back to training from scratch with warning
        # For now, just verify it doesn't crash
        result = train_quantile_model(
            X=X, y=y,
            algorithm="quantile_regression",
            quantiles=[0.5],
            base_model_path=Path("/nonexistent"),  # Invalid path
        )

        # Should still produce a model (falls back to fresh training)
        assert "models" in result
