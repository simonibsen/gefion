"""Price-integrity guard for backtesting (issue #262).

Every A/B account blowup traced to a reverse split priced as a market move:
the backtest prices on raw ``close``, which is not split-adjusted, so a 1:20
reverse split reads as a 20x overnight rally and a short position through it
loses 1900%.

A correct split adjustment is not available: ``stock_ohlcv.split_coefficient``
is 100% NULL (0 of 1,962,612 rows in 2023) and ``adjusted_close`` is corrupt
for serial reverse-splitters (SMX: close $0.129, adjusted_close $3.45e9).
Inferring the ratio from the jump itself would be exactly the plausible guess
the failure-semantics rule forbids -- a genuine 20x move would be silently
rewritten as a split.

So the guard refuses: a move it cannot explain drops the symbol and warns
naming it. Blocking, never marking through.
"""
from __future__ import annotations

import logging
from datetime import date

import pytest

from gefion.backtest.data_loader import (
    IMPLAUSIBLE_MOVE_RATIO,
    filter_implausible_price_moves,
)


def _bars(symbol: str, closes, start_day: int = 1):
    """Build a price-data slice for one symbol from a list of closes."""
    return [
        {
            "symbol": symbol,
            "date": date(2023, 8, start_day + i),
            "close": c,
            "open": c,
            "high": c,
            "low": c,
            "volume": 1_000,
        }
        for i, c in enumerate(closes)
    ]


def _symbols(price_data):
    return {row["symbol"] for row in price_data}


# --------------------------------------------------------------------------- #
# The defect: reverse splits.
# --------------------------------------------------------------------------- #
def test_reverse_split_jump_drops_symbol():
    """ADIL 2023-08-07: 0.2365 -> 6.22 (26.3x). This killed arm B."""
    kept, dropped = filter_implausible_price_moves(
        _bars("ADIL", [0.2400, 0.2365, 6.22, 6.10]))

    assert _symbols(kept) == set()
    assert "ADIL" in dropped
    assert dropped["ADIL"]["date"] == date(2023, 8, 3)
    assert dropped["ADIL"]["ratio"] == pytest.approx(26.30, rel=1e-3)


def test_downward_implausible_move_drops_symbol():
    """A forward split (or a data fault) in the other direction blocks too."""
    kept, dropped = filter_implausible_price_moves(
        _bars("SPLIT", [20.0, 20.0, 1.0]))

    assert _symbols(kept) == set()
    assert dropped["SPLIT"]["ratio"] == pytest.approx(0.05)


def test_only_the_offending_symbol_is_dropped():
    """A fault in one name must not discard the rest of the book."""
    data = (_bars("GOOD", [10.0, 10.5, 11.0])
            + _bars("ADIL", [0.2365, 6.22, 6.10])
            + _bars("ALSOGOOD", [50.0, 48.0, 51.0]))

    kept, dropped = filter_implausible_price_moves(data)

    assert _symbols(kept) == {"GOOD", "ALSOGOOD"}
    assert set(dropped) == {"ADIL"}
    assert len(kept) == 6


# --------------------------------------------------------------------------- #
# Normal market behavior must survive untouched.
# --------------------------------------------------------------------------- #
def test_ordinary_volatility_is_retained():
    """Real moves -- even violent ones -- stay. +50% and -30% are markets."""
    data = _bars("VOL", [10.0, 15.0, 10.5, 12.0])

    kept, dropped = filter_implausible_price_moves(data)

    assert dropped == {}
    assert kept == data


def test_unsorted_input_is_evaluated_in_date_order():
    """Rows arrive ordered by date then symbol; per-symbol order must not be
    assumed from list position, or a jump is computed against the wrong bar."""
    data = list(reversed(_bars("ADIL", [0.2365, 6.22])))

    _, dropped = filter_implausible_price_moves(data)

    assert "ADIL" in dropped


def test_single_bar_symbol_is_retained():
    """One bar has no ratio to check -- nothing is undetermined, so keep it."""
    kept, dropped = filter_implausible_price_moves(_bars("ONE", [5.0]))

    assert dropped == {}
    assert len(kept) == 1


