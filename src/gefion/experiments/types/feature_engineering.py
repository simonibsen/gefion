"""Feature engineering experiment type.

Creates and evaluates new computed features within the experiment sandbox.
Features are tagged as experimental until auto-promoted via statistical gates.
"""
import logging
from dataclasses import dataclass, field
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

    _cached_data: Optional[tuple] = field(default=None, repr=False, compare=False)

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
            # Load dataset + prices (cache across trials)
            if self._cached_data is None:
                X, y = load_dataset(self.dataset_uri, self.horizon_days)
                prices = _load_prices(self.dataset_uri)
                object.__setattr__(self, "_cached_data", (X, y, prices))
            X_base, y, prices = self._cached_data

            # Compute experimental feature
            X = X_base.copy()
            feature_col = f"exp_{function_name}"
            function_body = self.feature_config.get("function_body")

            if function_body:
                # Custom function body — execute in security sandbox
                feat_fn = _exec_function_body(function_body, function_name)
                if feat_fn is not None and prices is not None:
                    # Build a DataFrame with price data for the function
                    price_df = prices.iloc[:len(X)].reset_index(drop=True)
                    try:
                        result = feat_fn(price_df, **params)
                        X[feature_col] = result.values if hasattr(result, 'values') else result
                    except Exception as e:
                        logger.warning(f"Custom function '{function_name}' failed: {e}")
                        X[feature_col] = np.nan
                else:
                    logger.warning(f"Custom function '{function_name}' could not be loaded or no price data")
                    X[feature_col] = np.nan
            else:
                # Builtin function from _FEATURE_FUNCTIONS
                feat_fn = _FEATURE_FUNCTIONS.get(function_name)

                # Source column comes from prices (close, volume, etc.), not features
                if feat_fn is not None and prices is not None and self.source_column in prices.columns:
                    source_series = prices[self.source_column].iloc[:len(X)].reset_index(drop=True)
                    X[feature_col] = feat_fn(source_series, **params)
                elif feat_fn is not None and self.source_column in X.columns:
                    X[feature_col] = feat_fn(X[self.source_column], **params)
                else:
                    logger.warning(
                        f"Source column '{self.source_column}' not found in prices or features. "
                        f"Available price columns: {list(prices.columns) if prices is not None else 'none'}. "
                        f"Feature will be NaN."
                    )
                    X[feature_col] = np.nan

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
