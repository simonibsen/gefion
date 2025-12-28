"""ML ensemble model functionality.

Model ensembles combine predictions from multiple algorithms for improved accuracy.
Supports weighted averaging of quantile predictions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from g2.ml.models import (
    train_quantile_model,
    save_model_artifact,
    load_model_artifact,
    predict_quantiles,
)
from g2.observability import create_span

logger = logging.getLogger(__name__)


@dataclass
class EnsembleConfig:
    """Configuration for ensemble training."""

    algorithms: List[str] = field(default_factory=lambda: ["quantile_regression"])
    weights: Optional[List[float]] = None
    hyperparams: Optional[Dict[str, Dict[str, Any]]] = None
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])


def create_ensemble(
    model_paths: List[Path],
    weights: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Create an ensemble from existing trained model paths.

    Args:
        model_paths: List of paths to trained model artifacts
        weights: Optional weights for each model (must sum to 1.0).
                 If None, equal weights are used.

    Returns:
        Dict containing:
            - models: List of loaded model artifacts
            - weights: Weights for each model
            - ensemble_type: Type of ensemble ("weighted_average")
            - feature_names: Feature names from first model

    Raises:
        ValueError: If weights don't sum to 1.0 or don't match number of models
        FileNotFoundError: If any model path doesn't exist
    """
    with create_span("create_ensemble", num_models=len(model_paths)):
        # Validate weights
        if weights is not None:
            if len(weights) != len(model_paths):
                raise ValueError(
                    f"Number of weights ({len(weights)}) must match "
                    f"number of models ({len(model_paths)})"
                )
            if not np.isclose(sum(weights), 1.0, rtol=1e-5):
                raise ValueError(
                    f"Weights must sum to 1.0, got {sum(weights)}"
                )
        else:
            # Equal weights
            weights = [1.0 / len(model_paths)] * len(model_paths)

        # Load all models
        models = []
        for path in model_paths:
            with create_span("load_base_model", path=str(path)):
                model_data = load_model_artifact(Path(path))
                models.append(model_data)

        # Use feature names from first model
        feature_names = models[0]["feature_names"]

        logger.info(
            f"Created ensemble with {len(models)} models, "
            f"weights={weights}"
        )

        return {
            "models": models,
            "weights": weights,
            "ensemble_type": "weighted_average",
            "feature_names": feature_names,
            "quantiles": models[0].get("quantiles", [0.1, 0.5, 0.9]),
        }


