"""#108: netting a position to exactly zero shares must close it, not
divide by it.

TDD: written FIRST (root cause reproduced on prod data: a long-short
momentum run where a buy exactly covered an existing short crashed
portfolio.buy() at avg_price = position_cost / total_shares). A trade that
nets shares to zero means the position is CLOSED — remove it; no average
price exists for nothing. Same for short() netting out an existing long.
"""
import datetime as dt

D = dt.date(2024, 1, 2)


def _portfolio(cash=100_000.0):
    from gefion.backtest.portfolio import Portfolio
    return Portfolio(initial_cash=cash)


def test_buy_that_exactly_covers_short_closes_position():
    p = _portfolio()
    p.short("XYZ", shares=100, price=50.0, date=D)
    assert p.positions["XYZ"]["shares"] == -100
    p.buy("XYZ", shares=100, price=40.0, date=D)      # nets to exactly 0
    assert "XYZ" not in p.positions                   # closed, not div-by-zero
    # cash: +5000 short proceeds - 4000 buy = +1000 on 100k
    assert abs(p.cash - 101_000.0) < 1e-6


def test_short_over_long_still_refuses_by_design():
    """The inverse direction is a spec'd refusal, not a netting: a flip
    must close the long explicitly first. Pin it so #108's fix never
    'helpfully' relaxes it."""
    import pytest
    p = _portfolio()
    p.buy("ABC", shares=50, price=20.0, date=D)
    with pytest.raises(ValueError):
        p.short("ABC", shares=50, price=25.0, date=D)


def test_partial_netting_still_averages():
    p = _portfolio()
    p.short("PQR", shares=100, price=50.0, date=D)
    p.buy("PQR", shares=40, price=40.0, date=D)       # still -60 short
    pos = p.positions["PQR"]
    assert pos["shares"] == -60
    assert pos["avg_price"] > 0
