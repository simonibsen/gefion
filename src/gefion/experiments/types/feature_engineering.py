"""Feature engineering experiment type.

Creates and evaluates new computed features within the experiment sandbox.
Features are tagged as experimental until auto-promoted via statistical gates.
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


# Built-in feature functions that can be applied to a source column in-memory.
# Each takes (series, **params) and returns a Series.
_FEATURE_FUNCTIONS = {
    "rolling_zscore": lambda s, window=20: (
        (s - s.rolling(window).mean()) / s.rolling(window).std()
    ),
    "rolling_return": lambda s, window=5: s.pct_change(window),
    "rolling_std": lambda s, window=20: s.rolling(window).std(),
    "rolling_mean": lambda s, window=20: s.rolling(window).mean(),
    "ema": lambda s, window=12: s.ewm(span=window).mean(),
    "log_return": lambda s: np.log(s / s.shift(1)),
    "momentum": lambda s, window=10: s / s.shift(window) - 1,
}


def _exec_function_body(function_body: str, function_name: str) -> Optional[callable]:
    """Execute a function body string in the security sandbox.

    Uses the same safe_import and restricted builtins as the feature
    dispatcher, ensuring agent-generated code can't access the filesystem,
    network, or dangerous builtins.

    Args:
        function_body: Python source code containing a compute() function.
        function_name: Name for logging/error messages.

    Returns:
        The compute() callable, or None if execution failed.
    """
    # Same whitelist as gefion.features.dispatcher
    SAFE_MODULES = {
        'numpy', 'np', 'pandas', 'pd', 'datetime', 'math', 'statistics',
        'talib', 'scipy', 'sklearn', 'json', 're', 'itertools', 'functools',
        'operator', 'collections', 'typing',
    }

    # Build safe execution environment (mirrors dispatcher.py sandbox)
    real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__

    def safe_import(name, *args, **kwargs):
        if name.split('.')[0] not in SAFE_MODULES:
            raise ImportError(f"Import of '{name}' is not allowed for security reasons")
        return real_import(name, *args, **kwargs)

    safe_builtins = {
        '__import__': safe_import,
        'print': print, 'len': len, 'range': range, 'enumerate': enumerate,
        'zip': zip, 'map': map, 'filter': filter, 'sorted': sorted, 'reversed': reversed,
        'sum': sum, 'min': min, 'max': max, 'abs': abs, 'round': round,
        'int': int, 'float': float, 'str': str, 'bool': bool, 'list': list,
        'dict': dict, 'tuple': tuple, 'set': set, 'frozenset': frozenset,
        'any': any, 'all': all, 'isinstance': isinstance, 'type': type,
        'None': None, 'True': True, 'False': False,
        'Exception': Exception, 'ValueError': ValueError, 'TypeError': TypeError,
        'KeyError': KeyError, 'IndexError': IndexError, 'AttributeError': AttributeError,
        'ZeroDivisionError': ZeroDivisionError,
    }

    safe_globals = {'__builtins__': safe_builtins}

    # Pre-import safe modules
    try:
        safe_globals['np'] = safe_globals['numpy'] = np
        safe_globals['pd'] = safe_globals['pandas'] = pd
    except Exception:
        pass

    local_env = {}
    try:
        exec(function_body, safe_globals, local_env)
        fn = local_env.get("compute")
        if callable(fn):
            return fn
        logger.warning(f"Function body for '{function_name}' did not define a callable 'compute'")
        return None
    except Exception as e:
        logger.warning(f"Failed to execute function body for '{function_name}': {e}")
        return None


def _load_prices(dataset_uri: Optional[str]) -> Optional[pd.DataFrame]:
    """Load price data from the dataset directory.

    Returns DataFrame with close, volume, etc. or None if not found.
    """
    if not dataset_uri:
        return None
    from pathlib import Path
    dataset_dir = Path(dataset_uri).parent
    prices_parquet = dataset_dir / "prices.parquet"
    prices_csv = dataset_dir / "prices.csv"
    try:
        if prices_parquet.exists():
            return pd.read_parquet(prices_parquet)
        elif prices_csv.exists():
            return pd.read_csv(prices_csv)
    except Exception as e:
        logger.warning(f"Could not load prices: {e}")
    return None


@dataclass
class FeatureEngineeringExperiment:
    """Experiment that creates and evaluates a new computed feature.

    The experiment computes features in-memory (no DB writes) by applying
    a function to the source column in the dataset. Each trial tests
    different function parameters.
    """
    name: str
    principle_id: str
    null_hypothesis: str
    feature_config: Dict[str, Any]  # {function_name, params}
    source_column: str
    source_table: str = "stock_ohlcv"
    risk_level: str = "medium"
    objective_metric: str = "quantile_loss"
    algorithm: str = "lightgbm"
    dataset_uri: Optional[str] = None
    horizon_days: int = 7
    cv_config: Optional[Dict[str, Any]] = None
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])
    # Holdout window (from the cycle): trials/CV never see these rows;
    # they are used exactly once, in evaluate_holdout (FR-017/019)
    holdout_start: Optional[date] = None
    holdout_end: Optional[date] = None

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

    def _load_all(self) -> tuple:
        """Load and cache (X, y, meta, prices) with row-aligned indexes.

        Prices are aligned to X rows by (symbol, date) merge — positional
        slicing is wrong whenever row order differs between files.
        """
        if self._cached_data is None:
            X, y, meta = load_dataset(self.dataset_uri, self.horizon_days, with_meta=True)
            raw_prices = _load_prices(self.dataset_uri)
            prices = None
            if raw_prices is not None and {"symbol", "date"} <= set(raw_prices.columns):
                prices = meta.merge(raw_prices, on=["symbol", "date"], how="left")
            object.__setattr__(self, "_cached_data", (X, y, meta, prices))
        return self._cached_data

    def _masks(self, meta: pd.DataFrame) -> tuple:
        """(train_mask, holdout_mask) boolean arrays over dataset rows."""
        from gefion.experiments.types.holdout_eval import holdout_masks
        return holdout_masks(meta, self.holdout_start, self.holdout_end)

    def _training_data(self) -> tuple:
        """(X, y, meta) restricted to pre-holdout rows."""
        X, y, meta, _ = self._load_all()
        train, _ = self._masks(meta)
        return (X[train].reset_index(drop=True),
                y[train].reset_index(drop=True),
                meta[train].reset_index(drop=True))

    def _compute_feature_column(self, params: Dict[str, Any]) -> pd.Series:
        """Compute the experimental feature for every dataset row.

        Computed per symbol so rolling windows never bleed across symbol
        boundaries in the concatenated frame.
        """
        X, _, meta, prices = self._load_all()
        function_name = self.feature_config.get("function_name", "rolling_zscore")
        function_body = self.feature_config.get("function_body")
        values = pd.Series(np.nan, index=range(len(X)))

        if prices is None:
            logger.warning("No price data in dataset; experimental feature will be NaN")
            return values

        if function_body:
            feat_fn = _exec_function_body(function_body, function_name)
            if feat_fn is None:
                logger.warning(f"Custom function '{function_name}' could not be loaded")
                return values
            for _, grp in prices.groupby("symbol", sort=False):
                try:
                    res = feat_fn(grp.reset_index(drop=True), **params)
                    values.iloc[grp.index] = np.asarray(res)[:len(grp)]
                except Exception as e:
                    logger.warning(f"Custom function '{function_name}' failed: {e}")
            return values

        feat_fn = _FEATURE_FUNCTIONS.get(function_name)
        if feat_fn is None:
            logger.warning(
                f"'{function_name}' is not a builtin feature function and the "
                f"experiment config has no function_body. Feature will be NaN. "
                f"Builtins: {sorted(_FEATURE_FUNCTIONS)}"
            )
            return values
        if self.source_column not in prices.columns:
            logger.warning(
                f"Source column '{self.source_column}' not found in prices. "
                f"Available: {list(prices.columns)}. Feature will be NaN."
            )
            return values
        for _, grp in prices.groupby("symbol", sort=False):
            series = feat_fn(grp[self.source_column].reset_index(drop=True), **params)
            values.iloc[grp.index] = np.asarray(series)[:len(grp)]
        return values

    def _per_symbol_pinball(self, preds: pd.DataFrame, y: pd.Series,
                            symbols: pd.Series) -> Dict[str, float]:
        """Mean pinball loss per symbol over the given predictions."""
        from gefion.experiments.types.holdout_eval import per_symbol_pinball
        return per_symbol_pinball(preds, y, symbols, self.quantiles)

    def evaluate_holdout(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Train best config on pre-holdout data, score on the holdout window.

        Fits the experimental model (with the feature) and a baseline (same
        pipeline without it) on identical pre-holdout rows, then returns
        paired per-symbol pinball losses on the holdout window for
        compute_holdout_pvalue. The holdout is touched exactly once, here.
        """
        if self.holdout_start is None or self.holdout_end is None:
            raise ValueError(
                "evaluate_holdout requires a holdout window (holdout_start/holdout_end)"
            )
        with create_span("experiments.feature_engineering.evaluate_holdout",
                         horizon_days=self.horizon_days) as span:
            X, y, meta, _ = self._load_all()
            train, hold = self._masks(meta)
            if not hold.any():
                raise ValueError(
                    f"No dataset rows fall in the holdout window "
                    f"{self.holdout_start} - {self.holdout_end}"
                )
            function_name = self.feature_config.get("function_name", "rolling_zscore")
            feature_col = f"exp_{function_name}"
            X_exp = X.copy()
            X_exp[feature_col] = self._compute_feature_column(params)

            from gefion.experiments.types.holdout_eval import (
                observations_by_date, per_row_pinball)

            y_hold = y[hold].reset_index(drop=True)
            symbols_hold = meta["symbol"][hold].reset_index(drop=True)
            dates_hold = meta["date"][hold].reset_index(drop=True)

            exp_model = train_quantile_model(
                X_exp[train], y[train], algorithm=self.algorithm, quantiles=self.quantiles)
            exp_preds = predict_quantiles(exp_model, X_exp[hold])
            exp_scores = self._per_symbol_pinball(exp_preds, y_hold, symbols_hold)

            base_model = train_quantile_model(
                X[train], y[train], algorithm=self.algorithm, quantiles=self.quantiles)
            base_preds = predict_quantiles(base_model, X[hold])
            base_scores = self._per_symbol_pinball(base_preds, y_hold, symbols_hold)

            symbols = sorted(set(base_scores) & set(exp_scores))
            result = {
                "baseline_scores": [float(base_scores[s]) for s in symbols],
                "experimental_scores": [float(exp_scores[s]) for s in symbols],
                "symbols": symbols,
                "n_symbols": len(symbols),
                "holdout_rows": int(hold.sum()),
                "train_rows": int(train.sum()),
                "observations": observations_by_date(
                    per_row_pinball(base_preds, y_hold, self.quantiles),
                    per_row_pinball(exp_preds, y_hold, self.quantiles),
                    dates_hold),
            }
            set_attributes(span, n_symbols=len(symbols), holdout_rows=int(hold.sum()))
            return result

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """Compute experimental feature with given params, train, and evaluate.

        Args:
            params: Feature function parameters (e.g., {"window": 20}).

        Returns:
            Dict of averaged CV metrics including quantile_loss.
        """
        function_name = self.feature_config.get("function_name", "rolling_zscore")
        cv_cfg = self.cv_config or {"n_splits": 5, "embargo_pct": 0.0, "prediction_horizon": 0}

        with create_span(
            "experiments.feature_engineering.evaluate",
            function_name=function_name,
            horizon_days=self.horizon_days,
        ) as span:
            # Pre-holdout rows only (FR-017): trials must never see holdout data
            X_full, y_full, meta, _ = self._load_all()
            train, _ = self._masks(meta)
            feature_col = f"exp_{function_name}"
            feature_full = self._compute_feature_column(params)

            X = X_full[train].reset_index(drop=True).copy()
            y = y_full[train].reset_index(drop=True)
            X[feature_col] = feature_full.values[train]

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

            set_attributes(span, function_name=function_name,
                           feature_col=feature_col, n_folds=len(all_fold_metrics))

            return avg_metrics

    def to_experiment_config(self) -> ExperimentConfig:
        """Convert to a serializable ExperimentConfig for storage."""
        return ExperimentConfig(
            name=self.name,
            experiment_type="feature_engineering",
            search_space=self.feature_config.get("params", {}),
            principle_id=self.principle_id,
            null_hypothesis=self.null_hypothesis,
            objective_metric=self.objective_metric,
            cv_config=self.cv_config,
            extra_config={
                "feature_config": self.feature_config,
                "source_column": self.source_column,
                "source_table": self.source_table,
                "algorithm": self.algorithm,
                "dataset_uri": self.dataset_uri,
                "horizon_days": self.horizon_days,
                "quantiles": self.quantiles,
            },
        )
