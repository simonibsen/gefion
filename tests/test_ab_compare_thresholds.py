"""Regression test: the A/B harness must pass per-horizon class thresholds to
`ml dataset-build` (issue: the shipped #197 harness omitted them, and
dataset-build has no default, so the harness failed on its very first step —
"Threshold list length mismatch: provide one weak+strong threshold per
horizon"). No DB needed: the subprocess and backtest internals are stubbed.
"""
from datetime import date

import pytest

from gefion.backtest import ab_compare


class _FakeEngine:
    def __init__(self, **kwargs):
        pass

    def run(self):
        return {"metrics": {}, "equity_curve": [], "trades": []}


class _FakeStrat:
    def __init__(self, **kwargs):
        pass

    def generate_signals(self, *args, **kwargs):
        return []


@pytest.fixture
def captured_cli(monkeypatch):
    """Stub the subprocess CLI + backtest internals; capture CLI commands."""
    calls = []
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest",
        lambda *a, **k: [])
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy",
                        _FakeStrat)
    return calls


def _config(horizons, weak=None, strong=None):
    return ab_compare.MatchedConfig(
        start_date=date(2020, 1, 1), end_date=date(2020, 6, 30),
        split_spec={}, horizons=horizons, horizon_days=horizons[-1],
        hyperparams={"algorithm": "xgboost"}, strategy_params={},
        weak_thresholds=weak, strong_thresholds=strong)


def _dataset_build_cmd(calls):
    return next(c for c in calls if "dataset-build" in c)


def test_run_arm_passes_default_thresholds_one_per_horizon(captured_cli):
    ab_compare.run_arm(ab_compare.ArmSpec("A", train_universe="u"),
                       _config([7, 30]), conn=None)
    cmd = _dataset_build_cmd(captured_cli)
    assert "--weak-thresholds" in cmd
    assert "--strong-thresholds" in cmd
    # one threshold per horizon (the mismatch that broke the harness)
    weak = cmd[cmd.index("--weak-thresholds") + 1].split(",")
    strong = cmd[cmd.index("--strong-thresholds") + 1].split(",")
    assert len(weak) == 2 and len(strong) == 2
    assert weak == ["0.02", "0.02"] and strong == ["0.05", "0.05"]


def test_run_arm_honors_explicit_thresholds(captured_cli):
    ab_compare.run_arm(
        ab_compare.ArmSpec("A", train_universe="u"),
        _config([7, 30], weak=[0.01, 0.03], strong=[0.04, 0.08]), conn=None)
    cmd = _dataset_build_cmd(captured_cli)
    assert cmd[cmd.index("--weak-thresholds") + 1] == "0.01,0.03"
    assert cmd[cmd.index("--strong-thresholds") + 1] == "0.04,0.08"
