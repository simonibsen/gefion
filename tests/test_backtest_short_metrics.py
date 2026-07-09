"""Metrics under shorts (009, T012 — US4).

TDD: written FIRST. A winning short (price down) must read as a win; drawdown
and returns reconcile to the (signed-correct) equity curve; gross/net/long/short
exposure is reported. Trade metrics already key on `pnl`, and cover trades carry
the correct short P&L — this pins that and adds the exposure series.
"""
import datetime as dt

from gefion.backtest.engine import BacktestEngine
from gefion.backtest.metrics import calculate_trade_metrics

D = dt.date


def test_winning_short_counts_as_a_win():
    trades = [
        {"action": "short", "symbol": "A", "shares": 10, "price": 100, "side": "short"},
        {"action": "cover", "symbol": "A", "shares": 10, "price": 90,
         "pnl": 100.0, "side": "short"},   # short 100 → 90: +100
    ]
    m = calculate_trade_metrics(trades)
    assert m["win_rate"] == 1.0
    assert m["profit_factor"] == 0.0        # no losses → documented convention


def test_mixed_short_round_trips():
    trades = [{"action": "cover", "pnl": 100.0},    # winning short
              {"action": "cover", "pnl": -40.0}]    # losing short
    m = calculate_trade_metrics(trades)
    assert m["win_rate"] == 0.5
    assert m["profit_factor"] == 2.5        # 100 / 40


def _short_run():
    closes = [100.0, 95.0, 90.0]
    prices = [{"symbol": "AAA", "date": D(2025, 1, d + 1), "close": c}
              for d, c in enumerate(closes)]

    def strat(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "short", "symbol": "AAA", "shares": 10}]
        if current_date == D(2025, 1, 3):
            return [{"action": "cover", "symbol": "AAA", "shares": 10}]
        return []

    return BacktestEngine(
        price_data=prices, strategy=strat, initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 3), mode="long_short").run()


def test_short_only_declining_market_is_profitable_and_wins():
    result = _short_run()
    assert result["metrics"]["total_return"] > 0        # short 100 → cover 90
    tm = calculate_trade_metrics(result["trades"])
    assert tm["win_rate"] == 1.0


def test_exposure_series_reports_gross_net_long_short():
    result = _short_run()
    exp = result["exposure"]
    assert len(exp) == 3
    # day 1: short 10 @ 100 = 1_000 notional, equity 10_000 → 10% short
    assert round(exp[0]["short"], 4) == 0.1
    assert round(exp[0]["gross"], 4) == 0.1
    assert round(exp[0]["net"], 4) == -0.1
    assert exp[0]["long"] == 0.0
    # day 3: covered → flat
    assert exp[2]["short"] == 0.0
    assert exp[2]["gross"] == 0.0


def test_long_only_exposure_has_no_short_side():
    closes = [100.0, 110.0]
    prices = [{"symbol": "AAA", "date": D(2025, 1, d + 1), "close": c}
              for d, c in enumerate(closes)]

    def buy_strat(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "buy", "symbol": "AAA", "shares": 10}]
        return []

    result = BacktestEngine(
        price_data=prices, strategy=buy_strat, initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 2)).run()
    assert all(bar["short"] == 0.0 for bar in result["exposure"])
    assert round(result["exposure"][0]["long"], 4) == 0.1   # 1_000 / 10_000
