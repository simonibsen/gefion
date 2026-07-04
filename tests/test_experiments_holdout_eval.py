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


class TestHyperparameterHoldout:
    """Hyperparameter experiments must earn holdout p-values too."""

    def _experiment(self, tiny_dataset, holdout_start, holdout_end):
        from gefion.experiments.types.hyperparameter import HyperparameterExperiment

        return HyperparameterExperiment(
            name="hp-holdout-test",
            model_type="lightgbm",
            search_space={"max_depth": {"type": "int", "low": 2, "high": 6}},
            cv_config={"n_splits": 3, "embargo_pct": 0.0},
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            quantiles=[0.1, 0.5, 0.9],
            holdout_start=holdout_start,
            holdout_end=holdout_end,
        )

    def test_trials_exclude_holdout_rows(self, tiny_dataset):
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        X_train, y_train, meta_train = exp._training_data()

        assert meta_train["date"].max() < dates[-30]
        assert len(X_train) == 4 * 90

    def test_evaluate_holdout_pairs_best_vs_default_params(self, tiny_dataset):
        """Experimental = best params; baseline = library defaults."""
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        result = exp.evaluate_holdout({"max_depth": 3, "n_estimators": 60})

        assert len(result["baseline_scores"]) == len(result["experimental_scores"]) == 4
        assert result["n_symbols"] == 4
        assert result["holdout_rows"] > 0

    def test_requires_holdout_window(self, tiny_dataset):
        exp = self._experiment(tiny_dataset, None, None)

        with pytest.raises(ValueError, match="holdout"):
            exp.evaluate_holdout({})


class TestRunnerPassesHoldoutToHyperparameter:
    def test_hyperparameter_dispatch_receives_window(self):
        import inspect
        from gefion.experiments.core import ExperimentRunner

        source = inspect.getsource(ExperimentRunner.run)
        idx = source.index('experiment_type"] == "hyperparameter"')
        block = source[idx:idx + 900]
        assert "holdout_start=holdout_start" in block


class TestModelComparisonHoldout:
    """Model comparison experiments must earn holdout p-values too."""

    def _experiment(self, tiny_dataset, holdout_start, holdout_end):
        from gefion.experiments.types.model_comparison import ModelComparisonExperiment

        return ModelComparisonExperiment(
            name="mc-holdout-test",
            model_types=["lightgbm", "xgboost"],
            cv_config={"n_splits": 3, "embargo_pct": 0.0},
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            quantiles=[0.1, 0.5, 0.9],
            holdout_start=holdout_start,
            holdout_end=holdout_end,
        )

    def test_trials_exclude_holdout_rows(self, tiny_dataset):
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        X_train, y_train, meta_train = exp._training_data()

        assert meta_train["date"].max() < dates[-30]
        assert len(X_train) == 4 * 90

    def test_evaluate_holdout_pairs_winner_vs_incumbent(self, tiny_dataset):
        """Experimental = winning model type; baseline = incumbent algorithm."""
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        result = exp.evaluate_holdout({"model_type": "xgboost"})

        assert len(result["baseline_scores"]) == len(result["experimental_scores"]) == 4
        assert result["n_symbols"] == 4
        assert result["holdout_rows"] > 0

    def test_incumbent_winning_yields_identical_scores(self, tiny_dataset):
        """If the incumbent wins the comparison there is no improvement to
        promote — paired scores are identical, so the t-test yields p=1.0
        and FDR correctly rejects. Honest semantics, not a bug."""
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        result = exp.evaluate_holdout({"model_type": "lightgbm"})

        assert result["baseline_scores"] == pytest.approx(result["experimental_scores"])

    def test_requires_holdout_window(self, tiny_dataset):
        exp = self._experiment(tiny_dataset, None, None)

        with pytest.raises(ValueError, match="holdout"):
            exp.evaluate_holdout({"model_type": "xgboost"})


class TestRunnerPassesHoldoutToModelComparison:
    def test_model_comparison_dispatch_receives_window(self):
        import inspect
        from gefion.experiments.core import ExperimentRunner

        source = inspect.getsource(ExperimentRunner.run)
        idx = source.index('experiment_type"] == "model_comparison"')
        block = source[idx:idx + 900]
        assert "holdout_start=holdout_start" in block


