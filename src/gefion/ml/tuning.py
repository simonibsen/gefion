"""Hyperparameter tuning with Optuna.

This module provides Bayesian hyperparameter optimization using Optuna,
with time-series cross-validation to prevent data leakage.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit, cross_val_score

from gefion.observability import create_span

logger = logging.getLogger(__name__)


# Default search spaces for different algorithms
DEFAULT_SEARCH_SPACES = {
    "xgboost": {
        "n_estimators": {"type": "int", "low": 50, "high": 500},
        "max_depth": {"type": "int", "low": 3, "high": 12},
        "learning_rate": {"type": "float", "low": 0.01, "high": 0.3, "log": True},
        "min_child_weight": {"type": "int", "low": 1, "high": 10},
        "subsample": {"type": "float", "low": 0.6, "high": 1.0},
        "colsample_bytree": {"type": "float", "low": 0.6, "high": 1.0},
        "gamma": {"type": "float", "low": 0.0, "high": 5.0},
    },
    "lightgbm": {
        "n_estimators": {"type": "int", "low": 50, "high": 500},
        "max_depth": {"type": "int", "low": 3, "high": 12},
        "learning_rate": {"type": "float", "low": 0.01, "high": 0.3, "log": True},
        "num_leaves": {"type": "int", "low": 20, "high": 150},
        "min_child_samples": {"type": "int", "low": 5, "high": 100},
        "subsample": {"type": "float", "low": 0.6, "high": 1.0},
        "colsample_bytree": {"type": "float", "low": 0.6, "high": 1.0},
        "reg_alpha": {"type": "float", "low": 1e-8, "high": 10.0, "log": True},
        "reg_lambda": {"type": "float", "low": 1e-8, "high": 10.0, "log": True},
    },
    "sklearn": {
        "alpha": {"type": "float", "low": 0.001, "high": 10.0, "log": True},
    },
}


def create_study(
    study_name: str = "gefion_tuning",
    direction: str = "minimize",
    storage: Optional[str] = None,
) -> "optuna.Study":
    """
    Create an Optuna study for hyperparameter optimization.

    Args:
        study_name: Name for the study
        direction: "minimize" or "maximize"
        storage: Optional database URL for persistent storage

    Returns:
        Optuna Study object
    """
    try:
        import optuna
    except ImportError:
        raise ImportError(
            "Optuna not installed. Install with: pip install 'gefion[ml_extended]'"
        )

    # Suppress Optuna's verbose logging
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        study_name=study_name,
        direction=direction,
        storage=storage,
        load_if_exists=True,
    )
    return study


def create_time_series_cv(n_splits: int = 5) -> TimeSeriesSplit:
    """
    Create time-series cross-validation splitter.

    Uses sklearn's TimeSeriesSplit to ensure no data leakage -
    training data always comes before test data.

    Args:
        n_splits: Number of CV folds

    Returns:
        TimeSeriesSplit object
    """
    return TimeSeriesSplit(n_splits=n_splits)


def get_search_space(
    algorithm: str,
    custom_space: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Get hyperparameter search space for an algorithm.

    Args:
        algorithm: Algorithm name (xgboost, lightgbm, sklearn)
        custom_space: Optional custom space to override defaults

    Returns:
        Search space dictionary
    """
    base_space = DEFAULT_SEARCH_SPACES.get(algorithm.lower(), {}).copy()

    if custom_space:
        base_space.update(custom_space)

    return base_space


