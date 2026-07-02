"""Hyperparameter tuning experiment type with purged cross-validation.

Uses PurgedKFold to prevent information leakage from overlapping labels
in time-series financial data.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np

from gefion.experiments.core import ExperimentConfig
from gefion.ml.models import load_dataset, train_quantile_model, predict_quantiles
from gefion.ml.evaluation import calculate_calibration_metrics
from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


class PurgedKFold:
    """Purged K-Fold cross-validation for time-series data.

    Extends standard KFold with:
    - Time ordering: folds respect temporal order
    - Purging: removes samples within prediction_horizon of test set
    - Embargo: adds a gap between train and test to prevent leakage

    Compatible with sklearn's CV interface (split() yields train/test arrays).

    Based on López de Prado (2018), Ch. 7.
    """

    def __init__(
        self,
        n_splits: int = 5,
        embargo_pct: float = 0.0,
        prediction_horizon: int = 0,
    ):
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct
        self.prediction_horizon = prediction_horizon

    def split(self, X, y=None, groups=None):
        """Generate train/test indices for each fold.

        Yields (train_indices, test_indices) arrays.
        """
        n_samples = len(X)
        indices = np.arange(n_samples)

        # Split into n_splits contiguous test folds (time-ordered)
        fold_sizes = np.full(self.n_splits, n_samples // self.n_splits, dtype=int)
        fold_sizes[:n_samples % self.n_splits] += 1

        current = 0
        test_folds = []
        for size in fold_sizes:
            test_folds.append(indices[current:current + size])
            current += size

        embargo_size = int(n_samples * self.embargo_pct)

        for i, test_idx in enumerate(test_folds):
            test_start = test_idx[0]
            test_end = test_idx[-1]

            # Train on everything before and after the test fold
            # But apply purge + embargo
            train_mask = np.ones(n_samples, dtype=bool)

            # Exclude test indices
            train_mask[test_idx] = False

            # Purge: remove prediction_horizon samples before test start
            if self.prediction_horizon > 0:
                purge_start = max(0, test_start - self.prediction_horizon)
                train_mask[purge_start:test_start] = False

            # Embargo: remove embargo_size samples after test end
            if embargo_size > 0:
                embargo_end = min(n_samples, test_end + 1 + embargo_size)
                train_mask[test_end + 1:embargo_end] = False

            # Also apply embargo before test start (gap between train end and test start)
            if embargo_size > 0:
                embargo_before_start = max(0, test_start - embargo_size)
                # Only embargo indices that aren't already purged
                embargo_before_end = test_start
                if self.prediction_horizon > 0:
                    embargo_before_start = max(0, purge_start - embargo_size)
                    embargo_before_end = purge_start
                train_mask[embargo_before_start:embargo_before_end] = False

            train_idx = indices[train_mask]
            yield train_idx, test_idx

    def get_n_splits(self, X=None, y=None, groups=None):
        """Return the number of splitting iterations."""
        return self.n_splits


@dataclass
class HyperparameterExperiment:
    """Experiment that tunes model hyperparameters with purged CV.

    Uses PurgedKFold to prevent information leakage from overlapping
    labels in time-series financial data.
    """
    name: str
    model_type: str  # quantile, xgboost, lightgbm
    search_space: Dict[str, Any]
    cv_config: Dict[str, Any]  # {n_splits, embargo_pct, prediction_horizon}
    principle_id: Optional[str] = None
    null_hypothesis: Optional[str] = None
    risk_level: str = "low"
    objective_metric: str = "quantile_loss"
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])
    # Holdout window (from the cycle): trials never see these rows (FR-017)
    holdout_start: Optional[date] = None
    holdout_end: Optional[date] = None

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def _load_all(self) -> tuple:
        """Load and cache (X, y, meta) with row-aligned indexes."""
        if self._cached_data is None:
            X, y, meta = load_dataset(self.dataset_uri, self.horizon_days, with_meta=True)
            object.__setattr__(self, "_cached_data", (X, y, meta))
        return self._cached_data

    def _training_data(self) -> tuple:
        """(X, y, meta) restricted to pre-holdout rows."""
        from gefion.experiments.types.holdout_eval import holdout_masks
        X, y, meta = self._load_all()
        train, _ = holdout_masks(meta, self.holdout_start, self.holdout_end)
        return (X[train].reset_index(drop=True),
                y[train].reset_index(drop=True),
                meta[train].reset_index(drop=True))

    def evaluate_holdout(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Best params vs library defaults, scored per symbol on the holdout.

        Both models train on identical pre-holdout rows; the holdout window
        is touched exactly once, here (FR-019).
        """
        from gefion.experiments.types.holdout_eval import (
            holdout_masks, paired_result, per_symbol_pinball, require_holdout_window)

        require_holdout_window(self.holdout_start, self.holdout_end)
        with create_span("experiments.hyperparameter.evaluate_holdout",
                         model_type=self.model_type) as span:
            X, y, meta = self._load_all()
            train, hold = holdout_masks(meta, self.holdout_start, self.holdout_end)
            if not hold.any():
                raise ValueError(
                    f"No dataset rows fall in the holdout window "
                    f"{self.holdout_start} - {self.holdout_end}")

            y_hold = y[hold].reset_index(drop=True)
            symbols_hold = meta["symbol"][hold].reset_index(drop=True)

            exp_model = train_quantile_model(
                X[train], y[train], algorithm=self.model_type,
                hyperparams=params, quantiles=self.quantiles)
            exp_scores = per_symbol_pinball(
                predict_quantiles(exp_model, X[hold]), y_hold, symbols_hold,
                self.quantiles)

            base_model = train_quantile_model(
                X[train], y[train], algorithm=self.model_type,
                hyperparams=None, quantiles=self.quantiles)
            base_scores = per_symbol_pinball(
                predict_quantiles(base_model, X[hold]), y_hold, symbols_hold,
                self.quantiles)

            result = paired_result(base_scores, exp_scores,
                                   int(train.sum()), int(hold.sum()))
            set_attributes(span, n_symbols=result["n_symbols"])
            return result

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Run purged CV with given hyperparameters and return averaged metrics.

        Args:
            params: Hyperparameter values to test (e.g., {"learning_rate": 0.05}).

        Returns:
            Dict of averaged metrics across CV folds, including quantile_loss.
        """
        with create_span(
            "experiments.hyperparameter.evaluate",
            model_type=self.model_type,
            horizon_days=self.horizon_days,
        ) as span:
            # Pre-holdout rows only (FR-017): trials never see holdout data
            X, y, _ = self._training_data()

            cv = PurgedKFold(
                n_splits=self.cv_config.get("n_splits", 5),
                embargo_pct=self.cv_config.get("embargo_pct", 0.0),
                prediction_horizon=self.cv_config.get("prediction_horizon", 0),
            )

            all_fold_metrics: List[Dict[str, float]] = []

            for train_idx, test_idx in cv.split(X):
                X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
                X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

                model_data = train_quantile_model(
                    X_train, y_train,
                    algorithm=self.model_type,
                    hyperparams=params,
                    quantiles=self.quantiles,
                )
                preds = predict_quantiles(model_data, X_test)
                fold_metrics = calculate_calibration_metrics(preds, y_test)
                all_fold_metrics.append(fold_metrics)

            # Average metrics across folds
            avg_metrics: Dict[str, float] = {}
            all_keys = set()
            for fm in all_fold_metrics:
                all_keys.update(fm.keys())
            for key in all_keys:
                values = [fm[key] for fm in all_fold_metrics if key in fm]
                if values and all(isinstance(v, (int, float)) for v in values):
                    avg_metrics[key] = float(np.mean(values))

            set_attributes(span, n_folds=len(all_fold_metrics), **{
                k: v for k, v in avg_metrics.items()
                if isinstance(v, (int, float))
            })

            return avg_metrics

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="hyperparameter",
            search_space=self.search_space,
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "model_type": self.model_type,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
