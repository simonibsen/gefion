"""Multi-class classifier for trend prediction (5-class labels)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder

from gefion.observability import create_span

logger = logging.getLogger(__name__)


def load_dataset_for_classifier(
    artifact_uri: Path, horizon_days: int
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Load features and trend labels from CSV files for classification.

    Args:
        artifact_uri: Path to dataset manifest JSON file
        horizon_days: Horizon to filter labels (e.g., 7, 30, 90)

    Returns:
        (X_features, y_labels): Features DataFrame and labels Series

    Raises:
        FileNotFoundError: If CSV files don't exist
        ValueError: If no labels found for horizon
    """
    # Dataset CSVs are in the same directory as manifest
    dataset_dir = Path(artifact_uri).parent

    # Try both CSV and Parquet formats
    features_csv = dataset_dir / "features.csv"
    features_parquet = dataset_dir / "features.parquet"
    labels_csv = dataset_dir / "labels.csv"
    labels_parquet = dataset_dir / "labels.parquet"

    # Determine which format to use
    if features_parquet.exists():
        logger.info(f"Loading features from {features_parquet}")
        features_df = pd.read_parquet(features_parquet)
    elif features_csv.exists():
        logger.info(f"Loading features from {features_csv}")
        features_df = pd.read_csv(features_csv)
    else:
        raise FileNotFoundError(f"features file not found at {dataset_dir}")

    if labels_parquet.exists():
        logger.info(f"Loading labels from {labels_parquet}")
        labels_df = pd.read_parquet(labels_parquet)
    elif labels_csv.exists():
        logger.info(f"Loading labels from {labels_csv}")
        labels_df = pd.read_csv(labels_csv)
    else:
        raise FileNotFoundError(f"labels file not found at {dataset_dir}")

    # Pivot features to wide format
    logger.info("Pivoting features to wide format")
    features_wide = features_df.pivot_table(
        index=["symbol", "date"], columns="feature_name", values="value", aggfunc="first"
    ).reset_index()

    # Filter labels to specific horizon
    labels_horizon = labels_df[labels_df["horizon_days"] == horizon_days].copy()

    if len(labels_horizon) == 0:
        raise ValueError(f"No labels found for horizon {horizon_days} days")

    logger.info(f"Found {len(labels_horizon)} labels for {horizon_days}-day horizon")

    # Join features with labels on (symbol, date)
    merged = features_wide.merge(
        labels_horizon[["symbol", "date", "label"]], on=["symbol", "date"], how="inner"
    )

    if len(merged) == 0:
        raise ValueError(f"No matching features and labels for horizon {horizon_days}")

    # Separate features and target
    feature_cols = [col for col in features_wide.columns if col not in ["symbol", "date"]]
    X = merged[feature_cols]
    y = merged["label"]

    logger.info(f"Dataset shape: X={X.shape}, y={y.shape}")
    logger.info(f"Features: {list(feature_cols)}")
    logger.info(f"Label distribution: {y.value_counts().to_dict()}")

    return X, y


def train_classifier(
    X: pd.DataFrame,
    y: pd.Series,
    algorithm: str = "sklearn",
    hyperparams: Dict[str, Any] = None,
    device: str = "cpu",
) -> Dict[str, Any]:
    """
    Train a multi-class classifier for trend prediction.

    Args:
        X: Feature matrix (n_samples, n_features)
        y: Target labels (n_samples,) - categorical trend labels
        algorithm: Algorithm choice ("sklearn", "xgboost", "lightgbm")
        hyperparams: Optional hyperparameter overrides
        device: Compute device ("cpu" or "cuda" for GPU training)

    Returns:
        Dict containing:
            - model: Trained classifier pipeline
            - label_encoder: LabelEncoder for converting labels
            - feature_names: List[str] - Feature column names
            - train_metrics: Dict - Training metrics
            - algorithm: str - Algorithm used

    Raises:
        ValueError: If algorithm not supported
    """
    if hyperparams is None:
        hyperparams = {}

    with create_span("ml.train_classifier", algorithm=algorithm,
                      n_samples=X.shape[0], n_features=X.shape[1], device=device):
        return _train_classifier_impl(X, y, algorithm, hyperparams, device)


