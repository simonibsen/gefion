"""Signed positions: short & cover (009, T004/T006 — US1).

TDD: written FIRST. A short is a first-class negative position — proceeds
credited on open, marked up as price falls (the existing `calculate_equity`
`shares × price` is already correct for negative shares), P&L realized as
`(entry − exit) × size` on cover. The engine routes `short`/`cover` in
`long_short` mode and drops them in `long_only` (the reproducibility gate).
"""
import datetime as dt

import pytest

from gefion.backtest.engine import BacktestEngine
from gefion.backtest.portfolio import Portfolio

D = dt.date


# --- Portfolio: signed positions ---------------------------------------------------

def test_short_opens_negative_position_and_credits_proceeds():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    assert p.positions["AAA"]["shares"] == -10
    assert p.positions["AAA"]["avg_price"] == 100.0
    assert p.cash == 11_000.0                      # +1_000 proceeds


def test_short_marks_up_as_price_falls():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    assert p.calculate_equity({"AAA": 90.0}) == 10_100.0   # +10×(100−90)
    assert p.calculate_equity({"AAA": 110.0}) == 9_900.0   # −10×(110−100)
    # at entry price, equity is flat
    assert p.calculate_equity({"AAA": 100.0}) == 10_000.0


def test_cover_realizes_pnl_and_closes():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    p.cover("AAA", 10, 90.0, D(2025, 1, 2))
    assert "AAA" not in p.positions
    assert p.cash == 10_100.0                      # 11_000 − 900 buy-back
    tx = p.transactions[-1]
    assert tx["action"] == "cover"
    assert tx["realized_pnl"] == 100.0             # (100−90)×10


def test_partial_cover_is_pro_rata():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    p.cover("AAA", 4, 90.0, D(2025, 1, 2))
    assert p.positions["AAA"]["shares"] == -6
    assert p.positions["AAA"]["avg_price"] == 100.0
    assert p.transactions[-1]["realized_pnl"] == 40.0   # (100−90)×4


def test_cover_is_clamped_never_flips_to_long():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    p.cover("AAA", 25, 90.0, D(2025, 1, 2))        # more than the short
    assert "AAA" not in p.positions                # fully covered, not flipped
    assert p.cash == 10_100.0                       # only 10 covered @ 90


def test_short_on_an_existing_long_is_refused():
    """Flip requires an explicit close then open (spec edge case)."""
    p = Portfolio(10_000.0)
    p.buy("AAA", 10, 100.0, D(2025, 1, 1))
    with pytest.raises(ValueError):
        p.short("AAA", 5, 110.0, D(2025, 1, 2))


def test_averaging_into_a_short():
    p = Portfolio(10_000.0)
    p.short("AAA", 10, 100.0, D(2025, 1, 1))
    p.short("AAA", 10, 120.0, D(2025, 1, 2))
    assert p.positions["AAA"]["shares"] == -20
    assert p.positions["AAA"]["avg_price"] == 110.0   # (100+120)/2


# --- Engine: routing + mode gate ---------------------------------------------------

def _short_strategy(current_date, portfolio, historical):
    if current_date == D(2025, 1, 1):
        return [{"action": "short", "symbol": "AAA", "shares": 10}]
    if current_date == D(2025, 1, 3):
        return [{"action": "cover", "symbol": "AAA", "shares": 10}]
    return []


def _falling_prices():
    days = [D(2025, 1, 1), D(2025, 1, 2), D(2025, 1, 3)]
    return [{"symbol": "AAA", "date": d, "close": c}
            for d, c in zip(days, [100.0, 95.0, 90.0])]


def _engine(mode):
    return BacktestEngine(price_data=_falling_prices(), strategy=_short_strategy,
                          initial_cash=10_000.0, start_date=D(2025, 1, 1),
                          end_date=D(2025, 1, 3), mode=mode)


def test_engine_long_short_opens_and_covers():
    result = _engine("long_short").run()
    trades = result["trades"]
    assert [t["action"] for t in trades] == ["short", "cover"]
    assert trades[0]["side"] == "short"
    assert trades[1]["pnl"] == 100.0               # short 100 → cover 90
    # a winning short lifts final equity above the start
    assert result["equity_curve"][-1]["equity"] == 10_100.0


def test_engine_long_only_drops_short_and_cover():
    result = _engine("long_only").run()
    assert result["trades"] == []                  # short/cover ignored
    assert result["equity_curve"][-1]["equity"] == 10_000.0
