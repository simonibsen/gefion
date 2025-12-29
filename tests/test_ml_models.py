"""Tests for ML model training and prediction functionality."""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from g2.ml.models import (
    train_quantile_model,
    save_model_artifact,
    load_model_artifact,
    predict_quantiles,
)


def test_train_quantile_model_sklearn():
    """Test training sklearn quantile regression models."""
    # Create synthetic training data
    np.random.seed(42)
    n_samples = 100
    n_features = 5

    X = pd.DataFrame(
        np.random.randn(n_samples, n_features),
        columns=[f"feature_{i}" for i in range(n_features)]
    )
    y = pd.Series(np.random.randn(n_samples))

    # Train models
    model_data = train_quantile_model(X, y, algorithm="quantile_regression")

    # Validate structure
    assert "models" in model_data
    assert "q10" in model_data["models"]
    assert "q50" in model_data["models"]
    assert "q90" in model_data["models"]
    assert "feature_names" in model_data
    assert "quantiles" in model_data
    assert model_data["quantiles"] == [0.1, 0.5, 0.9]
    assert model_data["algorithm"] == "quantile_regression"


def test_save_and_load_model_artifact():
    """Test model serialization round-trip."""
    # Create synthetic data
    np.random.seed(42)
    X = pd.DataFrame(np.random.randn(50, 3), columns=["f1", "f2", "f3"])
    y = pd.Series(np.random.randn(50))

    # Train model
    model_data = train_quantile_model(X, y)

    # Save to temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = Path(tmpdir) / "test_model"
        save_model_artifact(
            model_data,
            artifact_path,
            metadata={"test": "value", "horizon_days": 7}
        )

        # Verify files exist
        assert (artifact_path / "model_q10.joblib").exists()
        assert (artifact_path / "model_q50.joblib").exists()
        assert (artifact_path / "model_q90.joblib").exists()
        assert (artifact_path / "metadata.json").exists()
        assert (artifact_path / "training_log.txt").exists()

        # Load model back
        loaded_data = load_model_artifact(artifact_path)

        # Validate loaded data
        assert "models" in loaded_data
        assert "q10" in loaded_data["models"]
        assert "q50" in loaded_data["models"]
        assert "q90" in loaded_data["models"]
        assert loaded_data["feature_names"] == ["f1", "f2", "f3"]
        assert loaded_data["metadata"]["test"] == "value"
        assert loaded_data["metadata"]["horizon_days"] == 7


def test_predict_quantiles():
    """Test generating quantile predictions."""
    # Create and train model
    np.random.seed(42)
    X_train = pd.DataFrame(np.random.randn(100, 3), columns=["f1", "f2", "f3"])
    y_train = pd.Series(np.random.randn(100))

    model_data = train_quantile_model(X_train, y_train)

    # Create test data
    X_test = pd.DataFrame(np.random.randn(10, 3), columns=["f1", "f2", "f3"])

    # Generate predictions
    predictions = predict_quantiles(model_data, X_test)

    # Validate predictions
    assert len(predictions) == 10
    assert "q10" in predictions.columns
    assert "q50" in predictions.columns
    assert "q90" in predictions.columns

    # Verify quantile ordering: q10 <= q50 <= q90
    assert (predictions["q10"] <= predictions["q50"]).all()
    assert (predictions["q50"] <= predictions["q90"]).all()


def test_predict_quantiles_missing_features():
    """Test prediction with missing features (should be handled by imputer)."""
    # Train with 3 features
    np.random.seed(42)
    X_train = pd.DataFrame(np.random.randn(100, 3), columns=["f1", "f2", "f3"])
    y_train = pd.Series(np.random.randn(100))

    model_data = train_quantile_model(X_train, y_train)

    # Test with only 2 features (f3 missing)
    X_test = pd.DataFrame(np.random.randn(10, 2), columns=["f1", "f2"])

    # Should handle missing feature via imputation
    predictions = predict_quantiles(model_data, X_test)

    assert len(predictions) == 10
    assert "q10" in predictions.columns
    assert "q50" in predictions.columns
    assert "q90" in predictions.columns


def test_train_with_missing_values():
    """Test training with missing values in features."""
    np.random.seed(42)
    X = pd.DataFrame(np.random.randn(100, 3), columns=["f1", "f2", "f3"])

    # Introduce missing values
    X.loc[10:20, "f1"] = np.nan
    X.loc[30:35, "f2"] = np.nan

    y = pd.Series(np.random.randn(100))

    # Should handle missing values via imputation
    model_data = train_quantile_model(X, y)

    assert "models" in model_data
    assert model_data["train_metrics"]["missing_value_pct"] > 0


def test_model_pipeline_pickle_compatibility():
    """Test that ModelPipeline can be pickled by joblib.

    Regression test: local classes inside functions can't be pickled,
    so ModelPipeline must be defined at module level.
    """
    import joblib
    import io
    from g2.ml.models import ModelPipeline
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LinearRegression

    # Create a simple pipeline
    imputer = SimpleImputer(strategy='median')
    model = LinearRegression()

    # Fit on dummy data
    X = np.array([[1, 2], [3, 4], [5, 6]])
    y = np.array([1, 2, 3])
    imputer.fit(X)
    model.fit(X, y)

    pipeline = ModelPipeline(imputer, model)

    # Test that it can be pickled and unpickled
    buffer = io.BytesIO()
    joblib.dump(pipeline, buffer)
    buffer.seek(0)
    loaded = joblib.load(buffer)

    # Verify it works after unpickling
    assert hasattr(loaded, 'predict')
    assert hasattr(loaded, 'named_steps')
    predictions = loaded.predict(X)
    assert len(predictions) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