def _train_classifier_impl(
    X: pd.DataFrame, y: pd.Series, algorithm: str,
    hyperparams: Dict[str, Any], device: str,
) -> Dict[str, Any]:
    logger.info(f"Training {algorithm} classifier")
    logger.info(f"Training data: {X.shape[0]} samples, {X.shape[1]} features")
    logger.info(f"Training device: {device}")

    # Encode labels to integers
    label_encoder = LabelEncoder()
    y_encoded = label_encoder.fit_transform(y)

    logger.info(f"Classes: {list(label_encoder.classes_)}")

    # Check for missing values
    missing_pct = X.isna().sum() / len(X) * 100
    high_missing = missing_pct[missing_pct > 20]
    if len(high_missing) > 0:
        logger.warning(f"Features with >20% missing values: {dict(high_missing)}")

    # Train model based on algorithm
    if algorithm == "sklearn":
        model = _train_sklearn_classifier(X, y_encoded, hyperparams)
    elif algorithm == "xgboost":
        model = _train_xgboost_classifier(X, y_encoded, hyperparams, device)
    elif algorithm == "lightgbm":
        model = _train_lightgbm_classifier(X, y_encoded, hyperparams, device)
    else:
        raise ValueError(
            f"Unsupported algorithm: {algorithm}. " f"Choose from: sklearn, xgboost, lightgbm"
        )

    # Calculate training metrics
    y_pred = model.predict(X)
    train_accuracy = accuracy_score(y_encoded, y_pred)

    train_metrics = {
        "num_samples": len(X),
        "num_features": X.shape[1],
        "num_classes": len(label_encoder.classes_),
        "missing_value_pct": float(X.isna().sum().sum() / (X.shape[0] * X.shape[1]) * 100),
        "train_accuracy": float(train_accuracy),
        "algorithm": algorithm,
    }

    logger.info(f"Training accuracy: {train_accuracy:.4f}")

    return {
        "model": model,
        "label_encoder": label_encoder,
        "feature_names": list(X.columns),
        "train_metrics": train_metrics,
        "algorithm": algorithm,
    }


def _train_sklearn_classifier(
    X: pd.DataFrame, y: np.ndarray, hyperparams: Dict[str, Any]
) -> Pipeline:
    """Train sklearn RandomForestClassifier."""
    n_estimators = hyperparams.get("n_estimators", 100)
    max_depth = hyperparams.get("max_depth", None)
    random_state = hyperparams.get("random_state", 42)

    pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=n_estimators,
                    max_depth=max_depth,
                    random_state=random_state,
                    n_jobs=-1,
                ),
            ),
        ]
    )

    pipeline.fit(X, y)
    return pipeline


def _train_xgboost_classifier(
    X: pd.DataFrame, y: np.ndarray, hyperparams: Dict[str, Any], device: str = "cpu"
) -> Pipeline:
    """Train XGBoost classifier."""
    try:
        import xgboost as xgb
    except ImportError:
        raise ImportError("xgboost not installed. Install with: pip install g2[ml_extended]")

    n_estimators = hyperparams.get("n_estimators", 100)
    max_depth = hyperparams.get("max_depth", 6)
    learning_rate = hyperparams.get("learning_rate", 0.1)
    random_state = hyperparams.get("random_state", 42)

    # Configure device-specific parameters
    # XGBoost 2.0+: use device="cuda" with tree_method="hist" (gpu_hist is deprecated)
    device_param = "cuda" if device == "cuda" else "cpu"

    pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                xgb.XGBClassifier(
                    n_estimators=n_estimators,
                    max_depth=max_depth,
                    learning_rate=learning_rate,
                    tree_method="hist",
                    device=device_param,
                    random_state=random_state,
                    n_jobs=-1,
                    objective="multi:softmax",
                ),
            ),
        ]
    )

    pipeline.fit(X, y)
    return pipeline


