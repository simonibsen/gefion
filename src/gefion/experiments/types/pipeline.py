"""Pipeline experiment type.

Chains multiple experiment stages (feature → model → strategy) with
dependency tracking and end-to-end holdout evaluation.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from gefion.experiments.core import ExperimentConfig
from gefion.experiments.types.hyperparameter import PurgedKFold
from gefion.experiments.types.feature_engineering import _FEATURE_FUNCTIONS
from gefion.experiments.types.label_engineering import _LABEL_TRANSFORMS
from gefion.ml.models import load_dataset, train_quantile_model, predict_quantiles
from gefion.ml.evaluation import calculate_calibration_metrics
from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


@dataclass
class PipelineExperiment:
    """Experiment that chains multiple stages evaluated end-to-end.

    Stages run sequentially: feature engineering → label transform → train.
    Each trial tests a different combination of pipeline parameters.
    The entire pipeline is evaluated via PurgedKFold CV.

    Supported stage types:
    - feature_engineering: adds computed feature column
    - label_transform: transforms prediction target
    - train: trains model (always last, implicit)
    """
    name: str
    stages: List[Dict[str, Any]]  # [{type, config}, {type, config}, ...]
    principle_id: Optional[str] = None
    null_hypothesis: Optional[str] = None
    risk_level: str = "high"
    objective_metric: str = "quantile_loss"
    algorithm: str = "lightgbm"
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    cv_config: Optional[Dict[str, Any]] = None
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not self.stages or len(self.stages) < 2:
            raise ValueError("Pipeline experiments require at least 2 stages")

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Run the full pipeline with given params and evaluate via CV.

        Args:
            params: Parameters that apply across pipeline stages
                    (e.g., {"window": 10, "algorithm": "xgboost"}).

        Returns:
            Dict of averaged CV metrics.
        """
        cv_cfg = self.cv_config or {"n_splits": 5, "embargo_pct": 0.0, "prediction_horizon": 0}

        with create_span(
            "experiments.pipeline.evaluate",
            stage_count=len(self.stages),
            horizon_days=self.horizon_days,
        ) as span:
            # Load dataset (cache across trials)
            if self._cached_data is None:
                X, y = load_dataset(self.dataset_uri, self.horizon_days)
                object.__setattr__(self, "_cached_data", (X, y))
            X_base, y_base = self._cached_data

            # Apply pipeline stages
            X = X_base.copy()
            y = y_base.copy()
            algorithm = params.get("algorithm", self.algorithm)

            for stage in self.stages:
                stage_type = stage.get("type", "")

                if stage_type == "feature_engineering":
                    fn_name = stage.get("function_name", "rolling_zscore")
                    source_col = stage.get("source_column", "close")
                    feat_fn = _FEATURE_FUNCTIONS.get(fn_name)
                    if feat_fn is not None and source_col in X.columns:
                        col_name = f"pipe_{fn_name}"
                        X[col_name] = feat_fn(X[source_col], **{
                            k: params.get(k, v)
                            for k, v in stage.get("default_params", {}).items()
                        }, **{k: v for k, v in params.items()
                              if k in (stage.get("param_keys", []))})

                elif stage_type == "label_transform":
                    label_type = stage.get("label_type", params.get("label_type", "raw"))
                    transform_fn = _LABEL_TRANSFORMS.get(label_type, _LABEL_TRANSFORMS["raw"])
                    y = transform_fn(y, **{k: params[k] for k in params if k != "label_type" and k != "algorithm" and k != "window"})

                elif stage_type == "train":
                    algorithm = stage.get("algorithm", params.get("algorithm", self.algorithm))

            # PurgedKFold CV evaluation
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
            experiment_type="pipeline",
            search_space={"stages": self.stages},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "stages": self.stages,
                "stage_count": len(self.stages),
                "algorithm": self.algorithm,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
