"""Label engineering experiment type.

Changes the prediction target (e.g., triple-barrier labeling, log returns).
Evaluates how different label transformations affect model quality.
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


# Label transformation functions.
# Each takes (y_series, **params) and returns a transformed Series.
_LABEL_TRANSFORMS = {
    "raw": lambda y, **kw: y,
    "log_return": lambda y, **kw: np.log1p(y),
    "winsorized": lambda y, clip=0.1, **kw: y.clip(
        lower=y.quantile(clip), upper=y.quantile(1 - clip)
    ),
    "threshold_return": lambda y, threshold=0.02, **kw: y.clip(
        lower=-threshold, upper=threshold
    ),
    "sign": lambda y, **kw: np.sign(y) * np.sqrt(np.abs(y)),
    "rank": lambda y, **kw: pd.Series(
        (y.rank(pct=True) - 0.5) * 2, index=y.index, name=y.name
    ),
}


@dataclass
class LabelEngineeringExperiment:
    """Experiment that changes the prediction target.

    Unlike feature engineering (which changes inputs), label engineering
    changes what the model predicts. Each trial applies a different
    label transformation and evaluates via PurgedKFold CV.
    """
    name: str
    principle_id: str
    null_hypothesis: str
    label_type: str  # raw, log_return, winsorized, threshold_return, sign, rank
    label_config: Optional[Dict[str, Any]] = None
    risk_level: str = "high"
    evaluation_metric: str = "quantile_loss"
    algorithm: str = "lightgbm"
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    cv_config: Optional[Dict[str, Any]] = None
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])
    # Holdout window (from the cycle): trials never see these rows (FR-017)
    holdout_start: Optional[date] = None
    holdout_end: Optional[date] = None
    # Stocks each arm picks per holdout date in the signal contest
    top_n: int = 5

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def _training_data(self) -> tuple:
        from gefion.experiments.types.holdout_eval import training_data
        return training_data(self)

    def evaluate_holdout(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Signal contest on the holdout window (FR-013).

        Label experiments change the prediction target, so prediction
        metrics are not comparable across arms. Instead: both arms train
        on identical pre-holdout rows (experimental on transformed labels,
        baseline on raw); each holdout date, each arm picks its top-N
        stocks by its own predicted q50 (rank-based, so label scale is
        irrelevant); the per-date score for BOTH arms is the mean realized
        raw forward return of the picks. Paired per-date scores, one-sided
        "greater". Deliberately ignores costs/turnover — the apply flow
        runs the full backtest as a second gate.
        """
        from gefion.experiments.types.holdout_eval import (
            holdout_masks, load_all_cached, require_holdout_window)

        require_holdout_window(self.holdout_start, self.holdout_end)
        label_type = params.get("label_type", self.label_type)
        top_n = int(params.get("top_n", self.top_n))
        with create_span("experiments.label_engineering.evaluate_holdout",
                         label_type=label_type, top_n=top_n) as span:
            X, y, meta = load_all_cached(self)
            train, hold = holdout_masks(meta, self.holdout_start, self.holdout_end)
            if not hold.any():
                raise ValueError(
                    f"No dataset rows fall in the holdout window "
                    f"{self.holdout_start} - {self.holdout_end}")

            transform_fn = _LABEL_TRANSFORMS.get(label_type, _LABEL_TRANSFORMS["raw"])
            X_train, y_train = X[train], y[train]
            # Transform computed on training labels only — quantile-based
            # transforms must not peek at holdout values
            y_train_exp = transform_fn(y_train.copy(), **{
                k: v for k, v in params.items() if k not in ("label_type", "top_n")})

            exp_model = train_quantile_model(
                X_train, y_train_exp, algorithm=self.algorithm, quantiles=self.quantiles)
            base_model = train_quantile_model(
                X_train, y_train, algorithm=self.algorithm, quantiles=self.quantiles)

            X_hold = X[hold]
            picks = pd.DataFrame({
                "date": meta["date"][hold].values,
                "realized": y[hold].values,  # raw forward return: shared yardstick
                "exp_q50": predict_quantiles(exp_model, X_hold)["q50"].values,
                "base_q50": predict_quantiles(base_model, X_hold)["q50"].values,
            })

            exp_scores, base_scores, dates = [], [], []
            for d, grp in picks.groupby("date", sort=True):
                exp_scores.append(float(
                    grp.nlargest(min(top_n, len(grp)), "exp_q50")["realized"].mean()))
                base_scores.append(float(
                    grp.nlargest(min(top_n, len(grp)), "base_q50")["realized"].mean()))
                dates.append(pd.Timestamp(d).date().isoformat())

            result = {
                "baseline_scores": base_scores,
                "experimental_scores": exp_scores,
                "n_dates": len(dates),
                "holdout_rows": int(hold.sum()),
                "train_rows": int(train.sum()),
                "alternative": "greater",
                "score_kind": "portfolio_forward_return",
                "top_n": top_n,
                # The contest scores are already per-date — they ARE the
                # observations regime-conditional evaluation consumes (#86)
                "observations": [
                    {"date": d, "baseline_score": b, "experimental_score": e}
                    for d, b, e in zip(dates, base_scores, exp_scores)
                ],
            }
            set_attributes(span, n_dates=len(dates), holdout_rows=int(hold.sum()))
            return result

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Transform labels with given params, train, and evaluate.

        Args:
            params: Label transform parameters (e.g., {"threshold": 0.03}).
                    If params contains "label_type", it overrides self.label_type.

        Returns:
            Dict of averaged CV metrics including quantile_loss.
        """
        label_type = params.get("label_type", self.label_type)
        cv_cfg = self.cv_config or {"n_splits": 5, "embargo_pct": 0.0, "prediction_horizon": 0}

        with create_span(
            "experiments.label_engineering.evaluate",
            label_type=label_type,
            horizon_days=self.horizon_days,
        ) as span:
            # Pre-holdout rows only (FR-017): trials never see holdout data
            X, y_raw, _ = self._training_data()

            # Transform labels
            transform_fn = _LABEL_TRANSFORMS.get(label_type, _LABEL_TRANSFORMS["raw"])
            y = transform_fn(y_raw.copy(), **params)

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

            set_attributes(span, label_type=label_type, n_folds=len(all_fold_metrics))

            return avg_metrics

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="label_engineering",
            search_space=self.label_config or {},
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.evaluation_metric,
            cv_config=self.cv_config,
            extra_config={
                "label_type": self.label_type,
                "label_config": self.label_config or {},
                "algorithm": self.algorithm,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
