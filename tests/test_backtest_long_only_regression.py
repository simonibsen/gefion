"""Long-only reproducibility gate (009, T002/T003 — US2).

The safety spine of spec 009: short-side execution must NOT perturb existing
long-only backtests. This harness pins a hand-computed deterministic scenario
(exact equity curve, trades, and metrics) plus a determinism check, and asserts
`mode` defaults to `long_only`. It re-runs at the end of every 009 phase — if it
ever fails, a short-side change has leaked into the default path (SC-902).

The reference values are hand-computed (not merely captured), so the gate is a
true oracle, not a snapshot of whatever the code happens to do.
"""
import datetime as dt

from gefion.backtest.engine import BacktestEngine

D = dt.date

# Deterministic scenario: one symbol, four daily closes, a buy on day 1 and a
# sell on day 3. Hand-computed with initial_cash=10_000, no costs/slippage:
#   day1  buy 10 @ 100 -> cash 9_000, pos 10@100; equity(100) = 9_000 + 1_000 = 10_000
#   day2  hold                        ; equity(110) = 9_000 + 1_100 = 10_100
#   day3  sell 10 @ 105 -> cash 10_050; equity(105) = 10_050 (pnl = (105-100)*10 = 50)
#   day4  hold                        ; equity(120) = 10_050
_DAYS = [D(2025, 1, 1), D(2025, 1, 2), D(2025, 1, 3), D(2025, 1, 4)]
_CLOSES = [100.0, 110.0, 105.0, 120.0]


def _prices():
    return [{"symbol": "AAA", "date": d, "close": c}
            for d, c in zip(_DAYS, _CLOSES)]


def _fixed_strategy(current_date, portfolio, historical):
    if current_date == D(2025, 1, 1):
        return [{"action": "buy", "symbol": "AAA", "shares": 10}]
    if current_date == D(2025, 1, 3):
        return [{"action": "sell", "symbol": "AAA", "shares": 10}]
    return []


def _engine(**kw):
    return BacktestEngine(
        price_data=_prices(), strategy=_fixed_strategy, initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 4), **kw)


def test_long_only_hand_computed_reference():
    result = _engine().run()

    assert result["mode"] == "long_only"                       # default seam
    equities = [round(p["equity"], 2) for p in result["equity_curve"]]
    assert equities == [10_000.0, 10_100.0, 10_050.0, 10_050.0]

    trades = result["trades"]
    assert [t["action"] for t in trades] == ["buy", "sell"]
    assert round(trades[1]["pnl"], 2) == 50.0                   # (105-100)*10

    assert round(result["metrics"]["total_return"], 4) == 0.005  # 50/10_000


def test_long_only_is_deterministic():
    a, b = _engine().run(), _engine().run()
    assert [p["equity"] for p in a["equity_curve"]] == \
           [p["equity"] for p in b["equity_curve"]]
    assert a["metrics"] == b["metrics"]
    assert a["trades"] == b["trades"]


def test_mode_defaults_to_long_only_without_flag():
    result = BacktestEngine(
        price_data=_prices(), strategy=lambda *a: [], initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 4)).run()
    assert result["mode"] == "long_only"
