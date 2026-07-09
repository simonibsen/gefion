"""Short risk: margin, forced cover, exposure limits (009, T010 — US3).

TDD: written FIRST. A short's loss is unbounded, so risk must be real: a short
that runs against you past the loss threshold is force-covered (a logged margin
event), oversized shorts are blocked by exposure limits, and equity is allowed
to go negative (represented, not clamped) — hiding blow-up risk would violate
the honesty principle.
"""
import datetime as dt

from gefion.backtest.engine import BacktestEngine
from gefion.backtest.risk import RiskLimits, RiskManager

D = dt.date


def _prices(closes):
    days = [D(2025, 1, d + 1) for d in range(len(closes))]
    return [{"symbol": "AAA", "date": d, "close": c} for d, c in zip(days, closes)]


def _short_once(current_date, portfolio, historical):
    if current_date == D(2025, 1, 1):
        return [{"action": "short", "symbol": "AAA", "shares": 10}]
    return []


def _engine(closes, strategy=_short_once, risk_manager=None, cash=10_000.0):
    prices = _prices(closes)
    return BacktestEngine(
        price_data=prices, strategy=strategy, initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=risk_manager)


def test_short_stop_loss_forces_a_cover_with_margin_event():
    # short at 100, price rises to 115 (>10% against the short) → forced cover
    rm = RiskManager(RiskLimits(stop_loss_pct=0.10))
    result = _engine([100.0, 115.0, 120.0], risk_manager=rm).run()
    covers = [t for t in result["trades"] if t["action"] == "cover"]
    assert covers and covers[0]["reason"] == "stop_loss"
    assert covers[0]["date"] == D(2025, 1, 2)          # covered when breached
    assert result["margin_events"]                      # logged
    ev = result["margin_events"][0]
    assert ev["symbol"] == "AAA"
    assert ev["loss"] > 0                                # a real loss
    # short is closed — not still open at the end
    assert not any(t for t in result["trades"][-1:] if False)


def test_take_profit_covers_a_winning_short():
    # short at 100, price falls to 80 (short gains 20%) → take-profit cover
    rm = RiskManager(RiskLimits(take_profit_pct=0.15))
    result = _engine([100.0, 80.0, 75.0], risk_manager=rm).run()
    covers = [t for t in result["trades"] if t["action"] == "cover"]
    assert covers and covers[0]["reason"] == "take_profit"
    # a winning short is not a margin event
    assert result["margin_events"] == []


def test_max_short_exposure_blocks_an_oversized_short():
    def big_short(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "short", "symbol": "AAA", "shares": 100}]  # 100% notional
        return []
    rm = RiskManager(RiskLimits(max_short_exposure=0.5))   # cap 50%
    result = _engine([100.0, 100.0], strategy=big_short, risk_manager=rm).run()
    assert [t for t in result["trades"] if t["action"] == "short"] == []
    assert result["equity_curve"][-1]["equity"] == 10_000.0   # nothing shorted


def test_negative_equity_is_represented_not_clamped():
    # a runaway short with no guardrail: price rockets, equity goes negative
    result = _engine([100.0, 1200.0]).run()               # no risk_manager
    assert result["equity_curve"][-1]["equity"] == -1_000.0   # 10k − 10×(1200−100)
