"""Tests for ML evaluation metrics and calibration analysis."""
import numpy as np
import pandas as pd
import pytest

from g2.ml.evaluation import (
    pinball_loss,
    calculate_calibration_metrics,
    generate_evaluation_report,
    calculate_forecast_skill_metrics,
)


def test_pinball_loss_perfect_prediction():
    """Test pinball loss with perfect predictions."""
    y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y_pred = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    # Perfect predictions should have zero loss
    loss = pinball_loss(y_true, y_pred, quantile=0.5)
    assert loss == 0.0


def test_pinball_loss_quantiles():
    """Test pinball loss for different quantiles."""
    y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y_pred = np.array([0.5, 1.5, 2.5, 3.5, 4.5])  # Underestimate by 0.5

    # Different quantiles should produce different losses
    loss_q10 = pinball_loss(y_true, y_pred, quantile=0.1)
    loss_q50 = pinball_loss(y_true, y_pred, quantile=0.5)
    loss_q90 = pinball_loss(y_true, y_pred, quantile=0.9)

    assert loss_q10 < loss_q50  # Lower quantile has lower penalty for underestimation
    assert loss_q50 < loss_q90  # Higher quantile has higher penalty for underestimation


def test_calculate_calibration_metrics_perfect_calibration():
    """Test calibration metrics with perfectly calibrated predictions."""
    np.random.seed(42)
    n_samples = 1000

    # Generate random data
    data = np.random.randn(n_samples)

    # Create predictions from true quantiles of the data
    q10_value = np.percentile(data, 10)
    q50_value = np.percentile(data, 50)
    q90_value = np.percentile(data, 90)

    predictions = pd.DataFrame({
        "q10": [q10_value] * n_samples,
        "q50": [q50_value] * n_samples,
        "q90": [q90_value] * n_samples,
    })
    actuals = pd.Series(data)

    metrics = calculate_calibration_metrics(predictions, actuals)

    # Should have perfect calibration (within statistical noise)
    assert abs(metrics["q10_calibration"] - 10.0) < 2.0  # Allow 2% error
    assert abs(metrics["q50_calibration"] - 50.0) < 2.0
    assert abs(metrics["q90_calibration"] - 90.0) < 2.0
    assert metrics["num_samples"] == n_samples


def test_calculate_calibration_metrics_structure():
    """Test that calibration metrics returns expected structure."""
    predictions = pd.DataFrame({
        "q10": [0.1, 0.2, 0.3],
        "q50": [0.5, 0.6, 0.7],
        "q90": [0.9, 1.0, 1.1],
    })
    actuals = pd.Series([0.4, 0.7, 0.8])

    metrics = calculate_calibration_metrics(predictions, actuals)

    # Check all expected keys are present
    assert "num_samples" in metrics
    assert "q10_calibration" in metrics
    assert "q50_calibration" in metrics
    assert "q90_calibration" in metrics
    assert "q10_loss" in metrics
    assert "q50_loss" in metrics
    assert "q90_loss" in metrics
    assert "quantile_loss" in metrics
    assert "avg_iqr" in metrics
    assert "median_iqr" in metrics
    assert "std_iqr" in metrics
    assert "interval_80_coverage" in metrics


def test_calculate_calibration_metrics_iqr():
    """Test inter-quantile range calculations."""
    predictions = pd.DataFrame({
        "q10": [0.0, 1.0, 2.0],
        "q50": [0.5, 1.5, 2.5],
        "q90": [1.0, 2.0, 3.0],
    })
    actuals = pd.Series([0.5, 1.5, 2.5])

    metrics = calculate_calibration_metrics(predictions, actuals)

    # IQR should be q90 - q10 = 1.0 for all samples
    assert metrics["avg_iqr"] == 1.0
    assert metrics["median_iqr"] == 1.0
    assert metrics["std_iqr"] == 0.0


def test_calculate_calibration_metrics_interval_coverage():
    """Test 80% prediction interval coverage."""
    # Create data where 2 out of 5 samples are outside [q10, q90]
    predictions = pd.DataFrame({
        "q10": [1.0, 1.0, 1.0, 1.0, 1.0],
        "q50": [2.0, 2.0, 2.0, 2.0, 2.0],
        "q90": [3.0, 3.0, 3.0, 3.0, 3.0],
    })
    actuals = pd.Series([0.5, 2.0, 2.5, 2.8, 3.5])  # 0.5 and 3.5 are outside

    metrics = calculate_calibration_metrics(predictions, actuals)

    # 3 out of 5 samples are within interval = 60%
    assert metrics["interval_80_coverage"] == 60.0


def test_generate_evaluation_report():
    """Test evaluation report generation."""
    results_by_horizon = {
        7: {
            "num_samples": 100,
            "q10_calibration": 12.0,
            "q50_calibration": 48.0,
            "q90_calibration": 88.0,
            "quantile_loss": 0.123,
            "avg_iqr": 0.456,
            "interval_80_coverage": 78.0,
        },
        30: {
            "num_samples": 150,
            "q10_calibration": 11.0,
            "q50_calibration": 51.0,
            "q90_calibration": 89.0,
            "quantile_loss": 0.234,
            "avg_iqr": 0.678,
            "interval_80_coverage": 79.0,
        },
    }

    report = generate_evaluation_report("test_model", results_by_horizon)

    # Check report contains expected sections
    assert "Model Evaluation Report: test_model" in report
    assert "Horizon: 7 days" in report
    assert "Horizon: 30 days" in report
    assert "Summary:" in report
    assert "Total Samples:" in report
    assert "250" in report  # 100 + 150


def test_calculate_forecast_skill_metrics():
    """Test forecast skill metrics (MAE, RMSE, directional accuracy)."""
    predictions = pd.DataFrame({
        "q50": [0.1, -0.1, 0.2, -0.2, 0.05],
    })
    actuals = pd.Series([0.15, -0.05, 0.25, -0.15, 0.03])

    metrics = calculate_forecast_skill_metrics(predictions, actuals)

    # Check all expected keys
    assert "mae" in metrics
    assert "rmse" in metrics
    assert "directional_accuracy" in metrics

    # All predictions have correct sign, so directional accuracy should be 100%
    assert metrics["directional_accuracy"] == 100.0


def test_calculate_calibration_metrics_length_mismatch():
    """Test that length mismatch raises ValueError."""
    predictions = pd.DataFrame({
        "q10": [0.1, 0.2],
        "q50": [0.5, 0.6],
        "q90": [0.9, 1.0],
    })
    actuals = pd.Series([0.4, 0.7, 0.8])  # Length 3 vs 2

    with pytest.raises(ValueError, match="Length mismatch"):
        calculate_calibration_metrics(predictions, actuals)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