def _suggest_params(trial: "optuna.Trial", search_space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Suggest parameters from search space using Optuna trial."""
    params = {}
    for name, config in search_space.items():
        param_type = config.get("type", "float")
        low = config.get("low", 0)
        high = config.get("high", 1)
        log = config.get("log", False)

        if param_type == "int":
            params[name] = trial.suggest_int(name, low, high)
        elif param_type == "float":
            params[name] = trial.suggest_float(name, low, high, log=log)
        elif param_type == "categorical":
            params[name] = trial.suggest_categorical(name, config.get("choices", []))

    return params


def tune_quantile_model(
    X: pd.DataFrame,
    y: pd.Series,
    algorithm: str = "xgboost",
    quantile: float = 0.5,
    n_trials: int = 50,
    cv_splits: int = 5,
    custom_space: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    scoring: str = "pinball",
) -> Dict[str, Any]:
    """
    Tune hyperparameters for a quantile regression model.

    Uses Optuna for Bayesian optimization with time-series cross-validation.

    Args:
        X: Feature matrix
        y: Target values
        algorithm: Algorithm to tune (xgboost, lightgbm, sklearn)
        quantile: Quantile to optimize (e.g., 0.5 for median)
        n_trials: Number of optimization trials
        cv_splits: Number of time-series CV splits
        custom_space: Optional custom search space
        timeout: Optional timeout in seconds
        progress_callback: Optional callback(trial_num, n_trials, best_value)
            called after each trial
        scoring: Scoring strategy — "pinball" (default, quantile-appropriate)
            or "mae" (legacy neg_mean_absolute_error).

    Returns:
        Dict with best_params, best_score, n_trials, scoring, etc.
    """
    with create_span(
        "ml.tune_quantile",
        algorithm=algorithm,
        quantile=quantile,
        n_trials=n_trials,
        cv_splits=cv_splits,
        n_samples=len(X),
        n_features=X.shape[1] if hasattr(X, 'shape') else 0,
        scoring=scoring,
    ):
        try:
            import optuna
        except ImportError:
            raise ImportError(
                "Optuna not installed. Install with: pip install 'gefion[ml_extended]'"
            )

        search_space = get_search_space(algorithm, custom_space)
        cv = create_time_series_cv(n_splits=cv_splits)

        # Handle missing values
        from sklearn.impute import SimpleImputer
        imputer = SimpleImputer(strategy='median')
        X_imputed = imputer.fit_transform(X)

        # Build scorer
        if scoring == "pinball":
            from gefion.ml.calibration import create_pinball_loss_scorer
            sklearn_scorer = create_pinball_loss_scorer(quantile)
        else:
            sklearn_scorer = "neg_mean_absolute_error"

        def objective(trial: optuna.Trial) -> float:
            params = _suggest_params(trial, search_space)

            model = _create_quantile_model(algorithm, quantile, params)
            if model is None:
                return float('inf')

            try:
                scores = cross_val_score(
                    model, X_imputed, y,
                    cv=cv,
                    scoring=sklearn_scorer,
                    n_jobs=1,
                )
                return -scores.mean()  # Minimize loss
            except Exception as e:
                logger.warning(f"Trial failed: {e}")
                return float('inf')

        study = create_study(direction="minimize")

        # Create Optuna callback for progress reporting
        callbacks = []
        if progress_callback:
            def optuna_callback(study, trial):
                best_val = study.best_value if study.best_trial else float('inf')
                progress_callback(trial.number + 1, n_trials, best_val)
            callbacks.append(optuna_callback)

        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=timeout,
            show_progress_bar=False,
            callbacks=callbacks if callbacks else None,
        )

        return {
            "best_params": study.best_params,
            "best_score": study.best_value,
            "n_trials": len(study.trials),
            "algorithm": algorithm,
            "quantile": quantile,
            "cv_splits": cv_splits,
            "scoring": scoring,
        }


def _create_quantile_model(algorithm: str, quantile: float, params: Dict[str, Any]):
    """Create a quantile regression model with given parameters."""
    algorithm = algorithm.lower()

    if algorithm == "xgboost":
        try:
            import xgboost as xgb
            return xgb.XGBRegressor(
                objective='reg:quantileerror',
                quantile_alpha=quantile,
                random_state=42,
                verbosity=0,
                **params,
            )
        except ImportError:
            logger.warning("XGBoost not available")
            return None

    elif algorithm == "lightgbm":
        try:
            import lightgbm as lgb
            return lgb.LGBMRegressor(
                objective='quantile',
                alpha=quantile,
                random_state=42,
                verbose=-1,
                **params,
            )
        except ImportError:
            logger.warning("LightGBM not available")
            return None

    elif algorithm == "sklearn":
        from sklearn.linear_model import QuantileRegressor
        alpha = params.pop('alpha', 0.1)
        return QuantileRegressor(
            quantile=quantile,
            alpha=alpha,
            solver='highs',
        )

    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")


def tune_classifier(
    X: pd.DataFrame,
    y: pd.Series,
    algorithm: str = "xgboost",
    n_trials: int = 50,
    cv_splits: int = 5,
    custom_space: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
) -> Dict[str, Any]:
    """
    Tune hyperparameters for a classification model.

    Uses Optuna for Bayesian optimization with time-series cross-validation.

    Args:
        X: Feature matrix
        y: Target labels
        algorithm: Algorithm to tune (xgboost, lightgbm)
        n_trials: Number of optimization trials
        cv_splits: Number of time-series CV splits
        custom_space: Optional custom search space
        timeout: Optional timeout in seconds
        progress_callback: Optional callback(trial_num, n_trials, best_value)
            called after each trial

    Returns:
        Dict with best_params, best_score, n_trials, etc.
    """
    with create_span(
        "ml.tune_classifier",
        algorithm=algorithm,
        n_trials=n_trials,
        cv_splits=cv_splits,
        n_samples=len(X),
        n_features=X.shape[1] if hasattr(X, 'shape') else 0,
    ):
        try:
            import optuna
        except ImportError:
            raise ImportError(
                "Optuna not installed. Install with: pip install 'gefion[ml_extended]'"
            )

        from sklearn.preprocessing import LabelEncoder

        search_space = get_search_space(algorithm, custom_space)
        cv = create_time_series_cv(n_splits=cv_splits)

        # Encode labels
        le = LabelEncoder()
        y_encoded = le.fit_transform(y)

        # Handle missing values
        from sklearn.impute import SimpleImputer
        imputer = SimpleImputer(strategy='median')
        X_imputed = imputer.fit_transform(X)

        def objective(trial: optuna.Trial) -> float:
            params = _suggest_params(trial, search_space)

            model = _create_classifier(algorithm, params)
            if model is None:
                return 0.0  # Return 0 accuracy if model unavailable

            try:
                scores = cross_val_score(
                    model, X_imputed, y_encoded,
                    cv=cv,
                    scoring='accuracy',
                    n_jobs=1,
                )
                return scores.mean()
            except Exception as e:
                logger.warning(f"Trial failed: {e}")
                return 0.0

        study = create_study(direction="maximize")  # Maximize accuracy

        # Create Optuna callback for progress reporting
        callbacks = []
        if progress_callback:
            def optuna_callback(study, trial):
                best_val = study.best_value if study.best_trial else 0.0
                progress_callback(trial.number + 1, n_trials, best_val)
            callbacks.append(optuna_callback)

        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=timeout,
            show_progress_bar=False,
            callbacks=callbacks if callbacks else None,
        )

        return {
            "best_params": study.best_params,
            "best_score": study.best_value,
            "n_trials": len(study.trials),
            "algorithm": algorithm,
            "cv_splits": cv_splits,
        }


def _create_classifier(algorithm: str, params: Dict[str, Any]):
    """Create a classification model with given parameters."""
    algorithm = algorithm.lower()

    if algorithm == "xgboost":
        try:
            import xgboost as xgb
            return xgb.XGBClassifier(
                random_state=42,
                verbosity=0,
                use_label_encoder=False,
                eval_metric='mlogloss',
                **params,
            )
        except ImportError:
            logger.warning("XGBoost not available")
            return None

    elif algorithm == "lightgbm":
        try:
            import lightgbm as lgb
            return lgb.LGBMClassifier(
                random_state=42,
                verbose=-1,
                **params,
            )
        except ImportError:
            logger.warning("LightGBM not available")
            return None

    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")


def save_tuning_results(results: Dict[str, Any], path: Path) -> None:
    """Save tuning results to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    logger.info(f"Saved tuning results to {path}")


def load_tuning_results(path: Path) -> Dict[str, Any]:
    """Load tuning results from JSON file."""
    with open(path) as f:
        return json.load(f)
