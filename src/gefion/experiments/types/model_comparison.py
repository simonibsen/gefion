"""Model comparison experiment type.

Evaluates multiple model types on identical data splits for fair comparison.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

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
    # Holdout window (from the cycle): trials never see these rows (FR-017)
    holdout_start: Optional[date] = None
    holdout_end: Optional[date] = None
    # The algorithm the winner must beat on holdout to earn promotion
    baseline_model_type: str = "lightgbm"

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def _training_data(self) -> tuple:
        from gefion.experiments.types.holdout_eval import training_data
        return training_data(self)

    def evaluate_holdout(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Winning model type vs the incumbent, scored per symbol on holdout.

        Both train on identical pre-holdout rows; the holdout window is
        touched exactly once, here (FR-019). If the incumbent itself won
        the comparison the paired scores are identical and the t-test
        yields p=1.0 — no improvement to promote, correctly rejected.
        """
        from gefion.experiments.types.holdout_eval import (
            holdout_masks, load_all_cached, observations_by_date, paired_result,
            per_row_pinball, per_symbol_pinball, require_holdout_window)

        require_holdout_window(self.holdout_start, self.holdout_end)
        winner = params.get("model_type", self.baseline_model_type)
        with create_span("experiments.model_comparison.evaluate_holdout",
                         winner=winner, baseline=self.baseline_model_type) as span:
            X, y, meta = load_all_cached(self)
            train, hold = holdout_masks(meta, self.holdout_start, self.holdout_end)
            if not hold.any():
                raise ValueError(
                    f"No dataset rows fall in the holdout window "
                    f"{self.holdout_start} - {self.holdout_end}")

            y_hold = y[hold].reset_index(drop=True)
            symbols_hold = meta["symbol"][hold].reset_index(drop=True)
            dates_hold = meta["date"][hold].reset_index(drop=True)

            def _preds(algorithm: str) -> pd.DataFrame:
                model = train_quantile_model(
                    X[train], y[train], algorithm=algorithm, quantiles=self.quantiles)
                return predict_quantiles(model, X[hold])

            exp_preds = _preds(winner)
            base_preds = (exp_preds if winner == self.baseline_model_type
                          else _preds(self.baseline_model_type))
            exp_scores = per_symbol_pinball(
                exp_preds, y_hold, symbols_hold, self.quantiles)
            base_scores = (exp_scores if winner == self.baseline_model_type
                           else per_symbol_pinball(
                               base_preds, y_hold, symbols_hold, self.quantiles))

            result = paired_result(base_scores, exp_scores,
                                   int(train.sum()), int(hold.sum()))
            result["observations"] = observations_by_date(
                per_row_pinball(base_preds, y_hold, self.quantiles),
                per_row_pinball(exp_preds, y_hold, self.quantiles),
                dates_hold)
            set_attributes(span, n_symbols=result["n_symbols"])
            return result

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
            # Pre-holdout rows only (FR-017): trials never see holdout data
            X, y, _ = self._training_data()

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
