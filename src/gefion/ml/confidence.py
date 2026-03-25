"""
Confidence metrics for predictions.

Computes confidence scores from various sources:
- Quantile predictions: IQR width
- Classifier predictions: entropy, margin
- Ensemble: model agreement
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np


def compute_quantile_confidence(
    q10: float,
    q50: float,
    q90: float,
    historical_iqr_median: float
) -> float:
    """
    Confidence from quantile predictions.

    Narrow IQR relative to historical = high confidence.
    Also boosts confidence for predictions far from zero (more decisive).

    Args:
        q10: 10th percentile prediction
        q50: 50th percentile (median) prediction
        q90: 90th percentile prediction
        historical_iqr_median: Median IQR from historical predictions

    Returns:
        Confidence score (0-1)
    """
    iqr = q90 - q10

    if historical_iqr_median <= 0:
        return 0.5  # No reference, return neutral

    relative_iqr = iqr / historical_iqr_median

    # Transform: narrower IQR = higher confidence
    # exp(-x) gives 1 when x=0, ~0.37 when x=1, ~0.14 when x=2
    confidence = math.exp(-relative_iqr)

    # Boost for predictions far from zero (more decisive)
    magnitude_factor = min(1.0, abs(q50) / historical_iqr_median)

    return confidence * 0.7 + magnitude_factor * 0.3


def compute_classifier_confidence(
    class_probabilities: Dict[str, float]
) -> Dict[str, float]:
    """
    Confidence from classifier probabilities.

    Uses two metrics:
    - Entropy: Lower entropy = more confident (concentrated probability)
    - Margin: Larger gap between top-2 classes = clearer signal

    Args:
        class_probabilities: Dict with class names as keys, probabilities as values
            Expected keys: strong_up, weak_up, flat, weak_down, strong_down

    Returns:
        Dict with 'entropy', 'margin', 'confidence'
    """
    probs = np.array(list(class_probabilities.values()))

    # Clip to avoid log(0)
    probs = np.clip(probs, 1e-10, 1.0)

    # Entropy (lower = more confident)
    # Max entropy for 5 classes = log(5) ≈ 1.61
    entropy = -np.sum(probs * np.log(probs))
    max_entropy = math.log(len(probs))
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
    entropy_confidence = 1 - normalized_entropy

    # Margin between top-2 classes
    sorted_probs = np.sort(probs)[::-1]
    margin = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) > 1 else 1.0

    # Combined confidence (equal weight to entropy and margin)
    confidence = entropy_confidence * 0.5 + margin * 0.5

    return {
        'entropy': float(entropy),
        'margin': float(margin),
        'confidence': float(confidence)
    }


def compute_ensemble_disagreement(
    base_model_predictions: List[float]
) -> Dict[str, float]:
    """
    Disagreement metrics across ensemble base models.

    Lower std dev and range = higher agreement = higher confidence.

    Args:
        base_model_predictions: List of predictions from each base model
            (e.g., q50 values from XGBoost, LightGBM, etc.)

    Returns:
        Dict with 'std', 'range', 'agreement_score'
    """
    if len(base_model_predictions) < 2:
        return {'std': 0.0, 'range': 0.0, 'agreement_score': 1.0}

    preds = np.array(base_model_predictions)
    std = float(np.std(preds))
    pred_range = float(np.max(preds) - np.min(preds))

    # Agreement score: inverse of std, normalized
    # exp(-std * 20) gives ~1 when std=0, ~0.37 when std=0.05, ~0.14 when std=0.10
    agreement_score = math.exp(-std * 20)

    return {
        'std': std,
        'range': pred_range,
        'agreement_score': agreement_score
    }
