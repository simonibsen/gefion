"""ML model training and prediction functionality."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import QuantileRegressor
from sklearn.pipeline import Pipeline

from gefion.ml.device import detect_device
from gefion.observability import create_span

logger = logging.getLogger(__name__)


class ModelPipeline:
    """Wrapper for model + imputer that can be pickled.

    Must be defined at module level for joblib serialization to work.
    """

    def __init__(self, imputer, model):
        self.named_steps = {'imputer': imputer, 'model': model}
        self.imputer = imputer
        self.model = model

    def predict(self, X):
        X_imputed = self.imputer.transform(X)
        return self.model.predict(X_imputed)


def load_dataset(artifact_uri: Path, horizon_days: int, with_meta: bool = False):
    """
    Load features and labels from CSV or Parquet files for a specific horizon.

    Supports both CSV and Parquet formats. Parquet is preferred if both exist.

    Args:
        artifact_uri: Path to dataset manifest JSON file
        horizon_days: Horizon to filter labels (e.g., 7, 30, 90)

    Returns:
        (X_features, y_labels): Features DataFrame and labels Series

    Raises:
        FileNotFoundError: If neither CSV nor Parquet files exist
        ValueError: If no labels found for horizon
    """
    # Dataset files are in the same directory as manifest
    dataset_dir = Path(artifact_uri).parent

    # Check for both CSV and Parquet formats (prefer Parquet)
    features_csv = dataset_dir / "features.csv"
    features_parquet = dataset_dir / "features.parquet"
    labels_csv = dataset_dir / "labels.csv"
    labels_parquet = dataset_dir / "labels.parquet"

    # Load features (prefer Parquet for performance)
    if features_parquet.exists():
        logger.info(f"Loading features from {features_parquet}")
        features_df = pd.read_parquet(features_parquet)
    elif features_csv.exists():
        logger.info(f"Loading features from {features_csv}")
        features_df = pd.read_csv(features_csv)
    else:
        raise FileNotFoundError(f"features file not found at {dataset_dir} (tried .parquet and .csv)")

    # Load labels (prefer Parquet for performance)
    if labels_parquet.exists():
        logger.info(f"Loading labels from {labels_parquet}")
        labels_df = pd.read_parquet(labels_parquet)
    elif labels_csv.exists():
        logger.info(f"Loading labels from {labels_csv}")
        labels_df = pd.read_csv(labels_csv)
    else:
        raise FileNotFoundError(f"labels file not found at {dataset_dir} (tried .parquet and .csv)")

    # Pivot to wide format for sklearn
    logger.info("Pivoting features to wide format")
    features_wide = features_df.pivot_table(
        index=["symbol", "date"],
        columns="feature_name",
        values="value",
        aggfunc="first"  # Take first if duplicates
    ).reset_index()

    # Filter to specific horizon
    labels_horizon = labels_df[labels_df["horizon_days"] == horizon_days].copy()

    if len(labels_horizon) == 0:
        raise ValueError(f"No labels found for horizon {horizon_days} days")

    logger.info(f"Found {len(labels_horizon)} labels for {horizon_days}-day horizon")

    # Join features with labels on (symbol, date)
    merged = features_wide.merge(
        labels_horizon[["symbol", "date", "forward_return"]],
        on=["symbol", "date"],
        how="inner"
    )

    if len(merged) == 0:
        raise ValueError(f"No matching features and labels for horizon {horizon_days}")

    # Separate features and target
    feature_cols = [col for col in features_wide.columns if col not in ["symbol", "date"]]
    X = merged[feature_cols]
    y = merged["forward_return"]

    logger.info(f"Dataset shape: X={X.shape}, y={y.shape}")
    logger.info(f"Features: {list(feature_cols)}")

    if with_meta:
        # Row-aligned symbol/date — lets callers split by date (holdout)
        # and group scores per symbol without joining back to disk
        return (X.reset_index(drop=True), y.reset_index(drop=True),
                merged[["symbol", "date"]].reset_index(drop=True))
    return X, y


def train_quantile_model(
    X: pd.DataFrame,
    y: pd.Series,
    algorithm: str = "quantile_regression",
    hyperparams: Dict[str, Any] = None,
    quantiles: List[float] = None,
    device: Optional[str] = None,
    base_model_path: Path = None,
    universe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Train quantile regression models for multiple quantiles.

    Args:
        X: Feature matrix (n_samples, n_features)
        y: Target values (n_samples,)
        algorithm: Algorithm choice ("quantile_regression", "xgboost", "lightgbm")
        hyperparams: Optional hyperparameter overrides
        quantiles: Quantiles to predict (default: [0.1, 0.5, 0.9])
        device: Compute device ("cpu" or "cuda"). None (default) =
                auto-detect (#146) — so every caller, including the
                experiment framework, inherits GPU support; a warm-start
                base model's recorded device is pinned over auto-detect
                (GPU/CPU training is not numerically identical).
        base_model_path: Optional path to base model for warm-start training.
                        Continues training from this model instead of starting fresh.
                        Only supported for xgboost and lightgbm.
        universe: Optional universe provenance stamp (spec 015) —
                 {"universe_name", "universe_fingerprint", ...} — recorded in
                 the result and artifact metadata. Absent = pre-015 result.

    Returns:
        Dict containing:
            - models: Dict[str, Pipeline] - Trained models for each quantile
            - imputer: SimpleImputer - Fitted imputer
            - feature_names: List[str] - Feature column names
            - train_metrics: Dict - Training metrics
            - warm_start: bool - Whether warm-start was used
            - base_model_path: str - Path to base model (if warm-start)

    Raises:
        ValueError: If algorithm not supported
    """
    if quantiles is None:
        quantiles = [0.1, 0.5, 0.9]

    if hyperparams is None:
        hyperparams = {}

    with create_span("ml.train", algorithm=algorithm, quantiles=str(quantiles),
                      n_samples=X.shape[0], n_features=X.shape[1],
                      device=device or "auto"):
        result = _train_quantile_model_impl(
            X, y, algorithm, hyperparams, quantiles, device, base_model_path
        )
        if universe is not None:
            result["universe"] = dict(universe)
        return result


