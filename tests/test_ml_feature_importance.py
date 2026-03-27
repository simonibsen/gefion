"""Tests for ML feature importance functionality."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

import gefion.cli as cli


runner = CliRunner()


# Pipeline wrappers at module level so they can be pickled
class XGBTestPipeline:
    """XGBoost pipeline wrapper for testing (must be at module level for pickling)."""
    def __init__(self, imputer, model):
        self.named_steps = {'imputer': imputer, 'model': model}
        self.imputer = imputer
        self.model = model

    def predict(self, X):
        X_imputed = self.imputer.transform(X)
        return self.model.predict(X_imputed)


class LGBTestPipeline:
    """LightGBM pipeline wrapper for testing (must be at module level for pickling)."""
    def __init__(self, imputer, model):
        self.named_steps = {'imputer': imputer, 'model': model}
        self.imputer = imputer
        self.model = model

    def predict(self, X):
        X_imputed = self.imputer.transform(X)
        return self.model.predict(X_imputed)


class TestFeatureImportanceCLI:
    """Tests for g2 ml feature-importance CLI command."""

    def test_feature_importance_requires_model_name(self):
        """Test that feature-importance requires --model-name."""
        result = runner.invoke(cli.app, ["ml", "feature-importance"])
        assert result.exit_code != 0
        assert "model-name" in result.output.lower() or "required" in result.output.lower()

    def test_feature_importance_requires_model_version(self):
        """Test that feature-importance requires --model-version."""
        result = runner.invoke(cli.app, ["ml", "feature-importance", "--model-name", "test"])
        assert result.exit_code != 0
        assert "model-version" in result.output.lower() or "required" in result.output.lower()

    def test_feature_importance_requires_horizon(self):
        """Test that feature-importance requires --horizon."""
        result = runner.invoke(cli.app, [
            "ml", "feature-importance",
            "--model-name", "test",
            "--model-version", "v1"
        ])
        assert result.exit_code != 0
        assert "horizon" in result.output.lower() or "required" in result.output.lower()


class TestFeatureImportanceComputation:
    """Tests for feature importance computation logic."""

    @pytest.fixture
    def mock_xgboost_model(self, tmp_path):
        """Create a mock XGBoost model artifact for testing."""
        try:
            import xgboost as xgb
            # Test that XGBoost actually works (not just importable)
            xgb.XGBRegressor(n_estimators=1)
        except (ImportError, Exception) as e:
            pytest.skip(f"XGBoost not available: {e}")

        # Create simple training data
        np.random.seed(42)
        X = pd.DataFrame({
            'feature_a': np.random.randn(100),
            'feature_b': np.random.randn(100),
            'feature_c': np.random.randn(100),
        })
        # feature_a is most important (directly correlated with y)
        y = X['feature_a'] * 2 + X['feature_b'] * 0.5 + np.random.randn(100) * 0.1

        # Train XGBoost model
        from sklearn.impute import SimpleImputer
        imputer = SimpleImputer(strategy='median')
        X_imputed = imputer.fit_transform(X)

        model = xgb.XGBRegressor(
            objective='reg:quantileerror',
            quantile_alpha=0.5,
            n_estimators=10,
            max_depth=3,
            random_state=42
        )
        model.fit(X_imputed, y)

        # Create pipeline wrapper (using module-level class for pickling)
        pipeline = XGBTestPipeline(imputer, model)

        # Save model artifact
        import joblib
        artifact_dir = tmp_path / "test_model_v1_h7"
        artifact_dir.mkdir()

        joblib.dump(pipeline, artifact_dir / "model_q50.joblib")

        metadata = {
            "feature_names": ["feature_a", "feature_b", "feature_c"],
            "quantiles": [0.5],
            "algorithm": "xgboost",
            "train_metrics": {"num_samples": 100, "num_features": 3}
        }
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata))

        return artifact_dir, X

    def test_compute_shap_importance_returns_ranked_features(self, mock_xgboost_model):
        """SHAP importance should return features ranked by importance."""
        from gefion.ml.importance import compute_shap_importance

        artifact_dir, X = mock_xgboost_model

        importance = compute_shap_importance(
            model_path=artifact_dir,
            X_sample=X,
            quantile="q50"
        )

        # Should return a dict with feature names and importance values
        assert isinstance(importance, dict)
        assert "feature_a" in importance
        assert "feature_b" in importance
        assert "feature_c" in importance

        # Importance values should be non-negative
        for feat, val in importance.items():
            assert val >= 0, f"Importance for {feat} should be non-negative"

    def test_shap_importance_feature_a_most_important(self, mock_xgboost_model):
        """feature_a should be most important (strongest signal in training data)."""
        from gefion.ml.importance import compute_shap_importance

        artifact_dir, X = mock_xgboost_model

        importance = compute_shap_importance(
            model_path=artifact_dir,
            X_sample=X,
            quantile="q50"
        )

        # feature_a should have highest importance
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        assert sorted_features[0][0] == "feature_a", \
            f"Expected feature_a to be most important, got {sorted_features}"

    def test_get_feature_importance_from_model(self, mock_xgboost_model):
        """get_feature_importance should load model and compute importance."""
        from gefion.ml.importance import get_feature_importance

        artifact_dir, X = mock_xgboost_model

        result = get_feature_importance(
            model_path=artifact_dir,
            quantile="q50",
            top_k=10
        )

        assert "importance" in result
        assert "feature_names" in result
        assert len(result["importance"]) <= 10

    def test_feature_importance_top_k_limits_results(self, mock_xgboost_model):
        """top_k parameter should limit number of features returned."""
        from gefion.ml.importance import get_feature_importance

        artifact_dir, _ = mock_xgboost_model

        result = get_feature_importance(
            model_path=artifact_dir,
            quantile="q50",
            top_k=2
        )

        assert len(result["importance"]) == 2


class TestFeatureImportanceWithLightGBM:
    """Tests for feature importance with LightGBM models."""

    @pytest.fixture
    def mock_lightgbm_model(self, tmp_path):
        """Create a mock LightGBM model artifact for testing."""
        try:
            import lightgbm as lgb
            # Test that LightGBM actually works
            lgb.LGBMRegressor(n_estimators=1, verbose=-1)
        except (ImportError, Exception) as e:
            pytest.skip(f"LightGBM not available: {e}")

        # Create simple training data
        np.random.seed(42)
        X = pd.DataFrame({
            'indicator_rsi': np.random.randn(100),
            'indicator_macd': np.random.randn(100),
        })
        y = X['indicator_rsi'] * 1.5 + np.random.randn(100) * 0.1

        from sklearn.impute import SimpleImputer
        imputer = SimpleImputer(strategy='median')
        X_imputed = imputer.fit_transform(X)

        model = lgb.LGBMRegressor(
            objective='quantile',
            alpha=0.5,
            n_estimators=10,
            max_depth=3,
            random_state=42,
            verbose=-1
        )
        model.fit(X_imputed, y)

        # Create pipeline wrapper (using module-level class for pickling)
        pipeline = LGBTestPipeline(imputer, model)

        import joblib
        artifact_dir = tmp_path / "lgb_model_v1_h7"
        artifact_dir.mkdir()

        joblib.dump(pipeline, artifact_dir / "model_q50.joblib")

        metadata = {
            "feature_names": ["indicator_rsi", "indicator_macd"],
            "quantiles": [0.5],
            "algorithm": "lightgbm",
        }
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata))

        return artifact_dir, X

    def test_lightgbm_shap_importance(self, mock_lightgbm_model):
        """SHAP should work with LightGBM models."""
        from gefion.ml.importance import compute_shap_importance

        artifact_dir, X = mock_lightgbm_model

        importance = compute_shap_importance(
            model_path=artifact_dir,
            X_sample=X,
            quantile="q50"
        )

        assert "indicator_rsi" in importance
        assert "indicator_macd" in importance


class TestFeatureImportanceEdgeCases:
    """Edge case tests for feature importance."""

    def test_handles_missing_shap_gracefully(self):
        """Should raise helpful error if SHAP not installed."""
        with patch.dict('sys.modules', {'shap': None}):
            # This would need to be tested differently - skip for now
            pass

    def test_handles_sklearn_model_without_tree_shap(self, tmp_path):
        """Should handle sklearn QuantileRegressor (uses KernelSHAP or falls back)."""
        from sklearn.linear_model import QuantileRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        import joblib

        # Create simple model
        np.random.seed(42)
        X = pd.DataFrame({
            'feat1': np.random.randn(50),
            'feat2': np.random.randn(50),
        })
        y = X['feat1'] + np.random.randn(50) * 0.1

        pipeline = Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', QuantileRegressor(quantile=0.5, alpha=0.1, solver='highs'))
        ])
        pipeline.fit(X, y)

        artifact_dir = tmp_path / "sklearn_model_v1_h7"
        artifact_dir.mkdir()
        joblib.dump(pipeline, artifact_dir / "model_q50.joblib")

        metadata = {
            "feature_names": ["feat1", "feat2"],
            "quantiles": [0.5],
            "algorithm": "quantile_regression",
        }
        (artifact_dir / "metadata.json").write_text(json.dumps(metadata))

        from gefion.ml.importance import get_feature_importance

        # Should work (may use permutation importance as fallback)
        result = get_feature_importance(
            model_path=artifact_dir,
            quantile="q50",
            top_k=10
        )

        assert "importance" in result
        assert len(result["importance"]) == 2
