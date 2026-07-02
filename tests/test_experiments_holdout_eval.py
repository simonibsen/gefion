"""Tests for real holdout evaluation in experiment cycles (step 2).

FR-017/019: training and trials must never see holdout rows; the holdout is
used exactly once, at final evaluation, to produce a genuine p-value that the
fail-closed FDR gate consumes.

TDD: Tests written first, before implementation.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def tiny_dataset(tmp_path):
    """A small on-disk dataset: 4 symbols x 120 days, one feature, 7d labels."""
    rng = np.random.default_rng(42)
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    dates = pd.bdate_range("2026-01-01", periods=120).date

    feat_rows, label_rows, price_rows = [], [], []
    for sym in symbols:
        base = rng.uniform(50, 150)
        closes = base * np.cumprod(1 + rng.normal(0.0005, 0.02, len(dates)))
        for i, d in enumerate(dates):
            feat_rows.append({"symbol": sym, "date": d,
                              "feature_name": "indicator_rsi_14",
                              "value": float(rng.uniform(20, 80))})
            label_rows.append({"symbol": sym, "date": d, "horizon_days": 7,
                               "forward_return": float(rng.normal(0, 0.03))})
            price_rows.append({"symbol": sym, "date": d,
                               "open": closes[i], "high": closes[i] * 1.01,
                               "low": closes[i] * 0.99, "close": closes[i],
                               "volume": 1_000_000})

    ds_dir = tmp_path / "tiny_v1"
    ds_dir.mkdir()
    pd.DataFrame(feat_rows).to_parquet(ds_dir / "features.parquet")
    pd.DataFrame(label_rows).to_parquet(ds_dir / "labels.parquet")
    pd.DataFrame(price_rows).to_parquet(ds_dir / "prices.parquet")
    manifest = ds_dir / "manifest.json"
    manifest.write_text(json.dumps({"name": "tiny", "format": "parquet",
                                    "horizons_days": [7]}))
    return {"uri": str(manifest), "dates": dates, "symbols": symbols}


class TestLoadDatasetWithMeta:
    """load_dataset must optionally expose symbol/date aligned to X rows."""

    def test_with_meta_returns_aligned_frames(self, tiny_dataset):
        from gefion.ml.models import load_dataset

        X, y, meta = load_dataset(tiny_dataset["uri"], 7, with_meta=True)

        assert len(X) == len(y) == len(meta)
        assert {"symbol", "date"} <= set(meta.columns)
        assert "symbol" not in X.columns and "date" not in X.columns

    def test_default_signature_unchanged(self, tiny_dataset):
        from gefion.ml.models import load_dataset

        result = load_dataset(tiny_dataset["uri"], 7)
        assert len(result) == 2


class TestTrialsExcludeHoldout:
    """Trials/CV must only ever see pre-holdout rows (FR-017)."""

    def _experiment(self, tiny_dataset, holdout_start, holdout_end):
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        return FeatureEngineeringExperiment(
            name="holdout-split-test",
            principle_id="p",
            null_hypothesis="h",
            feature_config={"function_name": "variance_ratio"},
            source_column="close",
            algorithm="lightgbm",
            cv_config={"n_splits": 3, "embargo_pct": 0.0},
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            quantiles=[0.1, 0.5, 0.9],
            holdout_start=holdout_start,
            holdout_end=holdout_end,
        )

    def test_training_rows_end_before_holdout(self, tiny_dataset):
        dates = tiny_dataset["dates"]
        holdout_start = dates[-30]
        exp = self._experiment(tiny_dataset, holdout_start, dates[-1])

        X_train, y_train, meta_train = exp._training_data()

        assert meta_train["date"].max() < holdout_start
        # 4 symbols x 90 pre-holdout days
        assert len(X_train) == 4 * 90

    def test_no_holdout_uses_all_rows(self, tiny_dataset):
        exp = self._experiment(tiny_dataset, None, None)

        X_train, y_train, meta_train = exp._training_data()

        assert len(X_train) == 4 * 120


class TestEvaluateHoldout:
    """Holdout evaluation produces paired per-symbol scores."""

    def test_returns_paired_per_symbol_scores(self, tiny_dataset):
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        dates = tiny_dataset["dates"]
        exp = FeatureEngineeringExperiment(
            name="holdout-eval-test",
            principle_id="p",
            null_hypothesis="h",
            feature_config={"function_name": "variance_ratio"},
            source_column="close",
            algorithm="lightgbm",
            cv_config={"n_splits": 3, "embargo_pct": 0.0},
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            quantiles=[0.1, 0.5, 0.9],
            holdout_start=dates[-30],
            holdout_end=dates[-1],
        )

        result = exp.evaluate_holdout({"window": 10})

        baseline = result["baseline_scores"]
        experimental = result["experimental_scores"]
        assert len(baseline) == len(experimental) == 4  # paired, one per symbol
        assert all(isinstance(s, float) for s in baseline + experimental)
        assert result["n_symbols"] == 4

    def test_requires_holdout_window(self, tiny_dataset):
        from gefion.experiments.types.feature_engineering import FeatureEngineeringExperiment

        exp = FeatureEngineeringExperiment(
            name="no-holdout",
            principle_id="p",
            null_hypothesis="h",
            feature_config={"function_name": "variance_ratio"},
            source_column="close",
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            holdout_start=None,
            holdout_end=None,
        )

        with pytest.raises(ValueError, match="holdout"):
            exp.evaluate_holdout({})


class TestRunnerHoldoutIntegration:
    """ExperimentRunner.run must holdout-evaluate cycle experiments."""

    def test_run_computes_and_stores_pvalue(self):
        """Source-level: run() wires evaluate_holdout -> compute_holdout_pvalue -> DB."""
        import inspect
        from gefion.experiments.core import ExperimentRunner

        source = inspect.getsource(ExperimentRunner.run)
        assert "evaluate_holdout" in source
        assert "compute_holdout_pvalue" in source
        assert "holdout_p_value" in source

    def test_runner_loads_cycle_holdout_window(self):
        """Cycle-linked experiments get their holdout dates from the cycle row."""
        import inspect
        from gefion.experiments import core

        source = inspect.getsource(core)
        assert "holdout_start_date" in source
