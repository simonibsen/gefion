"""Hyperparameter tuning experiment type with purged cross-validation.

Uses PurgedKFold to prevent information leakage from overlapping labels
in time-series financial data.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from gefion.experiments.core import ExperimentConfig
from gefion.observability import create_span

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
    objective_metric: str = "sharpe_ratio"

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
            },
        )