def train_ensemble(
    X: pd.DataFrame,
    y: pd.Series,
    algorithms: List[str],
    weights: Optional[List[float]] = None,
    hyperparams: Optional[Dict[str, Dict[str, Any]]] = None,
    output_dir: Optional[Path] = None,
    quantiles: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Train an ensemble of models from scratch.

    Args:
        X: Feature matrix (n_samples, n_features)
        y: Target values (n_samples,)
        algorithms: List of algorithms to train (e.g., ["xgboost", "lightgbm"])
        weights: Optional weights for each algorithm (must sum to 1.0).
                 If None, equal weights are used.
        hyperparams: Optional dict mapping algorithm name to hyperparameters
        output_dir: Optional directory to save model artifacts
        quantiles: Quantiles to predict (default: [0.1, 0.5, 0.9])

    Returns:
        Dict containing:
            - ensemble: The created ensemble
            - base_models: List of trained model data
            - metrics: Training metrics

    Raises:
        ValueError: If weights don't sum to 1.0 or algorithms list is empty
    """
    with create_span(
        "train_ensemble",
        num_algorithms=len(algorithms),
        num_samples=len(X),
        num_features=X.shape[1],
    ):
        if not algorithms:
            raise ValueError("At least one algorithm must be specified")

        if quantiles is None:
            quantiles = [0.1, 0.5, 0.9]

        if hyperparams is None:
            hyperparams = {}

        # Validate weights
        if weights is not None:
            if len(weights) != len(algorithms):
                raise ValueError(
                    f"Number of weights ({len(weights)}) must match "
                    f"number of algorithms ({len(algorithms)})"
                )
            if not np.isclose(sum(weights), 1.0, rtol=1e-5):
                raise ValueError(
                    f"Weights must sum to 1.0, got {sum(weights)}"
                )
        else:
            weights = [1.0 / len(algorithms)] * len(algorithms)

        # Train each algorithm
        base_models = []
        model_paths = []

        for i, algorithm in enumerate(algorithms):
            with create_span(
                f"train_ensemble.{algorithm}",
                algorithm=algorithm,
                weight=weights[i],
            ):
                algo_hyperparams = hyperparams.get(algorithm, {})

                logger.info(f"Training {algorithm} model ({i+1}/{len(algorithms)})")

                model_data = train_quantile_model(
                    X=X,
                    y=y,
                    algorithm=algorithm,
                    hyperparams=algo_hyperparams,
                    quantiles=quantiles,
                )

                base_models.append(model_data)

                # Save if output_dir specified
                if output_dir:
                    model_path = Path(output_dir) / f"base_model_{i}"
                    save_model_artifact(
                        model_data,
                        model_path,
                        metadata={
                            "algorithm": algorithm,
                            "ensemble_index": i,
                            "weight": weights[i],
                        },
                    )
                    model_paths.append(model_path)

        # Create ensemble from trained models
        if model_paths:
            # If we saved models, create ensemble from paths
            ensemble = create_ensemble(
                model_paths=model_paths,
                weights=weights,
            )
        else:
            # Create ensemble directly from model data
            ensemble = {
                "models": [
                    {
                        "models": m["models"],
                        "feature_names": m["feature_names"],
                        "quantiles": m["quantiles"],
                        "algorithm": m["algorithm"],
                    }
                    for m in base_models
                ],
                "weights": weights,
                "ensemble_type": "weighted_average",
                "feature_names": base_models[0]["feature_names"],
                "quantiles": quantiles,
            }

        # Save ensemble metadata
        if output_dir:
            save_ensemble(ensemble, Path(output_dir))

        # Compute metrics
        metrics = {
            "num_models": len(base_models),
            "algorithms": algorithms,
            "weights": weights,
            "num_samples": len(X),
            "num_features": X.shape[1],
        }

        logger.info(
            f"Trained ensemble with {len(base_models)} models: {algorithms}"
        )

        return {
            "ensemble": ensemble,
            "base_models": base_models,
            "metrics": metrics,
        }


def predict_ensemble(
    ensemble: Dict[str, Any],
    X: pd.DataFrame,
) -> pd.DataFrame:
    """
    Generate predictions using an ensemble of models.

    Computes weighted average of predictions from each base model.

    Args:
        ensemble: Ensemble dict from create_ensemble or train_ensemble
        X: Feature matrix (must match training feature schema)

    Returns:
        DataFrame with columns [q10, q50, q90] containing ensemble predictions
    """
    with create_span(
        "predict_ensemble",
        num_models=len(ensemble["models"]),
        num_samples=len(X),
    ):
        weights = ensemble["weights"]
        models = ensemble["models"]

        # Get predictions from each model
        all_predictions = []

        for i, model_data in enumerate(models):
            with create_span(
                "ensemble.predict_base_model",
                model_index=i,
                weight=weights[i],
            ):
                preds = predict_quantiles(model_data, X)
                all_predictions.append(preds)

        # Compute weighted average
        with create_span("ensemble.combine_predictions"):
            result = pd.DataFrame(index=X.index)

            for q_col in ["q10", "q50", "q90"]:
                if q_col not in all_predictions[0].columns:
                    continue

                weighted_sum = sum(
                    preds[q_col].values * weight
                    for preds, weight in zip(all_predictions, weights)
                )
                result[q_col] = weighted_sum

            # Enforce quantile ordering: q10 <= q50 <= q90
            if "q10" in result and "q50" in result:
                result["q10"] = np.minimum(result["q10"], result["q50"])
            if "q50" in result and "q90" in result:
                result["q90"] = np.maximum(result["q50"], result["q90"])

        logger.info(f"Generated ensemble predictions for {len(result)} samples")

        return result


def save_ensemble(
    ensemble: Dict[str, Any],
    artifact_path: Path,
) -> None:
    """
    Save ensemble metadata to disk.

    Note: Base models should already be saved separately.
    This saves the ensemble configuration and references.

    Args:
        ensemble: Ensemble dict from create_ensemble or train_ensemble
        artifact_path: Directory to save ensemble metadata
    """
    with create_span("save_ensemble", path=str(artifact_path)):
        artifact_path = Path(artifact_path)
        artifact_path.mkdir(parents=True, exist_ok=True)

        # Save ensemble metadata
        metadata = {
            "ensemble_type": ensemble["ensemble_type"],
            "weights": ensemble["weights"],
            "feature_names": ensemble["feature_names"],
            "quantiles": ensemble.get("quantiles", [0.1, 0.5, 0.9]),
            "num_models": len(ensemble["models"]),
        }

        metadata_file = artifact_path / "ensemble_metadata.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Saved ensemble metadata to {metadata_file}")


def load_ensemble(artifact_path: Path) -> Dict[str, Any]:
    """
    Load ensemble from disk.

    Args:
        artifact_path: Directory containing ensemble metadata and base models

    Returns:
        Ensemble dict ready for prediction

    Raises:
        FileNotFoundError: If ensemble metadata or base models not found
    """
    with create_span("load_ensemble", path=str(artifact_path)):
        artifact_path = Path(artifact_path)

        if not artifact_path.exists():
            raise FileNotFoundError(f"Ensemble directory not found: {artifact_path}")

        # Load metadata
        metadata_file = artifact_path / "ensemble_metadata.json"
        if not metadata_file.exists():
            raise FileNotFoundError(
                f"ensemble_metadata.json not found in {artifact_path}"
            )

        with open(metadata_file, "r") as f:
            metadata = json.load(f)

        # Load base models
        models = []
        for i in range(metadata["num_models"]):
            model_path = artifact_path / f"base_model_{i}"
            if model_path.exists():
                model_data = load_model_artifact(model_path)
                models.append(model_data)
            else:
                raise FileNotFoundError(
                    f"Base model not found: {model_path}"
                )

        ensemble = {
            "models": models,
            "weights": metadata["weights"],
            "ensemble_type": metadata["ensemble_type"],
            "feature_names": metadata["feature_names"],
            "quantiles": metadata.get("quantiles", [0.1, 0.5, 0.9]),
        }

        logger.info(
            f"Loaded ensemble with {len(models)} models from {artifact_path}"
        )

        return ensemble