def _train_lightgbm_classifier(
    X: pd.DataFrame, y: np.ndarray, hyperparams: Dict[str, Any], device: str = "cpu"
) -> Pipeline:
    """Train LightGBM classifier."""
    try:
        import lightgbm as lgb
    except ImportError:
        raise ImportError(
            "lightgbm not installed. Install with: pip install g2[ml_extended]"
        )

    n_estimators = hyperparams.get("n_estimators", 100)
    max_depth = hyperparams.get("max_depth", -1)
    learning_rate = hyperparams.get("learning_rate", 0.1)
    random_state = hyperparams.get("random_state", 42)

    # Configure device-specific parameters
    # LightGBM uses "gpu" (not "cuda") for GPU device
    device_param = "gpu" if device == "cuda" else "cpu"

    pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                lgb.LGBMClassifier(
                    n_estimators=n_estimators,
                    max_depth=max_depth,
                    learning_rate=learning_rate,
                    device=device_param,
                    random_state=random_state,
                    n_jobs=-1,
                    verbose=-1,
                ),
            ),
        ]
    )

    pipeline.fit(X, y)
    return pipeline


def predict_classifier(model_artifacts: Dict[str, Any], X: pd.DataFrame) -> pd.DataFrame:
    """
    Make predictions with trained classifier.

    Args:
        model_artifacts: Dict from train_classifier containing model, label_encoder, etc.
        X: Feature matrix to predict on

    Returns:
        DataFrame with predicted_class and probability columns for each class
    """
    with create_span("ml.predict_classifier", n_samples=X.shape[0]):
        return _predict_classifier_impl(model_artifacts, X)


def _predict_classifier_impl(model_artifacts: Dict[str, Any], X: pd.DataFrame) -> pd.DataFrame:
    model = model_artifacts["model"]
    label_encoder = model_artifacts["label_encoder"]

    # Get class predictions (encoded)
    y_pred_encoded = model.predict(X)

    # Get class probabilities
    y_proba = model.predict_proba(X)

    # Decode predictions to original labels
    y_pred = label_encoder.inverse_transform(y_pred_encoded)

    # Build results DataFrame
    results = pd.DataFrame({"predicted_class": y_pred})

    # Add probability columns for each class
    for i, class_name in enumerate(label_encoder.classes_):
        results[f"probability_{class_name}"] = y_proba[:, i]

    return results


def evaluate_classifier(
    model_artifacts: Dict[str, Any], X: pd.DataFrame, y: pd.Series
) -> Dict[str, Any]:
    """
    Evaluate classifier with metrics.

    Args:
        model_artifacts: Dict from train_classifier
        X: Feature matrix
        y: True labels

    Returns:
        Dict with accuracy, confusion_matrix, per_class_metrics
    """
    model = model_artifacts["model"]
    label_encoder = model_artifacts["label_encoder"]

    # Encode true labels
    y_encoded = label_encoder.transform(y)

    # Predict
    y_pred_encoded = model.predict(X)

    # Calculate metrics
    accuracy = accuracy_score(y_encoded, y_pred_encoded)
    conf_matrix = confusion_matrix(y_encoded, y_pred_encoded)

    # Per-class metrics
    per_class_metrics = {}
    for i, class_name in enumerate(label_encoder.classes_):
        # Binary mask for this class
        y_binary = (y_encoded == i).astype(int)
        y_pred_binary = (y_pred_encoded == i).astype(int)

        precision = precision_score(y_binary, y_pred_binary, zero_division=0)
        recall = recall_score(y_binary, y_pred_binary, zero_division=0)
        f1 = f1_score(y_binary, y_pred_binary, zero_division=0)

        per_class_metrics[class_name] = {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
        }

    return {
        "accuracy": float(accuracy),
        "confusion_matrix": conf_matrix.tolist(),
        "per_class_metrics": per_class_metrics,
    }
