"""Split-adjusted pricing for backtests (issue #262, enabled by #264).

The backtest priced on raw ``close``, which is not split-adjusted, so an
unrecorded reverse split read as a 16-60x overnight rally and destroyed any
short held through it. #263 added a guard that BLOCKS unexplained moves, but a
guard cannot fix forward splits: a 3:1 split is a ratio of 0.33, inside any
threshold that does not also block genuine -50% crashes (AAPL 4:1 = 0.2585,
TSLA 3:1 = 0.3322, WMT 3:1 = 0.3395, PANW 2:1 = 0.5151 all slip past).

#264's backfill recovered real split data -- 5,501 corporate actions -- so the
prices can now be adjusted rather than merely refused.

CONVENTION (verified against production, not assumed):

    WMT  2024-02-23 close 175.56  split_coefficient 1.0
    WMT  2024-02-26 close  59.60  split_coefficient 3.0   <- 3:1 forward
    ADIL 2023-08-04 close   0.2365 split_coefficient 1.0
    ADIL 2023-08-07 close   6.22   split_coefficient 0.04 <- 1:25 reverse

The coefficient applies ON its own date: that bar's close is ALREADY
post-split. So a bar at time t is back-adjusted by dividing by the product of
coefficients on dates STRICTLY LATER than t. 175.56/3 = 58.52 against a 59.60
next bar; 0.2365/0.04 = 5.91 against 6.22. Both continuous.

THE DIRECTION TEST BELOW IS THE POINT OF THIS FILE. An implementation that
multiplies where it should divide is internally consistent and silently wrong
-- and for ADIL's 0.04 coefficient it amplifies the error 625x rather than
removing it. An internally-consistent inversion survived every layer of
review in #010; only a planted-direction test catches it.
"""
from __future__ import annotations

import logging
from datetime import date

import pytest

from gefion.backtest.data_loader import split_adjust_prices


def _bar(symbol, day, close, coef=1.0, volume=1000):
    return {
        "symbol": symbol,
        "date": date(2024, 2, day),
        "close": close,
        "open": close,
        "high": close,
        "low": close,
        "volume": volume,
        "split_coefficient": coef,
    }


def _closes(rows, symbol=None):
    return [r["close"] for r in rows if symbol is None or r["symbol"] == symbol]


# --------------------------------------------------------------------------- #
# DIRECTION. If these pass while the others fail, the sign is wrong.
# --------------------------------------------------------------------------- #
def test_forward_split_HALVES_pre_split_prices_not_doubles():
    """A 2:1 split makes each old share into two cheaper ones. Pre-split prices
    must come DOWN to be comparable. An implementation that multiplies passes
    every continuity check while being exactly backwards."""
    rows = [_bar("X", 1, 100.0), _bar("X", 2, 50.0, coef=2.0)]

    out, dropped = split_adjust_prices(rows)

    assert dropped == {}
    assert _closes(out) == [50.0, 50.0]          # 100/2 == 50, NOT 200
    assert out[0]["close"] < 100.0, "pre-split price must fall, not rise"


def test_reverse_split_RAISES_pre_split_prices():
    """ADIL's real 1:25 (coefficient 0.04). Pre-split prices must go UP 25x.
    Multiplying by 0.04 instead of dividing gives 0.00946 -- a 625x error."""
    rows = [_bar("ADIL", 4, 0.2365, coef=1.0),
            _bar("ADIL", 7, 6.22, coef=0.04)]

    out, _ = split_adjust_prices(rows)

    assert out[0]["close"] == pytest.approx(5.9125)   # 0.2365 / 0.04
    assert out[1]["close"] == pytest.approx(6.22)
    assert out[0]["close"] > 0.2365, "reverse-split pre-price must rise"


def test_split_bar_itself_is_not_adjusted_by_its_own_coefficient():
    """The split date's close is already post-split. Including its own
    coefficient double-counts and shifts the whole series."""
    rows = [_bar("X", 2, 50.0, coef=2.0)]

    out, _ = split_adjust_prices(rows)

    assert out[0]["close"] == 50.0


# --------------------------------------------------------------------------- #
# Continuity: the actual defect being fixed.
# --------------------------------------------------------------------------- #
def test_reverse_split_no_longer_looks_like_a_26x_rally():
    """ADIL's real bars. Raw: 0.2365 -> 6.22 is +2530%, which killed the A/B
    short book. Adjusted, the day-over-day move must be ordinary."""
    rows = [_bar("ADIL", 4, 0.2365), _bar("ADIL", 7, 6.22, coef=0.04)]

    out, _ = split_adjust_prices(rows)

    ratio = out[1]["close"] / out[0]["close"]
    assert 0.5 < ratio < 2.0, f"still discontinuous: {ratio}x"


