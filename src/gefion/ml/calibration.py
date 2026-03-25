"""Conformal calibration for quantile prediction models.

Computes additive shift corrections from a holdout period so that
predicted quantiles achieve their nominal coverage rates.

Algorithm (per quantile q, per horizon):
    1. residuals = actuals - predictions
    2. shift = np.quantile(residuals, q)
    3. calibrated_prediction = raw_prediction + shift
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from gefion.observability import create_span

logger = logging.getLogger(__name__)

QUANTILE_MAP = {"q10": 0.1, "q50": 0.5, "q90": 0.9}


@np.errstate(invalid="ignore")
def compute_calibration_shifts(
    predictions: pd.DataFrame,
    actuals: pd.Series,
    quantiles: Optional[List[float]] = None,
) -> Dict[str, float]:
    """Compute additive shift corrections via conformal calibration.

    For each quantile *q* the shift equals the *q*-th empirical quantile
    of the residual vector ``actuals - predictions[q_key]``.

    Args:
        predictions: DataFrame with columns like ``q10``, ``q50``, ``q90``.
        actuals: Series of actual outcome values (same length).
        quantiles: Quantile levels to calibrate. Defaults to [0.1, 0.5, 0.9].

    Returns:
        Dict mapping quantile keys (``q10``, ``q50``, ``q90``) to float shifts.
    """
    if quantiles is None:
        quantiles = [0.1, 0.5, 0.9]

    with create_span(
        "ml.calibrate.compute_shifts",
        n_samples=len(actuals),
        quantiles=str(quantiles),
    ):
        actuals_arr = np.asarray(actuals, dtype=float)
        shifts: Dict[str, float] = {}

        for q in quantiles:
            q_key = f"q{int(q * 100)}"
            if q_key not in predictions.columns:
                logger.warning("Column %s not in predictions; skipping", q_key)
                continue

            preds_arr = np.asarray(predictions[q_key], dtype=float)
            residuals = actuals_arr - preds_arr
            shift = float(np.quantile(residuals, q))
            shifts[q_key] = shift
            logger.info("Shift for %s: %.6f (from %d residuals)", q_key, shift, len(residuals))

        return shifts


def apply_calibration_shifts(
    predictions: pd.DataFrame,
    shifts: Dict[str, float],
) -> pd.DataFrame:
    """Apply additive shifts and enforce quantile ordering.

    After shifting, ensures ``q10 <= q50 <= q90`` by clipping.

    Args:
        predictions: Raw quantile predictions.
        shifts: Mapping of quantile key to shift value.

    Returns:
        New DataFrame with calibrated predictions.
    """
    result = predictions.copy()

    for q_key, shift in shifts.items():
        if q_key in result.columns:
            result[q_key] = result[q_key] + shift

    # Enforce ordering: q10 <= q50 <= q90
    if "q10" in result.columns and "q50" in result.columns:
        result["q10"] = np.minimum(result["q10"], result["q50"])
    if "q50" in result.columns and "q90" in result.columns:
        result["q90"] = np.maximum(result["q50"], result["q90"])

    return result


def save_calibration(
    shifts: Dict[str, float],
    artifact_path: Path,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write ``calibration.json`` to the model artifact directory.

    Args:
        shifts: Quantile key -> shift value.
        artifact_path: Directory containing model artifacts.
        metadata: Extra metadata to include in the JSON file.

    Returns:
        Path to the written calibration file.
    """
    artifact_path = Path(artifact_path)
    artifact_path.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "shifts": shifts,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload.update(metadata)

    cal_file = artifact_path / "calibration.json"
    with open(cal_file, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("Saved calibration to %s", cal_file)
    return cal_file


def load_calibration(artifact_path: Path) -> Optional[Dict[str, Any]]:
    """Load ``calibration.json`` if present.

    Args:
        artifact_path: Directory containing model artifacts.

    Returns:
        Parsed calibration dict, or ``None`` if the file does not exist.
    """
    cal_file = Path(artifact_path) / "calibration.json"
    if not cal_file.exists():
        return None

    with open(cal_file) as f:
        data = json.load(f)

    logger.info("Loaded calibration from %s", cal_file)
    return data


def generate_calibration_report(
    model_name: str,
    shifts_by_horizon: Dict[int, Dict[str, Any]],
) -> str:
    """Format a human-readable before/after calibration report.

    Args:
        model_name: Name of the model.
        shifts_by_horizon: Mapping ``horizon -> {shifts, before, after}``.

    Returns:
        Formatted multi-line report string.
    """
    with create_span("ml.calibrate.report", model_name=model_name):
        lines = [
            "",
            "=" * 70,
            f"Calibration Report: {model_name}",
            "=" * 70,
            "",
        ]

        for horizon in sorted(shifts_by_horizon):
            info = shifts_by_horizon[horizon]
            shifts = info.get("shifts", {})
            before = info.get("before", {})
            after = info.get("after", {})

            lines.append(f"Horizon: {horizon} days")
            lines.append("-" * 50)

            # Shifts
            lines.append("  Shifts:")
            for q_key in ["q10", "q50", "q90"]:
                if q_key in shifts:
                    lines.append(f"    {q_key}: {shifts[q_key]:+.6f}")

            # Before / After calibration
            lines.append("  Before calibration:")
            for key, val in sorted(before.items()):
                lines.append(f"    {key}: {val:.1f}%")

            lines.append("  After calibration:")
            for key, val in sorted(after.items()):
                lines.append(f"    {key}: {val:.1f}%")

            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


def create_pinball_loss_scorer(quantile: float):
    """Create an sklearn-compatible scorer using pinball loss.

    Uses ``sklearn.metrics.make_scorer`` with the pinball loss from
    ``g2.ml.evaluation``.  Returns a scorer where higher is better
    (negated loss), consistent with sklearn convention.

    Args:
        quantile: Quantile level (e.g. 0.1, 0.5, 0.9).

    Returns:
        Callable scorer for use with ``cross_val_score``.
    """
    from sklearn.metrics import make_scorer

    from gefion.ml.evaluation import pinball_loss

    def _pinball(y_true, y_pred):
        return pinball_loss(y_true, y_pred, quantile)

    return make_scorer(_pinball, greater_is_better=False)
