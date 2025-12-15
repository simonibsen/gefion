"""ML model evaluation metrics and calibration analysis."""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    """
    Calculate quantile loss (pinball loss) for a specific quantile.

    The pinball loss is asymmetric: it penalizes overestimation and underestimation
    differently based on the quantile level.

    Formula:
        loss = mean(max(quantile * (y_true - y_pred), (quantile - 1) * (y_true - y_pred)))

    Perfect calibration means empirical coverage matches the theoretical quantile.
    For example, 50% of actuals should be below the q50 prediction.

    Args:
        y_true: Actual values
        y_pred: Predicted quantile values
        quantile: Quantile level (e.g., 0.1, 0.5, 0.9)

    Returns:
        Mean pinball loss across all samples
    """
    errors = y_true - y_pred
    loss = np.where(errors >= 0, quantile * errors, (quantile - 1) * errors)
    return float(np.mean(loss))


def calculate_calibration_metrics(
    predictions: pd.DataFrame,
    actuals: pd.Series
) -> Dict[str, float]:
    """
    Calculate calibration and performance metrics for quantile predictions.

    Calibration measures how well predicted quantiles match empirical coverage:
    - q10_calibration should be ~10% (10% of actuals below q10 prediction)
    - q50_calibration should be ~50% (50% of actuals below q50 prediction)
    - q90_calibration should be ~90% (90% of actuals below q90 prediction)

    Args:
        predictions: DataFrame with columns [q10, q50, q90]
        actuals: Series of actual outcome values (same length as predictions)

    Returns:
        Dict with metrics:
            - q10_calibration: % of actuals < q10 (target: 10%)
            - q50_calibration: % of actuals < q50 (target: 50%)
            - q90_calibration: % of actuals < q90 (target: 90%)
            - quantile_loss: Average pinball loss across quantiles
            - avg_iqr: Average inter-quantile range (q90 - q10)
            - num_samples: Number of samples evaluated

    Raises:
        ValueError: If predictions and actuals have different lengths
    """
    if len(predictions) != len(actuals):
        raise ValueError(f"Length mismatch: predictions={len(predictions)}, actuals={len(actuals)}")

    actuals_array = actuals.values

    # Calculate empirical coverage (% of actuals below predicted quantile)
    metrics = {
        "num_samples": len(actuals)
    }

    if "q10" in predictions.columns:
        coverage_q10 = np.mean(actuals_array < predictions["q10"].values) * 100
        loss_q10 = pinball_loss(actuals_array, predictions["q10"].values, 0.1)
        metrics["q10_calibration"] = float(coverage_q10)
        metrics["q10_loss"] = float(loss_q10)
        logger.debug(f"Q10 calibration: {coverage_q10:.1f}% (target: 10%)")

    if "q50" in predictions.columns:
        coverage_q50 = np.mean(actuals_array < predictions["q50"].values) * 100
        loss_q50 = pinball_loss(actuals_array, predictions["q50"].values, 0.5)
        metrics["q50_calibration"] = float(coverage_q50)
        metrics["q50_loss"] = float(loss_q50)
        logger.debug(f"Q50 calibration: {coverage_q50:.1f}% (target: 50%)")

    if "q90" in predictions.columns:
        coverage_q90 = np.mean(actuals_array < predictions["q90"].values) * 100
        loss_q90 = pinball_loss(actuals_array, predictions["q90"].values, 0.9)
        metrics["q90_calibration"] = float(coverage_q90)
        metrics["q90_loss"] = float(loss_q90)
        logger.debug(f"Q90 calibration: {coverage_q90:.1f}% (target: 90%)")

    # Calculate average pinball loss
    losses = []
    if "q10_loss" in metrics:
        losses.append(metrics["q10_loss"])
    if "q50_loss" in metrics:
        losses.append(metrics["q50_loss"])
    if "q90_loss" in metrics:
        losses.append(metrics["q90_loss"])

    if losses:
        metrics["quantile_loss"] = float(np.mean(losses))

    # Calculate inter-quantile range (IQR) statistics
    if "q10" in predictions.columns and "q90" in predictions.columns:
        iqr = predictions["q90"].values - predictions["q10"].values
        metrics["avg_iqr"] = float(np.mean(iqr))
        metrics["median_iqr"] = float(np.median(iqr))
        metrics["std_iqr"] = float(np.std(iqr))
        logger.debug(f"IQR statistics: mean={metrics['avg_iqr']:.4f}, median={metrics['median_iqr']:.4f}")

    # Calculate prediction interval coverage (actual within [q10, q90])
    if "q10" in predictions.columns and "q90" in predictions.columns:
        within_interval = (actuals_array >= predictions["q10"].values) & (actuals_array <= predictions["q90"].values)
        coverage_80 = np.mean(within_interval) * 100
        metrics["interval_80_coverage"] = float(coverage_80)
        logger.debug(f"80% interval coverage: {coverage_80:.1f}% (target: 80%)")

    logger.info(f"Calibration metrics calculated for {len(actuals)} samples")

    return metrics