# --------------------------------------------------------------------------- #
# Fail closed on anything undetermined.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_close", [None, 0.0, -1.0])
def test_unusable_close_drops_symbol(bad_close):
    """A missing or non-positive close makes the ratio undefined. Undefined is
    not 'no move' -- it blocks, exactly like an unparseable config."""
    kept, dropped = filter_implausible_price_moves(
        _bars("BAD", [10.0, bad_close, 10.0]))

    assert _symbols(kept) == set()
    assert "BAD" in dropped


def test_boundary_ratio_at_threshold_is_blocking():
    """`>=`, not `>`: at the threshold the move is undetermined, and an
    undetermined verdict is not approval."""
    _, dropped = filter_implausible_price_moves(
        _bars("EDGE", [1.0, float(IMPLAUSIBLE_MOVE_RATIO)]))

    assert "EDGE" in dropped


def test_just_inside_threshold_is_retained():
    _, dropped = filter_implausible_price_moves(
        _bars("EDGE", [1.0, IMPLAUSIBLE_MOVE_RATIO * 0.99]))

    assert dropped == {}


@pytest.mark.parametrize("bad_ratio", [1.0, 0.5, 0.0, -2.0])
def test_invalid_threshold_raises(bad_ratio):
    """A threshold <= 1 would drop every symbol or none. Refuse the config
    rather than run a backtest whose guard is meaningless."""
    with pytest.raises(ValueError, match="max_ratio"):
        filter_implausible_price_moves(_bars("X", [1.0, 2.0]),
                                       max_ratio=bad_ratio)


# --------------------------------------------------------------------------- #
# Dropped input must be named, never silent.
# --------------------------------------------------------------------------- #
def test_drop_warns_naming_symbol_date_and_ratio(caplog):
    """'Silent filtering is how a parameter goes missing for weeks.'"""
    with caplog.at_level(logging.WARNING, logger="gefion.backtest.data_loader"):
        filter_implausible_price_moves(_bars("ADIL", [0.2365, 6.22]))

    assert any("ADIL" in r.message and "2023-08-02" in r.message
               and "26.3" in r.message for r in caplog.records), caplog.text


def test_no_warning_when_nothing_is_dropped(caplog):
    with caplog.at_level(logging.WARNING, logger="gefion.backtest.data_loader"):
        filter_implausible_price_moves(_bars("GOOD", [10.0, 10.5]))

    assert caplog.records == []


def test_empty_input_is_a_no_op():
    kept, dropped = filter_implausible_price_moves([])

    assert kept == []
    assert dropped == {}


# --------------------------------------------------------------------------- #
# The guard must actually be wired into the backtest's price door.
# --------------------------------------------------------------------------- #
def test_loader_applies_the_guard(monkeypatch):
    """`load_price_data_for_backtest` is the single door price data enters a
    backtest through; the guard belongs there, not at each call site."""
    import gefion.backtest.data_loader as dl

    # Trailing column is split_coefficient (#264). 1.0 throughout, so ADIL's
    # jump is UNEXPLAINED by any split and must still be refused -- the guard
    # and the split adjustment compose rather than one masking the other.
    rows = [
        ("ADIL", date(2023, 8, 1), 0.2365, 0.23, 0.24, 0.23, 1000, 1.0),
        ("ADIL", date(2023, 8, 2), 6.22, 6.0, 6.3, 6.0, 1000, 1.0),
        ("GOOD", date(2023, 8, 1), 10.0, 10.0, 10.0, 10.0, 1000, 1.0),
        ("GOOD", date(2023, 8, 2), 10.5, 10.5, 10.5, 10.5, 1000, 1.0),
    ]

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): return None
        def fetchall(self): return rows

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    # `load_price_data_for_backtest` imports these inside the function body,
    # so they must be patched on the source module, not on `dl`.
    import gefion.universe as gu

    monkeypatch.setattr(dl.psycopg, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(gu, "resolve_universe",
                        lambda conn, universe: type(
                            "U", (), {"universe_id": None})())
    monkeypatch.setattr(gu, "universe_exclusion_clause",
                        lambda *a, **k: ("", []))

    price_data = dl.load_price_data_for_backtest("postgresql://x/y")

    assert _symbols(price_data) == {"GOOD"}