def _train_quantile_model_impl(
    X: pd.DataFrame,
    y: pd.Series,
    algorithm: str,
    hyperparams: Dict[str, Any],
    quantiles: List[float],
    device: Optional[str],
    base_model_path: Path,
) -> Dict[str, Any]:
    logger.info(f"Training {algorithm} model for quantiles {quantiles}")
    logger.info(f"Training data: {X.shape[0]} samples, {X.shape[1]} features")

    # Check for missing values
    missing_pct = X.isna().sum() / len(X) * 100
    high_missing = missing_pct[missing_pct > 20]
    if len(high_missing) > 0:
        logger.warning(f"Features with >20% missing values: {dict(high_missing)}")

    # Load base model for warm-start if provided
    base_models = None
    base_device = None
    warm_start_used = False
    if base_model_path is not None:
        base_model_path = Path(base_model_path)
        if base_model_path.exists():
            try:
                base_artifact = load_model_artifact(base_model_path)
                base_models = base_artifact.get("models", {})
                base_algorithm = base_artifact.get("algorithm", "")

                # Validate algorithm match
                if base_algorithm and base_algorithm != algorithm:
                    logger.warning(
                        f"Algorithm mismatch: base model is {base_algorithm}, "
                        f"requested {algorithm}. Falling back to fresh training."
                    )
                    base_models = None
                elif algorithm == "quantile_regression":
                    logger.warning(
                        "sklearn QuantileRegressor does not support warm-start. "
                        "Falling back to fresh training."
                    )
                    base_models = None
                else:
                    logger.info(f"Loaded base model from {base_model_path} for warm-start")
                    warm_start_used = True
                    base_device = base_artifact.get("device")
            except Exception as e:
                logger.warning(f"Failed to load base model: {e}. Training from scratch.")
                base_models = None
        else:
            logger.warning(f"Base model path not found: {base_model_path}. Training from scratch.")

    # Device resolution (#146): explicit wins; a warm-start base model's
    # recorded device is pinned over auto-detect (GPU/CPU training is not
    # numerically identical); otherwise auto-detect.
    if device is None:
        if warm_start_used and base_device:
            device = base_device
            logger.info(f"Training device: {device} (pinned from base model)")
        else:
            device = detect_device()
            logger.info(f"Training device: {device} (auto-detected)")
    else:
        logger.info(f"Training device: {device}")
    # LightGBM pip wheels have no GPU support and sklearn is CPU-only —
    # downgrade with a warning, never error; provenance records what RAN.
    if device == "cuda" and algorithm in ("lightgbm", "quantile_regression"):
        logger.warning(
            f"{algorithm} cannot train on cuda ({'pip wheel is CPU-only' if algorithm == 'lightgbm' else 'sklearn is CPU-only'}) "
            "— training on cpu")
        device = "cpu"

    # Train separate model for each quantile
    models = {}

    for quantile in quantiles:
        quantile_key = f"q{int(quantile * 100)}"
        base_model = base_models.get(quantile_key) if base_models else None

        logger.info(f"Training model for quantile {quantile}" +
                   (" (warm-start)" if base_model else ""))

        if algorithm == "quantile_regression":
            model = _train_sklearn_quantile(X, y, quantile, hyperparams)
        elif algorithm == "xgboost":
            model = _train_xgboost_quantile(X, y, quantile, hyperparams, device, base_model)
        elif algorithm == "lightgbm":
            model = _train_lightgbm_quantile(X, y, quantile, hyperparams, device, base_model)
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}. "
                           f"Choose from: quantile_regression, xgboost, lightgbm")

        models[quantile_key] = model

    # Calculate training metrics
    train_preds = {
        q_key: models[q_key].predict(X)
        for q_key in models.keys()
    }

    train_metrics = {
        "num_samples": len(X),
        "num_features": X.shape[1],
        "missing_value_pct": float(X.isna().sum().sum() / (X.shape[0] * X.shape[1]) * 100),
        "quantiles": quantiles,
        "algorithm": algorithm
    }

    # Extract imputer from first model (all use same imputer)
    first_model = list(models.values())[0]
    imputer = first_model.named_steps['imputer']

    result = {
        "models": models,
        "imputer": imputer,
        "feature_names": list(X.columns),
        "train_metrics": train_metrics,
        "quantiles": quantiles,
        "algorithm": algorithm,
        "warm_start": warm_start_used,
        "device": device,
    }

    if warm_start_used and base_model_path is not None:
        result["base_model_path"] = str(base_model_path)

    return result


