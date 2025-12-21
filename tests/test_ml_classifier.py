"""
TDD tests for trend classification model.

These tests will initially fail and drive the implementation of
multi-class classifier for predicting trend labels (5-class).
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_load_dataset_for_classifier():
    """Test loading dataset with trend labels for classification."""
    from g2.ml.classifier import load_dataset_for_classifier

    # Create a temporary test dataset
    tmp_dir = Path("/tmp/test_classifier_dataset")
    tmp_dir.mkdir(exist_ok=True)

    # Create features.csv
    features_csv = tmp_dir / "features.csv"
    features_csv.write_text(
        "symbol,date,feature_name,value\n"
        "AAPL,2024-01-01,indicator_rsi_14,50.0\n"
        "AAPL,2024-01-01,indicator_sma_20,100.0\n"
        "AAPL,2024-01-02,indicator_rsi_14,55.0\n"
        "AAPL,2024-01-02,indicator_sma_20,101.0\n"
    )

    # Create labels.csv with trend labels
    labels_csv = tmp_dir / "labels.csv"
    labels_csv.write_text(
        "symbol,date,horizon_days,forward_return,label\n"
        "AAPL,2024-01-01,7,0.05,weak_up\n"
        "AAPL,2024-01-02,7,0.08,strong_up\n"
    )

    # Create manifest
    manifest_path = tmp_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"name": "test", "version": "v1"}))

    # Load dataset
    X, y = load_dataset_for_classifier(manifest_path, horizon_days=7)

    # Verify shapes
    assert X.shape[0] == 2  # 2 samples
    assert X.shape[1] == 2  # 2 features (rsi, sma)
    assert len(y) == 2

    # Verify labels are categorical
    assert y.iloc[0] == "weak_up"
    assert y.iloc[1] == "strong_up"


def test_train_classifier_basic():
    """Test training a basic multi-class classifier."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    from g2.ml.classifier import train_classifier

    # Create sample data
    X = pd.DataFrame({
        "feature1": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
        "feature2": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
    })
    y = pd.Series(["strong_down", "weak_down", "neutral", "weak_up", "strong_up"] * 2)

    # Train classifier
    result = train_classifier(X, y, algorithm="sklearn")

    # Verify result structure
    assert "model" in result
    assert "feature_names" in result
    assert "train_metrics" in result
    assert "label_encoder" in result

    # Verify feature names
    assert result["feature_names"] == ["feature1", "feature2"]

    # Verify model can predict
    model = result["model"]
    predictions = model.predict(X)
    assert len(predictions) == len(y)


def test_train_classifier_with_xgboost():
    """Test training classifier with XGBoost."""
    try:
        import pandas as pd
        import xgboost
    except ImportError:
        pytest.skip("pandas or xgboost not installed")

    from g2.ml.classifier import train_classifier

    # Create sample data
    X = pd.DataFrame({
        "feature1": [1.0, 2.0, 3.0, 4.0, 5.0] * 4,
        "feature2": [5.0, 4.0, 3.0, 2.0, 1.0] * 4,
    })
    y = pd.Series(["strong_down", "weak_down", "neutral", "weak_up", "strong_up"] * 4)

    # Train with XGBoost
    result = train_classifier(X, y, algorithm="xgboost")

    # Verify XGBoost model
    assert result["algorithm"] == "xgboost"
    assert "model" in result

    # Predict
    predictions = result["model"].predict(X)
    assert len(predictions) == len(y)


def test_predict_classifier():
    """Test making predictions with trained classifier."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    from g2.ml.classifier import train_classifier, predict_classifier

    # Train model
    X_train = pd.DataFrame({
        "feature1": [1.0, 2.0, 3.0, 4.0, 5.0] * 2,
        "feature2": [5.0, 4.0, 3.0, 2.0, 1.0] * 2,
    })
    y_train = pd.Series(["strong_down", "weak_down", "neutral", "weak_up", "strong_up"] * 2)
    model_artifacts = train_classifier(X_train, y_train)

    # Predict on new data
    X_new = pd.DataFrame({
        "feature1": [2.5, 3.5],
        "feature2": [3.5, 2.5],
    })

    predictions = predict_classifier(model_artifacts, X_new)

    # Verify predictions
    assert len(predictions) == 2
    assert "predicted_class" in predictions.columns
    assert "probability_strong_down" in predictions.columns
    assert "probability_weak_down" in predictions.columns
    assert "probability_neutral" in predictions.columns
    assert "probability_weak_up" in predictions.columns
    assert "probability_strong_up" in predictions.columns


def test_evaluate_classifier():
    """Test evaluating classifier with metrics."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    from g2.ml.classifier import train_classifier, evaluate_classifier

    # Train model
    X = pd.DataFrame({
        "feature1": [1.0, 2.0, 3.0, 4.0, 5.0] * 4,
        "feature2": [5.0, 4.0, 3.0, 2.0, 1.0] * 4,
    })
    y = pd.Series(["strong_down", "weak_down", "neutral", "weak_up", "strong_up"] * 4)
    model_artifacts = train_classifier(X, y)

    # Evaluate
    metrics = evaluate_classifier(model_artifacts, X, y)

    # Verify metrics
    assert "accuracy" in metrics
    assert "confusion_matrix" in metrics
    assert "per_class_metrics" in metrics

    # Check per-class metrics
    per_class = metrics["per_class_metrics"]
    assert "strong_down" in per_class
    assert "precision" in per_class["strong_down"]
    assert "recall" in per_class["strong_down"]
    assert "f1" in per_class["strong_down"]


def test_classifier_handles_missing_values():
    """Test that classifier handles missing values in features."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        pytest.skip("pandas or numpy not installed")

    from g2.ml.classifier import train_classifier

    # Create data with missing values
    X = pd.DataFrame({
        "feature1": [1.0, np.nan, 3.0, 4.0, 5.0] * 2,
        "feature2": [5.0, 4.0, np.nan, 2.0, 1.0] * 2,
    })
    y = pd.Series(["strong_down", "weak_down", "neutral", "weak_up", "strong_up"] * 2)

    # Train should not fail
    result = train_classifier(X, y)

    # Should have imputer
    assert "imputer" in result or "model" in result

    # Should be able to predict
    predictions = result["model"].predict(X)
    assert len(predictions) == len(y)
