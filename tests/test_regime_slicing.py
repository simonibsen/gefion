"""Tests for regime-sliced backtest metrics (005 T020).

Pure computation over a synthetic equity curve + trades + labels; verifies
per-regime metrics, the reconciliation guarantee (FR-009), and low-power flags.
"""
import datetime as dt

from gefion.regimes.slicing import slice_backtest_by_regime, daily_returns


def _dates(n):
    d0 = dt.date(2024, 1, 1)
    return [d0 + dt.timedelta(days=i) for i in range(n)]


def _fixture():
    d = _dates(6)
    equity = [100.0, 110.0, 121.0, 108.9, 119.79, 131.769]
    curve = [{"date": d[i], "equity": equity[i]} for i in range(6)]
    labels = {d[1]: "calm", d[2]: "calm", d[3]: "stressed", d[4]: "stressed", d[5]: "stressed"}
    trades = [
        {"date": d[1], "pnl": 5.0}, {"date": d[2], "pnl": -2.0}, {"date": d[4], "pnl": 3.0},
    ]
    return curve, trades, labels


def test_daily_returns_length_and_values():
    curve, _, _ = _fixture()
    rets = daily_returns(curve)
    assert len(rets) == 5
    assert abs(rets[0][1] - 0.10) < 1e-9


def test_per_bucket_metrics_present():
    curve, trades, labels = _fixture()
    out = slice_backtest_by_regime(curve, trades, labels, initial_capital=100.0,
                                   min_effective_n=1)
    buckets = out["buckets"]
    assert set(buckets) == {"calm", "stressed"}
    for b in buckets.values():
        for k in ("total_return", "sharpe_ratio", "max_drawdown", "trade_count",
                  "raw_n", "effective_n"):
            assert k in b


def test_bucket_returns_are_correct():
    curve, trades, labels = _fixture()
    out = slice_backtest_by_regime(curve, trades, labels, initial_capital=100.0,
                                   min_effective_n=1)
    assert abs(out["buckets"]["calm"]["total_return"] - 0.21) < 1e-6
    assert abs(out["buckets"]["stressed"]["total_return"] - 0.089) < 1e-6


def test_reconciliation_growth_and_trades():
    curve, trades, labels = _fixture()
    out = slice_backtest_by_regime(curve, trades, labels, initial_capital=100.0,
                                   min_effective_n=1)
    assert out["reconciliation_ok"] is True
    # trade counts sum to total
    assert sum(b["trade_count"] for b in out["buckets"].values()) == 3


def test_low_power_flag_when_threshold_high():
    curve, trades, labels = _fixture()
    out = slice_backtest_by_regime(curve, trades, labels, initial_capital=100.0,
                                   min_effective_n=20)
    # each bucket is a single episode → effective_n 1 < 20 → flagged
    assert all(b["low_power"] for b in out["buckets"].values())


def test_not_low_power_when_threshold_met():
    curve, trades, labels = _fixture()
    out = slice_backtest_by_regime(curve, trades, labels, initial_capital=100.0,
                                   min_effective_n=1)
    assert all(not b["low_power"] for b in out["buckets"].values())