def _train_sklearn_quantile(
    X: pd.DataFrame,
    y: pd.Series,
    quantile: float,
    hyperparams: Dict[str, Any]
) -> Pipeline:
    """Train sklearn QuantileRegressor."""
    alpha = hyperparams.get("alpha", 0.1)  # L2 regularization
    solver = hyperparams.get("solver", "highs")  # Fast LP solver

    pipeline = Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('model', QuantileRegressor(
            quantile=quantile,
            alpha=alpha,
            solver=solver
        ))
    ])

    pipeline.fit(X, y)
    return pipeline


def _train_xgboost_quantile(
    X: pd.DataFrame,
    y: pd.Series,
    quantile: float,
    hyperparams: Dict[str, Any],
    device: str = "cpu",
    base_model: Any = None,
) -> "XGBPipeline":
    """Train XGBoost quantile regressor.

    Args:
        X: Feature matrix
        y: Target values
        quantile: Quantile to predict (e.g., 0.5 for median)
        hyperparams: Hyperparameter overrides
        device: Compute device ("cpu" or "cuda")
        base_model: Optional base model for warm-start training

    Returns:
        XGBPipeline with trained model
    """
    try:
        import xgboost as xgb
    except ImportError:
        raise ImportError("XGBoost not installed. Install with: pip install 'gefion[ml_extended]'")

    n_estimators = hyperparams.get("n_estimators", 100)
    max_depth = hyperparams.get("max_depth", 6)
    learning_rate = hyperparams.get("learning_rate", 0.1)

    # XGBoost requires imputation first (no native handling of NaN for quantile)
    imputer = SimpleImputer(strategy='median')
    X_imputed = imputer.fit_transform(X)

    # Configure device-specific parameters
    # XGBoost 2.0+: use device="cuda" with tree_method="hist" (gpu_hist is deprecated)
    device_param = "cuda" if device == "cuda" else "cpu"

    model = xgb.XGBRegressor(
        objective='reg:quantileerror',
        quantile_alpha=quantile,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        tree_method="hist",
        device=device_param,
        random_state=42
    )

    # Warm-start: continue training from base model
    if base_model is not None and hasattr(base_model, 'model'):
        # Extract the underlying XGBoost model from the pipeline wrapper
        xgb_base = base_model.model
        logger.info(f"Warm-starting XGBoost from base model with {xgb_base.n_estimators} trees")
        model.fit(X_imputed, y, xgb_model=xgb_base)
    else:
        model.fit(X_imputed, y)

    # Use module-level class for pickle compatibility
    return ModelPipeline(imputer, model)


