"""Feature selection experiment type.

Evaluates feature subsets to find the optimal set for model performance.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from gefion.experiments.core import ExperimentConfig
from gefion.experiments.types.hyperparameter import PurgedKFold
from gefion.ml.models import load_dataset, train_quantile_model, predict_quantiles
from gefion.ml.evaluation import calculate_calibration_metrics
from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


@dataclass
class FeatureSelectionExperiment:
    """Experiment that evaluates feature subsets.

    Each trial trains a model on a different subset of features using
    PurgedKFold CV. The search space specifies which features to include
    per trial (e.g., params={"features": ["f1", "f3"]}).
    """
    name: str
    principle_id: str
    null_hypothesis: str
    feature_names: List[str]
    selection_method: str = "importance"  # importance, forward, backward
    risk_level: str = "low"
    objective_metric: str = "quantile_loss"
    algorithm: str = "lightgbm"
    cv_config: Optional[Dict[str, Any]] = None
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not self.feature_names:
            raise ValueError("feature_names must be a non-empty list")

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Train model on specified feature subset and return CV metrics.

        Args:
            params: Must contain "features" key with list of feature names
                    to include (e.g., {"features": ["f1", "f3"]}).

        Returns:
            Dict of averaged metrics across CV folds.
        """
        selected_features = params["features"]
        cv_cfg = self.cv_config or {"n_splits": 5, "embargo_pct": 0.0, "prediction_horizon": 0}

        with create_span(
            "experiments.feature_selection.evaluate",
            n_features=len(selected_features),
            horizon_days=self.horizon_days,
        ) as span:
            # Load dataset (cache across trials)
            if self._cached_data is None:
                X, y = load_dataset(self.dataset_uri, self.horizon_days)
                object.__setattr__(self, "_cached_data", (X, y))
            X_full, y = self._cached_data

            # Select feature subset
            X = X_full[selected_features]

            cv = PurgedKFold(
                n_splits=cv_cfg.get("n_splits", 5),
                embargo_pct=cv_cfg.get("embargo_pct", 0.0),
                prediction_horizon=cv_cfg.get("prediction_horizon", 0),
            )

            all_fold_metrics: List[Dict[str, float]] = []

            for train_idx, test_idx in cv.split(X):
                X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
                X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]

                model_data = train_quantile_model(
                    X_train, y_train,
                    algorithm=self.algorithm,
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

            set_attributes(span, n_folds=len(all_fold_metrics),
                           features=",".join(selected_features))

            return avg_metrics

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="feature_selection",
            search_space={"features": self.feature_names},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "selection_method": self.selection_method,
                "feature_names": self.feature_names,
                "algorithm": self.algorithm,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
