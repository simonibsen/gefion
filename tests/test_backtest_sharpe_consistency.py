"""Regression tests for issue #248: the A/B report published a positive
Sharpe (+1.196) alongside a -204% total return for the same arm/equity
curve -- a mathematical impossibility.

Root cause: `_calculate_sharpe_ratio` computes daily pct returns but only
includes a day when the PRIOR day's equity was positive. Once a collapsing,
unbounded (#217 gross-exposure has no margin-call/liquidation floor) equity
curve goes non-positive, every subsequent day is silently dropped from the
sample -- the reported ratio ends up describing only the early, healthier
slice of the run, decoupled from the actual (deeply negative) outcome.

The fix returns ``float('nan')`` (not ``None``) for every degenerate case:
NaN is still a real float, so the many other callers of `calculate_metrics`
across the codebase (CLI tables, UI views, strategy ranking/sorting) that
format or compare `sharpe_ratio` as a plain number keep working unchanged,
while NaN is unambiguously "not a number" rather than a misleading 0.0.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from gefion.backtest.metrics import _calculate_sharpe_ratio


def _curve(equities, start=date(2020, 1, 1)):
    return [{"date": start + timedelta(days=i), "equity": e}
            for i, e in enumerate(equities)]


class TestSharpeConsistency:
    def test_nan_when_equity_curve_goes_non_positive(self):
        """The reported case: a wildly volatile curve that ends deeply
        negative must not yield a finite (let alone positive) Sharpe.

        Large, mostly-positive daily swings (an unbounded, un-margin-called
        short blowup, #217, can mark equity down past zero in a single bar)
        with one final collapse past zero -- the exact shape that let the
        old code silently drop every post-collapse day and compute a
        healthy-looking Sharpe from the survivorship-biased remainder.
        """
        equities = [100000.0, 250000.0, 50000.0, 300000.0, 20000.0,
                    400000.0, 10000.0, -80000.0]
        curve = _curve(equities)

        total_return = (curve[-1]["equity"] - curve[0]["equity"]) / curve[0]["equity"]
        assert total_return < 0  # sanity: this run lost money overall
        assert curve[-1]["equity"] < 0  # sanity: equity actually went negative

        assert math.isnan(_calculate_sharpe_ratio(curve))

    def test_zero_variance_returns_sentinel_not_zero(self):
        """A flat curve has an undefined risk-adjusted ratio -- 0.0 reads as
        'market neutral performance', which is a different claim."""
        curve = _curve([100000.0, 100000.0, 100000.0, 100000.0])
        assert math.isnan(_calculate_sharpe_ratio(curve))

    def test_single_point_curve_returns_sentinel(self):
        assert math.isnan(_calculate_sharpe_ratio(_curve([100000.0])))

    def test_empty_curve_returns_sentinel(self):
        assert math.isnan(_calculate_sharpe_ratio([]))

    def test_well_behaved_curve_still_computes_a_real_ratio(self):
        """Normal path (no non-positive equity, real variance) is unaffected."""
        curve = _curve([100000.0, 101000.0, 100500.0, 102000.0, 103500.0])
        sharpe = _calculate_sharpe_ratio(curve)
        assert not math.isnan(sharpe)
        assert sharpe > 0  # this curve is monotonically up overall

    def test_sharpe_never_positive_when_equity_curve_lost_money(self):
        """General consistency check (not just the one reported curve):
        for any curve that stays positive throughout but nets a loss, the
        Sharpe must not be positive -- the sign must track the outcome."""
        curve = _curve([100000.0, 105000.0, 95000.0, 90000.0, 80000.0])
        sharpe = _calculate_sharpe_ratio(curve)
        assert not math.isnan(sharpe)
        assert sharpe <= 0