def generate_evaluation_report(
    model_name: str,
    results_by_horizon: Dict[int, Dict[str, float]]
) -> str:
    """
    Generate human-readable evaluation report for model performance.

    Args:
        model_name: Name of the model being evaluated
        results_by_horizon: Dict mapping horizon_days to calibration metrics

    Returns:
        Formatted report string
    """
    lines = [
        "",
        "=" * 70,
        f"Model Evaluation Report: {model_name}",
        "=" * 70,
        ""
    ]

    for horizon, metrics in sorted(results_by_horizon.items()):
        lines.append(f"Horizon: {horizon} days")
        lines.append("-" * 50)
        lines.append(f"  Samples:              {metrics.get('num_samples', 0):,}")

        # Calibration metrics
        if "q10_calibration" in metrics:
            q10_cal = metrics["q10_calibration"]
            q10_error = abs(q10_cal - 10.0)
            lines.append(f"  Q10 Calibration:      {q10_cal:>6.1f}% (target: 10%, error: {q10_error:.1f}%)")

        if "q50_calibration" in metrics:
            q50_cal = metrics["q50_calibration"]
            q50_error = abs(q50_cal - 50.0)
            lines.append(f"  Q50 Calibration:      {q50_cal:>6.1f}% (target: 50%, error: {q50_error:.1f}%)")

        if "q90_calibration" in metrics:
            q90_cal = metrics["q90_calibration"]
            q90_error = abs(q90_cal - 90.0)
            lines.append(f"  Q90 Calibration:      {q90_cal:>6.1f}% (target: 90%, error: {q90_error:.1f}%)")

        if "interval_80_coverage" in metrics:
            lines.append(f"  80% Interval Coverage: {metrics['interval_80_coverage']:>6.1f}% (target: 80%)")

        # Loss metrics
        if "quantile_loss" in metrics:
            lines.append(f"  Quantile Loss:        {metrics['quantile_loss']:>6.4f}")

        # IQR statistics
        if "avg_iqr" in metrics:
            lines.append(f"  Avg IQR:              {metrics['avg_iqr']:>6.4f}")
            if "std_iqr" in metrics:
                lines.append(f"  IQR Std Dev:          {metrics['std_iqr']:>6.4f}")

        lines.append("")

    # Summary statistics
    lines.append("=" * 70)
    lines.append("Summary:")
    lines.append("-" * 50)

    total_samples = sum(m.get("num_samples", 0) for m in results_by_horizon.values())
    lines.append(f"  Total Samples:        {total_samples:,}")
    lines.append(f"  Horizons Evaluated:   {len(results_by_horizon)}")

    # Average calibration error across all horizons
    q50_errors = [abs(m.get("q50_calibration", 50.0) - 50.0)
                  for m in results_by_horizon.values()
                  if "q50_calibration" in m]
    if q50_errors:
        avg_q50_error = np.mean(q50_errors)
        lines.append(f"  Avg Q50 Cal Error:    {avg_q50_error:.1f}%")

    lines.append("=" * 70)
    lines.append("")

    return "\n".join(lines)


def calculate_forecast_skill_metrics(
    predictions: pd.DataFrame,
    actuals: pd.Series
) -> Dict[str, float]:
    """
    Calculate forecast skill metrics beyond calibration.

    Args:
        predictions: DataFrame with columns [q10, q50, q90]
        actuals: Series of actual outcomes

    Returns:
        Dict with additional metrics:
            - mae: Mean absolute error (using q50 as point forecast)
            - rmse: Root mean squared error
            - directional_accuracy: % of times q50 predicts correct sign
    """
    metrics = {}

    if "q50" in predictions.columns:
        actuals_array = actuals.values
        q50_array = predictions["q50"].values

        # Mean absolute error
        mae = np.mean(np.abs(actuals_array - q50_array))
        metrics["mae"] = float(mae)

        # Root mean squared error
        rmse = np.sqrt(np.mean((actuals_array - q50_array) ** 2))
        metrics["rmse"] = float(rmse)

        # Directional accuracy (% correct sign)
        correct_direction = np.sign(actuals_array) == np.sign(q50_array)
        directional_accuracy = np.mean(correct_direction) * 100
        metrics["directional_accuracy"] = float(directional_accuracy)

        logger.debug(f"Forecast skill: MAE={mae:.4f}, RMSE={rmse:.4f}, "
                    f"Directional={directional_accuracy:.1f}%")

    return metrics
