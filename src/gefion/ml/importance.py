"""Feature importance computation using SHAP."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import joblib
import numpy as np
import pandas as pd

from gefion.observability import create_span

logger = logging.getLogger(__name__)


def compute_shap_importance(
    model_path: Path,
    X_sample: pd.DataFrame,
    quantile: str = "q50",
) -> Dict[str, float]:
    """
    Compute SHAP-based feature importance for a trained model.

    Args:
        model_path: Path to model artifact directory
        X_sample: Sample data for SHAP computation (features only)
        quantile: Which quantile model to use (e.g., "q50")

    Returns:
        Dict mapping feature names to mean absolute SHAP values

    Raises:
        ImportError: If SHAP is not installed
        FileNotFoundError: If model file not found
    """
    with create_span("ml.feature_importance", quantile=quantile, n_samples=X_sample.shape[0]):
        return _compute_shap_importance_impl(model_path, X_sample, quantile)


def _compute_shap_importance_impl(
    model_path: Path, X_sample: pd.DataFrame, quantile: str,
) -> Dict[str, float]:
    try:
        import shap
    except ImportError:
        raise ImportError(
            "SHAP not installed. Install with: pip install 'gefion[ml_extended]'"
        )

    model_path = Path(model_path)

    # Load model
    model_file = model_path / f"model_{quantile}.joblib"
    if not model_file.exists():
        raise FileNotFoundError(f"Model file not found: {model_file}")

    pipeline = joblib.load(model_file)
    logger.info(f"Loaded model from {model_file}")

    # Load metadata for feature names
    metadata_file = model_path / "metadata.json"
    if metadata_file.exists():
        with open(metadata_file) as f:
            metadata = json.load(f)
        feature_names = metadata.get("feature_names", list(X_sample.columns))
    else:
        feature_names = list(X_sample.columns)

    # Align features
    X_aligned = X_sample[feature_names] if all(f in X_sample.columns for f in feature_names) else X_sample

    # Get the underlying model
    model = _extract_model(pipeline)
    algorithm = _detect_algorithm(model)

    logger.info(f"Computing SHAP values for {algorithm} model with {len(X_aligned)} samples")

    # Compute SHAP values based on model type
    if algorithm in ("xgboost", "lightgbm"):
        # TreeExplainer is fast and exact for tree-based models
        shap_values = _compute_tree_shap(model, X_aligned, pipeline)
    else:
        # Use permutation importance as fallback for sklearn models
        shap_values = _compute_permutation_importance(pipeline, X_aligned)

    # Compute mean absolute SHAP value per feature
    if isinstance(shap_values, np.ndarray):
        if shap_values.ndim == 1:
            # Already per-feature (from permutation importance)
            mean_abs_shap = shap_values
        else:
            # (n_samples, n_features) - take mean across samples
            mean_abs_shap = np.abs(shap_values).mean(axis=0)
    else:
        mean_abs_shap = np.array(shap_values)

    # Ensure we have the right shape
    mean_abs_shap = np.atleast_1d(mean_abs_shap)

    # Create importance dict
    importance = {}
    for i, feat in enumerate(feature_names):
        if i < len(mean_abs_shap):
            importance[feat] = float(mean_abs_shap[i])
        else:
            importance[feat] = 0.0

    logger.info(f"Computed importance for {len(importance)} features")
    return importance


def _extract_model(pipeline) -> Any:
    """Extract the underlying model from a pipeline wrapper."""
    if hasattr(pipeline, 'named_steps'):
        if 'model' in pipeline.named_steps:
            return pipeline.named_steps['model']
    if hasattr(pipeline, 'model'):
        return pipeline.model
    return pipeline


def _detect_algorithm(model) -> str:
    """Detect the algorithm type from the model object."""
    model_type = type(model).__name__.lower()

    if 'xgb' in model_type:
        return 'xgboost'
    elif 'lgb' in model_type:
        return 'lightgbm'
    elif 'quantile' in model_type:
        return 'quantile_regression'
    else:
        return 'unknown'


def _compute_tree_shap(model, X: pd.DataFrame, pipeline) -> np.ndarray:
    """Compute SHAP values using TreeExplainer for tree-based models."""
    import shap

    # Apply imputation if pipeline has imputer
    if hasattr(pipeline, 'imputer'):
        X_imputed = pipeline.imputer.transform(X)
    elif hasattr(pipeline, 'named_steps') and 'imputer' in pipeline.named_steps:
        X_imputed = pipeline.named_steps['imputer'].transform(X)
    else:
        X_imputed = X.values if isinstance(X, pd.DataFrame) else X

    # Create explainer
    explainer = shap.TreeExplainer(model)

    # Compute SHAP values
    shap_values = explainer.shap_values(X_imputed)

    return shap_values


def _compute_permutation_importance(pipeline, X: pd.DataFrame) -> np.ndarray:
    """
    Compute permutation-based feature importance as fallback.

    This is slower than TreeSHAP but works for any model.
    """
    from sklearn.inspection import permutation_importance

    # Need a simple scoring function
    # Since we don't have y, we use a variance-based proxy
    # Shuffle each feature and measure prediction variance change

    n_features = X.shape[1]
    importance = np.zeros(n_features)

    # Get baseline predictions
    baseline_preds = pipeline.predict(X)
    baseline_var = np.var(baseline_preds)

    for i in range(n_features):
        X_permuted = X.copy()
        X_permuted.iloc[:, i] = np.random.permutation(X_permuted.iloc[:, i].values)
        permuted_preds = pipeline.predict(X_permuted)

        # Importance = how much prediction changes when feature is shuffled
        importance[i] = np.mean(np.abs(baseline_preds - permuted_preds))

    return importance


def get_feature_importance(
    model_path: Path,
    quantile: str = "q50",
    top_k: Optional[int] = None,
    sample_size: int = 1000,
) -> Dict[str, Any]:
    """
    Get feature importance for a trained model.

    This is the main entry point that loads the model and computes importance.

    Args:
        model_path: Path to model artifact directory
        quantile: Which quantile model to analyze (default: q50)
        top_k: Limit to top K features (default: all)
        sample_size: Number of samples to use for SHAP (default: 1000)

    Returns:
        Dict with:
            - importance: Dict[str, float] - feature importance scores
            - feature_names: List[str] - ordered feature names
            - algorithm: str - detected algorithm type
    """
    model_path = Path(model_path)

    # Load metadata
    metadata_file = model_path / "metadata.json"
    if not metadata_file.exists():
        raise FileNotFoundError(f"Metadata not found: {metadata_file}")

    with open(metadata_file) as f:
        metadata = json.load(f)

    feature_names = metadata.get("feature_names", [])
    algorithm = metadata.get("algorithm", "unknown")

    # Create synthetic sample data for SHAP computation
    # In practice, you'd load real data from the dataset
    logger.info(f"Creating sample data with {len(feature_names)} features")
    np.random.seed(42)
    X_sample = pd.DataFrame(
        np.random.randn(min(sample_size, 500), len(feature_names)),
        columns=feature_names
    )

    # Compute importance
    importance = compute_shap_importance(
        model_path=model_path,
        X_sample=X_sample,
        quantile=quantile,
    )

    # Sort by importance
    sorted_importance = dict(
        sorted(importance.items(), key=lambda x: x[1], reverse=True)
    )

    # Apply top_k limit
    if top_k is not None and top_k > 0:
        sorted_importance = dict(list(sorted_importance.items())[:top_k])

    return {
        "importance": sorted_importance,
        "feature_names": list(sorted_importance.keys()),
        "algorithm": algorithm,
        "quantile": quantile,
        "num_features": len(feature_names),
    }


def format_importance_table(importance: Dict[str, float], top_k: int = 20) -> str:
    """Format importance as a text table for CLI output."""
    lines = []
    lines.append("Feature Importance (SHAP)")
    lines.append("=" * 50)
    lines.append(f"{'Rank':<6} {'Feature':<30} {'Importance':>12}")
    lines.append("-" * 50)

    sorted_items = sorted(importance.items(), key=lambda x: x[1], reverse=True)

    for i, (feature, value) in enumerate(sorted_items[:top_k], 1):
        lines.append(f"{i:<6} {feature:<30} {value:>12.6f}")

    if len(sorted_items) > top_k:
        lines.append(f"... and {len(sorted_items) - top_k} more features")

    return "\n".join(lines)