def test_forward_split_no_longer_looks_like_a_crash():
    """WMT's real 3:1. Raw 175.56 -> 59.60 reads as -66%."""
    rows = [_bar("WMT", 23, 175.56), _bar("WMT", 26, 59.60, coef=3.0)]

    out, _ = split_adjust_prices(rows)

    ratio = out[1]["close"] / out[0]["close"]
    assert 0.9 < ratio < 1.1, f"still discontinuous: {ratio}x"


def test_multiple_splits_compound():
    """Two 2:1 splits after the first bar => divide by 4, not 2."""
    rows = [_bar("X", 1, 400.0), _bar("X", 2, 200.0, coef=2.0),
            _bar("X", 3, 100.0, coef=2.0)]

    out, _ = split_adjust_prices(rows)

    assert _closes(out) == [100.0, 100.0, 100.0]


def test_all_ohlc_fields_adjusted_together():
    rows = [{"symbol": "X", "date": date(2024, 2, 1), "close": 100.0,
             "open": 90.0, "high": 110.0, "low": 80.0, "volume": 1000,
             "split_coefficient": 1.0},
            _bar("X", 2, 50.0, coef=2.0)]

    out, _ = split_adjust_prices(rows)

    assert (out[0]["open"], out[0]["high"], out[0]["low"]) == (45.0, 55.0, 40.0)


def test_volume_adjusted_inversely():
    """Price halves, share count doubles -- dollar volume is invariant."""
    rows = [_bar("X", 1, 100.0, volume=1000), _bar("X", 2, 50.0, coef=2.0)]

    out, _ = split_adjust_prices(rows)

    assert out[0]["volume"] == 2000
    assert out[0]["close"] * out[0]["volume"] == 100.0 * 1000


# --------------------------------------------------------------------------- #
# Unchanged where there is nothing to adjust.
# --------------------------------------------------------------------------- #
def test_no_splits_leaves_prices_untouched():
    rows = [_bar("X", 1, 100.0), _bar("X", 2, 101.0), _bar("X", 3, 99.0)]

    out, dropped = split_adjust_prices(rows)

    assert dropped == {}
    assert _closes(out) == [100.0, 101.0, 99.0]
    assert [r["volume"] for r in out] == [1000, 1000, 1000]


def test_symbols_are_adjusted_independently():
    rows = [_bar("A", 1, 100.0), _bar("A", 2, 50.0, coef=2.0),
            _bar("B", 1, 100.0), _bar("B", 2, 101.0)]

    out, _ = split_adjust_prices(rows)

    assert _closes(out, "A") == [50.0, 50.0]
    assert _closes(out, "B") == [100.0, 101.0]


def test_input_order_does_not_matter():
    """Rows arrive ordered by (date, symbol); per-symbol order is not implied."""
    rows = list(reversed([_bar("X", 1, 100.0), _bar("X", 2, 50.0, coef=2.0)]))

    out, _ = split_adjust_prices(rows)

    by_date = {r["date"]: r["close"] for r in out}
    assert by_date[date(2024, 2, 1)] == 50.0


# --------------------------------------------------------------------------- #
# Fail closed: an unknown split history is not a known-absent one.
# --------------------------------------------------------------------------- #
def test_null_coefficient_drops_the_symbol():
    """87 symbols came out of the #264 backfill still unpopulated. A NULL
    coefficient means "unknown", and an unknown split history makes every
    earlier price untrustworthy -- so the symbol is refused, not guessed at."""
    rows = [_bar("X", 1, 100.0), _bar("X", 2, 50.0, coef=None)]

    out, dropped = split_adjust_prices(rows)

    assert out == []
    assert "X" in dropped


def test_null_coefficient_drops_only_that_symbol():
    rows = [_bar("BAD", 1, 100.0, coef=None), _bar("GOOD", 1, 100.0)]

    out, dropped = split_adjust_prices(rows)

    assert {r["symbol"] for r in out} == {"GOOD"}
    assert set(dropped) == {"BAD"}


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_nonsensical_coefficient_drops_the_symbol(bad):
    """A zero coefficient would divide by zero; a negative one is meaningless."""
    rows = [_bar("X", 1, 100.0), _bar("X", 2, 50.0, coef=bad)]

    out, dropped = split_adjust_prices(rows)

    assert out == []
    assert "X" in dropped


def test_drop_warns_naming_the_symbol(caplog):
    with caplog.at_level(logging.WARNING, logger="gefion.backtest.data_loader"):
        split_adjust_prices([_bar("X", 1, 100.0, coef=None)])

    assert any("X" in r.message for r in caplog.records), caplog.text


def test_empty_input_is_a_no_op():
    assert split_adjust_prices([]) == ([], {})