def _train_lightgbm_quantile(
    X: pd.DataFrame,
    y: pd.Series,
    quantile: float,
    hyperparams: Dict[str, Any],
    device: str = "cpu",
    base_model: Any = None,
) -> "LGBPipeline":
    """Train LightGBM quantile regressor.

    Args:
        X: Feature matrix
        y: Target values
        quantile: Quantile to predict (e.g., 0.5 for median)
        hyperparams: Hyperparameter overrides
        device: Compute device ("cpu" or "cuda")
        base_model: Optional base model for warm-start training

    Returns:
        LGBPipeline with trained model
    """
    try:
        import lightgbm as lgb
    except ImportError:
        raise ImportError("LightGBM not installed. Install with: pip install 'gefion[ml_extended]'")
    except OSError as e:
        if "libomp" in str(e):
            raise OSError(
                "LightGBM requires OpenMP library. On macOS, install with: brew install libomp\n"
                "Alternatively, use --algorithm xgboost or --algorithm quantile_regression"
            ) from e
        raise

    n_estimators = hyperparams.get("n_estimators", 100)
    max_depth = hyperparams.get("max_depth", 6)
    learning_rate = hyperparams.get("learning_rate", 0.1)

    imputer = SimpleImputer(strategy='median')
    X_imputed = imputer.fit_transform(X)

    # Configure device-specific parameters
    # LightGBM uses "gpu" (not "cuda") for GPU device
    device_param = "gpu" if device == "cuda" else "cpu"

    model = lgb.LGBMRegressor(
        objective='quantile',
        alpha=quantile,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        device=device_param,
        random_state=42,
        verbose=-1
    )

    # Warm-start: continue training from base model
    if base_model is not None and hasattr(base_model, 'model'):
        # Extract the underlying LightGBM model from the pipeline wrapper
        lgb_base = base_model.model
        logger.info(f"Warm-starting LightGBM from base model")
        model.fit(X_imputed, y, init_model=lgb_base)
    else:
        model.fit(X_imputed, y)

    # Use module-level class for pickle compatibility
    return ModelPipeline(imputer, model)


def save_model_artifact(
    model_data: Dict[str, Any],
    artifact_path: Path,
    metadata: Dict[str, Any]
) -> None:
    """
    Save model artifacts and metadata to disk.

    Args:
        model_data: Dict from train_quantile_model containing models, imputer, etc.
        artifact_path: Directory path to save artifacts (will be created)
        metadata: Additional metadata to save (dataset info, hyperparams, etc.)

    Creates:
        artifact_path/
            model_q10.joblib
            model_q50.joblib
            model_q90.joblib
            metadata.json
            training_log.txt
    """
    artifact_path = Path(artifact_path)
    artifact_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Saving model artifacts to {artifact_path}")

    # Save each quantile model
    for q_key, model in model_data["models"].items():
        model_file = artifact_path / f"model_{q_key}.joblib"
        joblib.dump(model, model_file, compress=3)
        logger.info(f"Saved {q_key} model to {model_file}")

    # Combine metadata
    full_metadata = {
        "feature_names": model_data["feature_names"],
        "quantiles": model_data["quantiles"],
        "device": model_data.get("device"),
        "universe": model_data.get("universe"),
        "algorithm": model_data["algorithm"],
        "train_metrics": model_data["train_metrics"],
        **metadata
    }

    # Save metadata
    metadata_file = artifact_path / "metadata.json"
    with open(metadata_file, "w") as f:
        json.dump(full_metadata, f, indent=2, default=str)
    logger.info(f"Saved metadata to {metadata_file}")

    # Create training log
    log_file = artifact_path / "training_log.txt"
    with open(log_file, "w") as f:
        f.write(f"Model Training Log\n")
        f.write(f"==================\n\n")
        f.write(f"Algorithm: {model_data['algorithm']}\n")
        f.write(f"Quantiles: {model_data['quantiles']}\n")
        f.write(f"Features: {len(model_data['feature_names'])}\n")
        f.write(f"Samples: {model_data['train_metrics']['num_samples']}\n")
        f.write(f"Missing value %: {model_data['train_metrics']['missing_value_pct']:.2f}%\n")
        f.write(f"\nFeature names:\n")
        for feat in model_data['feature_names']:
            f.write(f"  - {feat}\n")
    logger.info(f"Saved training log to {log_file}")


