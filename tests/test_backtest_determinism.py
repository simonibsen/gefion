"""Determinism regression tests (highest-priority defect: same config, same
window, same universe must produce the same numbers on every run).

Root cause: `get_predictions_for_date`'s SQL had no tiebreaker on its
`ORDER BY q50 DESC`, so Postgres could return equal-q50 rows in any order
across runs/plans. `predictions` is built as a dict from that row order, and
the later `buy_candidates.sort(key=..., reverse=True)` is a *stable* sort --
ties silently inherited the arbitrary DB row order, which changes WHICH names
are held at a `max_positions` boundary.

These tests never touch a real database: they mock `get_predictions_for_date`
directly and control the dict order it returns, simulating the two orders
Postgres could hand back for an identical, tied query.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from gefion.backtest.engine import BacktestEngine
from gefion.strategies.ml_signal import MLSignalStrategy


def _price_data(symbols, as_of):
    return [{"symbol": s, "date": as_of, "close": 100.0} for s in symbols]


def _run_backtest(tied_predictions, prediction_date, trading_date):
    """Run a one-day backtest where `tied_predictions` (a dict, in whatever
    key order the caller passes) is returned for `prediction_date`."""
    symbols = list(tied_predictions.keys())
    price_data = _price_data(symbols, trading_date)

    strat = MLSignalStrategy(
        model_name="test",
        model_version="v1",
        return_threshold=0.02,
        downside_limit=-0.5,
        max_positions=2,
        position_size=0.1,
    )

    def strategy_fn(current_date, portfolio, prices):
        return strat.generate_signals(
            current_date=current_date,
            portfolio=portfolio,
            price_data=prices,
            initial_cash=100_000.0,
        )

    def fake_get_predictions(db_url, model_name, model_version, pred_date, horizon_days):
        return tied_predictions if pred_date == prediction_date else {}

    with patch(
        "gefion.strategies.ml_signal.get_predictions_for_date",
        side_effect=fake_get_predictions,
    ):
        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy_fn,
            initial_cash=100_000.0,
            start_date=trading_date,
            end_date=trading_date,
        )
        return engine.run()


class TestBacktestDeterminism:
    """4 symbols tied at q50, max_positions=2 -- a tie sits exactly at the
    boundary that decides which 2 of the 4 get bought."""

    TRADING_DATE = date(2024, 1, 2)
    PREDICTION_DATE = TRADING_DATE - timedelta(days=1)

    @staticmethod
    def _tied_predictions():
        return {
            "AAA": {"q10": 0.01, "q50": 0.10, "q90": 0.20},
            "BBB": {"q10": 0.01, "q50": 0.10, "q90": 0.20},
            "CCC": {"q10": 0.01, "q50": 0.10, "q90": 0.20},
            "DDD": {"q10": 0.01, "q50": 0.10, "q90": 0.20},
        }

    def test_same_process_repeat_run_is_identical(self):
        """Running the same backtest twice, same predictions dict order,
        must produce byte-identical results. (Passes even on broken code --
        see the reverse-order test below for the assertion that pins the fix.)"""
        preds = self._tied_predictions()

        result1 = _run_backtest(preds, self.PREDICTION_DATE, self.TRADING_DATE)
        result2 = _run_backtest(preds, self.PREDICTION_DATE, self.TRADING_DATE)

        assert result1["equity_curve"] == result2["equity_curve"]
        assert result1["trades"] == result2["trades"]

        opened1 = [t["symbol"] for t in result1["trades"] if t["action"] == "buy"]
        opened2 = [t["symbol"] for t in result2["trades"] if t["action"] == "buy"]
        assert opened1 == opened2

    def test_reversed_db_row_order_does_not_change_outcome(self):
        """This is the assertion that actually pins the fix: present the tied
        candidate rows in the opposite order (simulating Postgres handing back
        the same tie a different way on a different run/plan) and require the
        SET AND ORDER of positions opened to be unchanged."""
        forward = self._tied_predictions()
        reversed_order = dict(reversed(list(forward.items())))
        assert list(forward.keys()) != list(reversed_order.keys())

        result_forward = _run_backtest(forward, self.PREDICTION_DATE, self.TRADING_DATE)
        result_reversed = _run_backtest(reversed_order, self.PREDICTION_DATE, self.TRADING_DATE)

        opened_forward = [t["symbol"] for t in result_forward["trades"] if t["action"] == "buy"]
        opened_reversed = [t["symbol"] for t in result_reversed["trades"] if t["action"] == "buy"]

        assert opened_forward == opened_reversed, (
            "tie-break order must not depend on the order predictions "
            "were returned in"
        )
        assert result_forward["equity_curve"] == result_reversed["equity_curve"]
        assert result_forward["trades"] == result_reversed["trades"]

    def test_max_positions_boundary_picks_exactly_two_of_four_ties(self):
        """Sanity check on the fixture itself: with 4 symbols tied at q50 and
        max_positions=2, exactly 2 must be bought (proves the tie is really at
        the boundary, not a vacuous no-op test)."""
        preds = self._tied_predictions()
        result = _run_backtest(preds, self.PREDICTION_DATE, self.TRADING_DATE)
        opened = [t["symbol"] for t in result["trades"] if t["action"] == "buy"]
        assert len(opened) == 2
        assert set(opened) <= set(preds.keys())
