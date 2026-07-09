"""Short holding costs (009, T008 — US3).

TDD: written FIRST. A short is not free: a borrow fee accrues for every day the
short is held, dividends are owed to the lender while short, and ordinary
transaction costs apply symmetrically to short/cover. Without these a short
backtest is dishonestly rosy — the honesty layer that makes short returns
believable.
"""
import datetime as dt

from gefion.backtest.costs import TransactionCosts
from gefion.backtest.engine import BacktestEngine

D = dt.date
_DAYS = [D(2025, 1, 1), D(2025, 1, 2), D(2025, 1, 3)]


def _prices(dividends=None):
    """Three flat days at 100 (so P&L is pure cost); optional dividend map."""
    rows = []
    for d in _DAYS:
        row = {"symbol": "AAA", "date": d, "close": 100.0}
        if dividends and d in dividends:
            row["dividend_amount"] = dividends[d]
        rows.append(row)
    return rows


def _short_then_cover(current_date, portfolio, historical):
    if current_date == D(2025, 1, 1):
        return [{"action": "short", "symbol": "AAA", "shares": 10}]
    if current_date == D(2025, 1, 3):
        return [{"action": "cover", "symbol": "AAA", "shares": 10}]
    return []


def _engine(costs, dividends=None):
    return BacktestEngine(
        price_data=_prices(dividends), strategy=_short_then_cover,
        initial_cash=10_000.0, start_date=D(2025, 1, 1), end_date=D(2025, 1, 3),
        mode="long_short", costs=costs)


def test_borrow_fee_accrues_daily_while_short():
    # borrow_rate_annual 0.252 → 0.001/day. Short opens day1, held end-of-day
    # on day1 and day2 (covered day3) → 2 days × (10 × 100 × 0.001) = 2.0.
    result = _engine(TransactionCosts(borrow_rate_annual=0.252)).run()
    assert round(result["short_costs"]["borrow_total"], 4) == 2.0
    assert round(result["equity_curve"][-1]["equity"], 2) == 9_998.0


def test_dividend_debited_to_the_lender_while_short():
    result = _engine(TransactionCosts(), dividends={D(2025, 1, 2): 2.0}).run()
    assert result["short_costs"]["dividends_total"] == 20.0    # 10 × $2
    assert round(result["equity_curve"][-1]["equity"], 2) == 9_980.0


def test_transaction_costs_apply_symmetrically_to_short_and_cover():
    result = _engine(TransactionCosts(commission_per_trade=1.0)).run()
    short_tx = next(t for t in result["trades"] if t["action"] == "short")
    cover_tx = next(t for t in result["trades"] if t["action"] == "cover")
    assert short_tx["cost"] == 1.0
    assert cover_tx["cost"] == 1.0


def test_no_borrow_no_dividends_when_not_configured():
    # a short with zero borrow rate and no dividends costs nothing to hold
    result = _engine(TransactionCosts()).run()
    assert result["short_costs"]["borrow_total"] == 0.0
    assert result["short_costs"]["dividends_total"] == 0.0
    assert round(result["equity_curve"][-1]["equity"], 2) == 10_000.0
