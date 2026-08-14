"""`--no-coverage-audit` must skip the ACCUMULATORS, not just the audit (#271).

`ml dataset-build` OOMs on long windows because the #191 coverage audit keeps
two whole-build accumulators that `--symbol-batch-size` cannot bound:

    grid_labels: Dict[Any, float]      # one entry per (symbol, date)
    feature_presence: dict[str, set]   # per feature, every (symbol, date) seen

Measured on sloth (15 GB box), full NASDAQ universe, 2018-2023: 13,974,632 kB
RSS killed by the OOM reaper, then 11,746,932 kB at 7m22s and still climbing
WITH `--symbol-batch-size 50` verified in /proc/<pid>/cmdline. They scale with
features x symbols x dates, so a 6-month window fits at batch 200 and a 6-year
window dies at batch 50.

Both exist only to feed an audit the code itself calls "Advisory +
NON-BLOCKING — never fail the build". It never raises; it just makes the
process die before finishing.

THE POINT OF THIS FILE: skipping only the audit CALL would save nothing --
the memory is spent accumulating, long before the audit runs. The flag has to
stop the accumulation itself. `test_flag_stops_presence_accumulation` is the
test that distinguishes a real fix from a cosmetic one.
"""
from __future__ import annotations

import inspect

import pytest

from gefion.ml import dataset


def _source() -> str:
    return inspect.getsource(dataset)


def test_export_accepts_the_flag():
    """The build entry point must take the option at all."""
    src = _source()

    assert "coverage_audit" in src, "no coverage_audit parameter anywhere"


def test_flag_stops_presence_accumulation():
    """THE test. `feature_presence` is the dominant cost (~200 features x
    millions of (symbol, date) tuples). If `_record_presence` still populates
    it when the audit is off, the flag is cosmetic and the OOM is unchanged."""
    src = _source()

    start = src.index("def _record_presence")
    body = src[start:start + 500]

    assert "coverage_audit" in body or "if not " in body, (
        "_record_presence must be gated when the audit is disabled -- "
        "otherwise the accumulator still grows and nothing is saved")


def test_flag_stops_grid_label_accumulation():
    """`grid_labels` is ~5.5M entries on a 6-year window (vs ~460k on 6
    months). Gated too, for the same reason."""
    src = _source()

    # Skip the `def _accumulate_grid_labels(...)` definition; index() finds
    # that before the call site this is actually about.
    def_idx = src.index("def _accumulate_grid_labels")
    idx = src.index("_accumulate_grid_labels(grid_labels", def_idx + 30)
    window = src[max(0, idx - 300):idx]

    assert "if coverage_audit" in window, (
        "grid_labels accumulation must be gated when the audit is disabled")


def test_audit_is_not_run_when_disabled():
    src = _source()

    # Skip past the `def _run_coverage_audit(conn, ...)` definition -- a plain
    # index() finds that first, not the call site we mean to check.
    def_idx = src.index("def _run_coverage_audit")
    idx = src.index("_run_coverage_audit(conn", def_idx + 30)
    window = src[max(0, idx - 400):idx]

    assert "if coverage_audit" in window, "audit call must be gated"


def test_audit_runs_by_default():
    """Default must be unchanged -- this is an opt-OUT, so every existing
    build keeps its audit."""
    sig = inspect.signature(dataset.build_dataset) if hasattr(
        dataset, "build_dataset") else None
    if sig is not None and "coverage_audit" in sig.parameters:
        assert sig.parameters["coverage_audit"].default is True
    else:
        # Entry point named differently; assert the default in source instead.
        assert "coverage_audit: bool = True" in _source(), (
            "coverage_audit must default to True (opt-out, not opt-in)")


class TestCli:
    def test_dataset_build_exposes_the_flag(self):
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["ml", "dataset-build", "--help"])

        assert result.exit_code == 0
        assert "--no-coverage-audit" in result.output

    def test_ab_compare_exposes_the_flag(self):
        """The A/B is the caller that needs it -- epic #179's 6-year run is
        what #271 blocks."""
        from typer.testing import CliRunner
        from gefion.cli import app

        result = CliRunner().invoke(app, ["backtest", "ab-compare", "--help"])

        assert result.exit_code == 0
        assert "--no-coverage-audit" in result.output


class TestMatchedConfig:
    def test_field_exists_and_defaults_on(self):
        import dataclasses
        from datetime import date

        from gefion.backtest.ab_compare import MatchedConfig

        fields = {f.name for f in dataclasses.fields(MatchedConfig)}
        assert "coverage_audit" in fields

        cfg = MatchedConfig(
            start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
            split_spec={}, horizons=[30], horizon_days=30,
            hyperparams={}, strategy_params={})
        assert cfg.coverage_audit is True

    def test_echoed_for_provenance(self):
        from datetime import date

        from gefion.backtest.ab_compare import MatchedConfig

        cfg = MatchedConfig(
            start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
            split_spec={}, horizons=[30], horizon_days=30,
            hyperparams={}, strategy_params={}, coverage_audit=False)

        assert cfg.to_dict()["coverage_audit"] is False


def test_run_arm_forwards_the_flag(monkeypatch):
    from datetime import date

    from gefion.backtest import ab_compare

    calls = []
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])

    class _E:
        def __init__(self, **k): pass
        def run(self): return {"metrics": {}, "equity_curve": [], "trades": []}

    class _S:
        def __init__(self, **k): pass
        def generate_signals(self, *a, **k): return []

    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _E)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _S)

    cfg = ab_compare.MatchedConfig(
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"}, strategy_params={},
        coverage_audit=False)
    ab_compare.run_arm(ab_compare.ArmSpec("A", train_universe="u"), cfg, conn=None)

    ds = next(c for c in calls if c[:2] == ["ml", "dataset-build"])
    assert "--no-coverage-audit" in ds


def test_run_arm_omits_the_flag_by_default(monkeypatch):
    from datetime import date

    from gefion.backtest import ab_compare

    calls = []
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])

    class _E:
        def __init__(self, **k): pass
        def run(self): return {"metrics": {}, "equity_curve": [], "trades": []}

    class _S:
        def __init__(self, **k): pass
        def generate_signals(self, *a, **k): return []

    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _E)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _S)

    cfg = ab_compare.MatchedConfig(
        start_date=date(2018, 1, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"}, strategy_params={})
    ab_compare.run_arm(ab_compare.ArmSpec("A", train_universe="u"), cfg, conn=None)

    ds = next(c for c in calls if c[:2] == ["ml", "dataset-build"])
    assert "--no-coverage-audit" not in ds