class TestPvalueDirection:
    """The gate must be one-sided: only IMPROVEMENT earns a small p-value.

    Two-sided testing let significantly WORSE experiments survive FDR.
    """

    def test_worse_loss_scores_do_not_earn_small_pvalue(self):
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [0.030, 0.031, 0.029, 0.030, 0.032, 0.031]
        much_worse = [0.050, 0.052, 0.049, 0.051, 0.053, 0.050]
        p = compute_holdout_pvalue(baseline, much_worse, alternative="less")
        assert p > 0.5  # worse must never look significant

    def test_better_loss_scores_earn_small_pvalue(self):
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [0.050, 0.052, 0.049, 0.051, 0.053, 0.050]
        better = [0.030, 0.031, 0.029, 0.030, 0.032, 0.031]
        p = compute_holdout_pvalue(baseline, better, alternative="less")
        assert p < 0.01

    def test_greater_direction_for_return_scores(self):
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [0.001, 0.002, 0.001, 0.0, 0.002, 0.001]
        higher = [0.010, 0.012, 0.011, 0.009, 0.012, 0.010]
        assert compute_holdout_pvalue(baseline, higher, alternative="greater") < 0.01
        assert compute_holdout_pvalue(higher, baseline, alternative="greater") > 0.5

    def test_default_is_one_sided_less(self):
        """Default matches the loss semantics of all existing callers."""
        from gefion.experiments.statistical import compute_holdout_pvalue

        baseline = [0.030, 0.031, 0.029, 0.030, 0.032, 0.031]
        much_worse = [0.050, 0.052, 0.049, 0.051, 0.053, 0.050]
        assert compute_holdout_pvalue(baseline, much_worse) > 0.5


class TestLabelEngineeringHoldout:
    """Label experiments are evaluated via trading outcomes (FR-013)."""

    def _experiment(self, tiny_dataset, holdout_start, holdout_end):
        from gefion.experiments.types.label_engineering import LabelEngineeringExperiment

        return LabelEngineeringExperiment(
            name="label-holdout-test",
            principle_id="p",
            null_hypothesis="h",
            label_type="winsorized",
            algorithm="lightgbm",
            cv_config={"n_splits": 3, "embargo_pct": 0.0},
            dataset_uri=tiny_dataset["uri"],
            horizon_days=7,
            quantiles=[0.1, 0.5, 0.9],
            holdout_start=holdout_start,
            holdout_end=holdout_end,
        )

    def test_trials_exclude_holdout_rows(self, tiny_dataset):
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        X_train, y_train, meta_train = exp._training_data()

        assert meta_train["date"].max() < dates[-30]
        assert len(X_train) == 4 * 90

    def test_evaluate_holdout_scores_realized_returns_per_date(self, tiny_dataset):
        """Both arms are judged on the SAME outcome variable — realized raw
        forward returns of the stocks each arm's signal selects per date —
        never on prediction metrics against different targets."""
        dates = tiny_dataset["dates"]
        exp = self._experiment(tiny_dataset, dates[-30], dates[-1])

        result = exp.evaluate_holdout({"label_type": "winsorized"})

        assert len(result["baseline_scores"]) == len(result["experimental_scores"])
        assert len(result["baseline_scores"]) == 30  # paired per holdout date
        assert result["alternative"] == "greater"  # returns: higher is better
        assert result["score_kind"] == "portfolio_forward_return"

    def test_requires_holdout_window(self, tiny_dataset):
        exp = self._experiment(tiny_dataset, None, None)

        with pytest.raises(ValueError, match="holdout"):
            exp.evaluate_holdout({})


class TestRunnerPassesHoldoutToLabelEngineering:
    def test_label_engineering_dispatch_receives_window(self):
        import inspect
        from gefion.experiments.core import ExperimentRunner

        source = inspect.getsource(ExperimentRunner.run)
        idx = source.index('experiment_type"] == "label_engineering"')
        block = source[idx:idx + 1400]
        assert "holdout_start=holdout_start" in block

    def test_runner_respects_evaluator_alternative(self):
        """The runner must use the evaluator's declared score direction."""
        import inspect
        from gefion.experiments.core import ExperimentRunner

        source = inspect.getsource(ExperimentRunner.run)
        assert 'alternative' in source


# --- per-date holdout scores for regime-conditional evaluation (005 T034) ---

def test_paired_result_by_date_produces_per_observation_records():
    import pandas as pd
    from datetime import date
    from gefion.experiments.types.holdout_eval import per_row_pinball, paired_result_by_date

    y = pd.Series([1.0, 2.0, 3.0])
    preds = pd.DataFrame({"q10": [0.9, 2.1, 2.8], "q50": [1.0, 2.0, 3.0], "q90": [1.1, 1.9, 3.2]})
    base_loss = per_row_pinball(preds, y, [0.1, 0.5, 0.9])
    assert len(base_loss) == 3  # one loss per row (not grouped by symbol)

    dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    out = paired_result_by_date(base_loss, base_loss, dates, holdout_rows=3)
    assert "observations" in out and len(out["observations"]) == 3
    rec = out["observations"][0]
    assert set(rec) == {"date", "baseline_score", "experimental_score"}
    assert rec["date"] == dates[0]
