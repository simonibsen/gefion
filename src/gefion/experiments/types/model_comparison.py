"""Model comparison experiment type.

Evaluates multiple model types on identical data splits for fair comparison.
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
class ModelComparisonExperiment:
    """Experiment that compares multiple model types on identical splits.

    All models are trained on the same purged CV splits and evaluated
    on the same holdout for direct metric comparability.
    """
    name: str
    model_types: List[str]  # e.g., ["quantile", "xgboost", "lightgbm"]
    principle_id: Optional[str] = None
    null_hypothesis: Optional[str] = None
    risk_level: str = "low"
    objective_metric: str = "quantile_loss"
    cv_config: Optional[Dict[str, Any]] = None
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Train the specified algorithm with purged CV and return averaged metrics.

        Args:
            params: Must contain "model_type" key (e.g., {"model_type": "lightgbm"}).

        Returns:
            Dict of averaged metrics across CV folds, including quantile_loss.
        """
        algorithm = params["model_type"]
        cv_cfg = self.cv_config or {"n_splits": 5, "embargo_pct": 0.0, "prediction_horizon": 0}

        with create_span(
            "experiments.model_comparison.evaluate",
            algorithm=algorithm,
            horizon_days=self.horizon_days,
        ) as span:
            # Load dataset (cache across trials)
            if self._cached_data is None:
                X, y = load_dataset(self.dataset_uri, self.horizon_days)
                object.__setattr__(self, "_cached_data", (X, y))
            X, y = self._cached_data

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
                    algorithm=algorithm,
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

            set_attributes(span, algorithm=algorithm, n_folds=len(all_fold_metrics))

            return avg_metrics

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="model_comparison",
            search_space={"model_type": self.model_types},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "model_types": self.model_types,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
