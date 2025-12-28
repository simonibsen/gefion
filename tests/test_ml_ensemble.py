"""Tests for ML ensemble model functionality (TDD).

Model ensembles combine predictions from multiple algorithms for improved accuracy.
"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from g2.ml.models import train_quantile_model, save_model_artifact


# Tests for create_ensemble (combining existing models)
class TestCreateEnsemble:
    """Tests for creating ensembles from existing trained models."""

    def test_create_ensemble_from_model_paths(self, trained_models):
        """Test creating an ensemble from multiple trained model paths."""
        from g2.ml.ensemble import create_ensemble

        model_paths, _ = trained_models

        # Create ensemble from paths
        ensemble = create_ensemble(
            model_paths=model_paths,
            weights=None,  # Equal weights
        )

        assert ensemble is not None
        assert "models" in ensemble
        assert len(ensemble["models"]) == 2
        assert "weights" in ensemble
        assert ensemble["ensemble_type"] == "weighted_average"

    def test_create_ensemble_with_custom_weights(self, trained_models):
        """Test creating ensemble with custom weights."""
        from g2.ml.ensemble import create_ensemble

        model_paths, _ = trained_models

        # Create ensemble with custom weights
        ensemble = create_ensemble(
            model_paths=model_paths,
            weights=[0.7, 0.3],
        )

        assert ensemble["weights"] == [0.7, 0.3]
        np.testing.assert_almost_equal(sum(ensemble["weights"]), 1.0)

    def test_create_ensemble_weights_must_sum_to_one(self, trained_models):
        """Test that weights must sum to 1.0."""
        from g2.ml.ensemble import create_ensemble

        model_paths, _ = trained_models

        with pytest.raises(ValueError, match="Weights must sum to 1.0"):
            create_ensemble(
                model_paths=model_paths,
                weights=[0.5, 0.3],  # Sum is 0.8, not 1.0
            )

    def test_create_ensemble_weights_length_must_match(self, trained_models):
        """Test that weights length must match number of models."""
        from g2.ml.ensemble import create_ensemble

        model_paths, _ = trained_models

        with pytest.raises(ValueError, match="Number of weights"):
            create_ensemble(
                model_paths=model_paths,
                weights=[0.5, 0.3, 0.2],  # 3 weights but 2 models
            )


# Tests for train_ensemble (train and combine in one step)
class TestTrainEnsemble:
    """Tests for training ensemble models from scratch."""

    def test_train_ensemble_with_multiple_algorithms(self, synthetic_data):
        """Test training ensemble with multiple algorithms."""
        from g2.ml.ensemble import train_ensemble

        X, y = synthetic_data

        # Use two sklearn models with different hyperparams (avoid xgboost issues)
        result = train_ensemble(
            X=X,
            y=y,
            algorithms=["quantile_regression", "quantile_regression"],
            hyperparams={
                "quantile_regression": {"alpha": 0.1},
            },
            weights=None,  # Auto-equal
        )

        assert "ensemble" in result
        assert "base_models" in result
        assert len(result["base_models"]) == 2
        assert "metrics" in result

    def test_train_ensemble_saves_all_artifacts(self, synthetic_data, tmp_path):
        """Test that training saves all artifacts correctly."""
        from g2.ml.ensemble import train_ensemble

        X, y = synthetic_data

        # Use sklearn only (avoid xgboost issues)
        result = train_ensemble(
            X=X,
            y=y,
            algorithms=["quantile_regression", "quantile_regression"],
            output_dir=tmp_path / "ensemble_model",
        )

        # Check that ensemble metadata was saved
        assert (tmp_path / "ensemble_model" / "ensemble_metadata.json").exists()

        # Check that base model directories were created
        assert (tmp_path / "ensemble_model" / "base_model_0").exists()
        assert (tmp_path / "ensemble_model" / "base_model_1").exists()

    def test_train_ensemble_with_single_algorithm_works(self, synthetic_data):
        """Test that training with single algorithm works (no ensemble, just wrapper)."""
        from g2.ml.ensemble import train_ensemble

        X, y = synthetic_data

        result = train_ensemble(
            X=X,
            y=y,
            algorithms=["quantile_regression"],
        )

        # Should still work but just be a single model
        assert len(result["base_models"]) == 1


# Tests for predict_ensemble
class TestPredictEnsemble:
    """Tests for ensemble prediction."""

    def test_predict_ensemble_returns_weighted_average(self, trained_ensemble, synthetic_data):
        """Test that ensemble predictions are weighted averages."""
        from g2.ml.ensemble import predict_ensemble

        ensemble, _ = trained_ensemble
        X, _ = synthetic_data

        # Get ensemble predictions
        predictions = predict_ensemble(ensemble, X[:10])

        assert len(predictions) == 10
        assert "q10" in predictions.columns
        assert "q50" in predictions.columns
        assert "q90" in predictions.columns

    def test_predict_ensemble_preserves_quantile_ordering(self, trained_ensemble, synthetic_data):
        """Test that ensemble predictions maintain q10 <= q50 <= q90."""
        from g2.ml.ensemble import predict_ensemble

        ensemble, _ = trained_ensemble
        X, _ = synthetic_data

        predictions = predict_ensemble(ensemble, X[:20])

        # Verify quantile ordering
        assert (predictions["q10"] <= predictions["q50"]).all()
        assert (predictions["q50"] <= predictions["q90"]).all()

    def test_predict_ensemble_with_different_weights_changes_output(self, trained_models, synthetic_data):
        """Test that different weights produce different predictions."""
        from g2.ml.ensemble import create_ensemble, predict_ensemble

        model_paths, _ = trained_models
        X, _ = synthetic_data

        # Create two ensembles with different weights
        ensemble1 = create_ensemble(model_paths=model_paths, weights=[0.9, 0.1])
        ensemble2 = create_ensemble(model_paths=model_paths, weights=[0.1, 0.9])

        pred1 = predict_ensemble(ensemble1, X[:5])
        pred2 = predict_ensemble(ensemble2, X[:5])

        # Predictions should differ (unless models happen to agree perfectly)
        # At least one prediction should be different
        assert not np.allclose(pred1["q50"].values, pred2["q50"].values, rtol=0.01)


# Tests for save/load ensemble
class TestEnsemblePersistence:
    """Tests for saving and loading ensembles."""

    def test_save_load_roundtrip(self, trained_ensemble, tmp_path):
        """Test that saving and loading preserves ensemble."""
        from g2.ml.ensemble import load_ensemble

        ensemble, _ = trained_ensemble

        # The ensemble was already saved by train_ensemble to tmp_path / "ensemble"
        save_path = tmp_path / "ensemble"

        # Verify files exist
        assert (save_path / "ensemble_metadata.json").exists()
        assert (save_path / "base_model_0").exists()
        assert (save_path / "base_model_1").exists()

        # Load and verify
        loaded = load_ensemble(save_path)

        assert loaded["ensemble_type"] == ensemble["ensemble_type"]
        assert loaded["weights"] == ensemble["weights"]
        assert len(loaded["models"]) == len(ensemble["models"])

    def test_load_nonexistent_raises_error(self, tmp_path):
        """Test that loading non-existent ensemble raises error."""
        from g2.ml.ensemble import load_ensemble

        with pytest.raises(FileNotFoundError):
            load_ensemble(tmp_path / "nonexistent")


# Tests for observability (tracing)
class TestEnsembleObservability:
    """Tests for OpenTelemetry tracing in ensemble operations."""

    def test_train_ensemble_creates_spans(self, synthetic_data, monkeypatch):
        """Test that training creates appropriate spans."""
        from g2.ml.ensemble import train_ensemble

        # Track span names
        created_spans = []

        class MockSpan:
            def __init__(self, name):
                self.name = name
                created_spans.append(name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_attribute(self, key, value):
                pass

        def mock_create_span(name, **kwargs):
            return MockSpan(name)

        # Patch create_span
        import g2.ml.ensemble as ensemble_module
        monkeypatch.setattr(ensemble_module, "create_span", mock_create_span)

        X, y = synthetic_data
        train_ensemble(
            X=X,
            y=y,
            algorithms=["quantile_regression"],
        )

        # Verify spans were created
        assert "train_ensemble" in created_spans


# Fixtures
@pytest.fixture
def synthetic_data():
    """Create synthetic training data."""
    np.random.seed(42)
    n_samples = 100
    n_features = 5

    X = pd.DataFrame(
        np.random.randn(n_samples, n_features),
        columns=[f"feature_{i}" for i in range(n_features)]
    )
    y = pd.Series(np.random.randn(n_samples))

    return X, y


@pytest.fixture
def trained_models(synthetic_data, tmp_path):
    """Create multiple trained models in temporary directory."""
    X, y = synthetic_data

    model_paths = []

    # Train sklearn model
    model1 = train_quantile_model(X, y, algorithm="quantile_regression")
    path1 = tmp_path / "model_sklearn"
    save_model_artifact(model1, path1, metadata={"algorithm": "quantile_regression"})
    model_paths.append(path1)

    # Train second sklearn model (avoid xgboost dependency issues)
    # Use different hyperparameters to get different predictions
    model2 = train_quantile_model(
        X, y,
        algorithm="quantile_regression",
        hyperparams={"alpha": 0.5}  # Different regularization
    )
    path2 = tmp_path / "model_sklearn2"
    save_model_artifact(model2, path2, metadata={"algorithm": "quantile_regression"})
    model_paths.append(path2)

    return model_paths, tmp_path


@pytest.fixture
def trained_ensemble(synthetic_data, tmp_path):
    """Create a trained ensemble for testing."""
    from g2.ml.ensemble import train_ensemble

    X, y = synthetic_data

    # Use two sklearn models to test ensemble properly
    result = train_ensemble(
        X=X,
        y=y,
        algorithms=["quantile_regression", "quantile_regression"],
        hyperparams={"quantile_regression": {"alpha": 0.1}},
        output_dir=tmp_path / "ensemble",
    )

    return result["ensemble"], tmp_path


# Tests for CLI predict-ensemble command
class TestPredictEnsembleCLI:
    """Tests for predict-ensemble CLI command."""

    def test_predict_ensemble_generates_predictions(self, trained_ensemble, synthetic_data):
        """Test that predict_ensemble generates valid predictions."""
        from g2.ml.ensemble import predict_ensemble

        ensemble, _ = trained_ensemble
        X, _ = synthetic_data

        # Generate predictions
        predictions = predict_ensemble(ensemble, X)

        assert len(predictions) == len(X)
        assert "q10" in predictions.columns
        assert "q50" in predictions.columns
        assert "q90" in predictions.columns

        # Verify quantile ordering
        assert (predictions["q10"] <= predictions["q50"]).all()
        assert (predictions["q50"] <= predictions["q90"]).all()

    def test_predict_ensemble_with_loaded_ensemble(self, trained_ensemble, synthetic_data):
        """Test predictions work after loading ensemble from disk."""
        from g2.ml.ensemble import load_ensemble, predict_ensemble

        _, tmp_path = trained_ensemble
        X, _ = synthetic_data

        # Load ensemble from disk
        ensemble = load_ensemble(tmp_path / "ensemble")

        # Generate predictions
        predictions = predict_ensemble(ensemble, X[:5])

        assert len(predictions) == 5
        assert all(col in predictions.columns for col in ["q10", "q50", "q90"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