def load_model_artifact(artifact_path: Path) -> Dict[str, Any]:
    """
    Load model artifacts and metadata from disk.

    Args:
        artifact_path: Directory containing model files

    Returns:
        Dict containing models, metadata, feature_names, etc.

    Raises:
        FileNotFoundError: If artifact directory or required files don't exist
    """
    artifact_path = Path(artifact_path)

    if not artifact_path.exists():
        raise FileNotFoundError(f"Model artifact directory not found: {artifact_path}")

    # Load metadata
    metadata_file = artifact_path / "metadata.json"
    if not metadata_file.exists():
        raise FileNotFoundError(f"metadata.json not found in {artifact_path}")

    with open(metadata_file, "r") as f:
        metadata = json.load(f)

    logger.info(f"Loading model artifacts from {artifact_path}")
    logger.info(f"Quantiles: {metadata['quantiles']}")

    # Load each quantile model
    models = {}
    for quantile in metadata["quantiles"]:
        q_key = f"q{int(quantile * 100)}"
        model_file = artifact_path / f"model_{q_key}.joblib"

        if not model_file.exists():
            raise FileNotFoundError(f"Model file not found: {model_file}")

        models[q_key] = joblib.load(model_file)
        logger.info(f"Loaded {q_key} model from {model_file}")

    # Load calibration if present
    from gefion.ml.calibration import load_calibration

    calibration = load_calibration(artifact_path)
    if calibration:
        logger.info("Loaded calibration data (shifts: %s)", calibration.get("shifts", {}))

    return {
        "models": models,
        "feature_names": metadata["feature_names"],
        "quantiles": metadata["quantiles"],
        "algorithm": metadata["algorithm"],
        "device": metadata.get("device"),
        "universe": metadata.get("universe"),
        "metadata": metadata,
        "calibration": calibration,
    }


def predict_quantiles(
    model_data: Dict[str, Any],
    X: pd.DataFrame
) -> pd.DataFrame:
    """
    Generate quantile predictions using trained models.

    Args:
        model_data: Dict from load_model_artifact containing models
        X: Feature matrix (must match training feature schema)

    Returns:
        DataFrame with columns [q10, q50, q90] containing predictions

    Raises:
        ValueError: If feature schema doesn't match training
    """
    with create_span("ml.predict", n_samples=X.shape[0], n_quantiles=len(model_data.get("models", {}))):
        return _predict_quantiles_impl(model_data, X)


def _predict_quantiles_impl(model_data: Dict[str, Any], X: pd.DataFrame) -> pd.DataFrame:
    # Validate feature schema
    expected_features = model_data["feature_names"]
    missing_features = set(expected_features) - set(X.columns)
    extra_features = set(X.columns) - set(expected_features)

    if missing_features:
        logger.warning(f"Missing features (will fill with median): {missing_features}")
        # Add missing columns with NaN (imputer will handle)
        for feat in missing_features:
            X[feat] = np.nan

    if extra_features:
        logger.info(f"Extra features (will ignore): {extra_features}")

    # Align to training schema
    X_aligned = X[expected_features]

    # Predict each quantile
    predictions = pd.DataFrame(index=X_aligned.index)

    for q_key, model in model_data["models"].items():
        predictions[q_key] = model.predict(X_aligned)
        logger.debug(f"Generated {q_key} predictions: mean={predictions[q_key].mean():.4f}")

    # Apply calibration shifts if present
    calibration = model_data.get("calibration")
    if calibration and calibration.get("shifts"):
        with create_span("ml.predict.apply_calibration", shifts=str(calibration["shifts"])):
            from gefion.ml.calibration import apply_calibration_shifts

            predictions = apply_calibration_shifts(predictions, calibration["shifts"])
            logger.info("Applied calibration shifts: %s", calibration["shifts"])
    else:
        # Enforce quantile ordering: q10 <= q50 <= q90
        if "q10" in predictions and "q50" in predictions:
            predictions["q10"] = np.minimum(predictions["q10"], predictions["q50"])
        if "q50" in predictions and "q90" in predictions:
            predictions["q90"] = np.maximum(predictions["q50"], predictions["q90"])

    logger.info(f"Generated predictions for {len(predictions)} samples")

    return predictions
