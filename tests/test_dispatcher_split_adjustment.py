"""Split-adjusted pricing for the FEATURE dispatcher (issue #272).

`backtest/data_loader.py::split_adjust_prices` was fixed for this in #269, but
every stock-scope feature function (RSI, SMA, EMA, MACD, BB, realized_vol,
ADX, Stoch, price_change_pct) is fed straight from
`dispatcher._fetch_from_stock_ohlcv`, which selected raw `close` and the
CORRUPT `adjusted_close` (SMX: close $0.129 vs adjusted_close
$3,452,451,366) without ever touching `split_coefficient`. A 1:25 reverse
split reads as a +2500% overnight move to every one of those functions.

This fixes it ONCE at the fetch, mirroring the proven convention from
`backtest/data_loader.py::split_adjust_prices` (read first, same math): a
bar's `split_coefficient` applies ON that bar, so bars strictly BEFORE it are
divided by the product of all LATER coefficients. Extended here to also
adjust `adjusted_close` (the backtest path doesn't use that column at all,
since it's the corrupt one -- but every indicator function prefers it when
present, so it must carry the same correction as `close`).

THE DIRECTION TEST IS THE POINT. Multiplying where you should divide is
internally consistent and silently wrong -- for a 0.04 coefficient that is a
625x error, not a 25x one.
"""
from __future__ import annotations

import logging
from datetime import date

import pytest

from gefion.features.dispatcher import (
    _fetch_from_stock_ohlcv,
    _split_adjust_stock_rows,
)


def _row(day, close, coef=1.0, volume=1000, adjusted_close=None,
         open_=None, high=None, low=None):
    return {
        "date": date(2024, 2, day),
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "adjusted_close": adjusted_close if adjusted_close is not None else close,
        "volume": volume,
        "split_coefficient": coef,
    }


def _closes(rows):
    return [r["close"] for r in rows]


# --------------------------------------------------------------------------- #
# DIRECTION. If these pass while the others fail, the sign is wrong.
# --------------------------------------------------------------------------- #
def test_forward_split_HALVES_pre_split_prices_not_doubles():
    rows = [_row(1, 100.0), _row(2, 50.0, coef=2.0)]

    out, dropped = _split_adjust_stock_rows(rows)

    assert dropped is None
    assert _closes(out) == [50.0, 50.0]  # 100/2 == 50, NOT 200
    assert out[0]["close"] < 100.0, "pre-split price must fall, not rise"


def test_reverse_split_RAISES_pre_split_prices():
    """ADIL's real 1:25 (coefficient 0.04). Multiplying instead of dividing
    gives 0.00946 -- a 625x error, not a 25x one."""
    rows = [_row(4, 0.2365, coef=1.0), _row(7, 6.22, coef=0.04)]

    out, _ = _split_adjust_stock_rows(rows)

    assert out[0]["close"] == pytest.approx(5.9125)  # 0.2365 / 0.04
    assert out[1]["close"] == pytest.approx(6.22)
    assert out[0]["close"] > 0.2365, "reverse-split pre-price must rise"


def test_split_bar_itself_is_not_adjusted_by_its_own_coefficient():
    rows = [_row(2, 50.0, coef=2.0)]

    out, _ = _split_adjust_stock_rows(rows)

    assert out[0]["close"] == 50.0


# --------------------------------------------------------------------------- #
# Continuity: the actual defect being fixed.
# --------------------------------------------------------------------------- #
def test_reverse_split_no_longer_looks_like_a_26x_rally():
    rows = [_row(4, 0.2365), _row(7, 6.22, coef=0.04)]

    out, _ = _split_adjust_stock_rows(rows)

    ratio = out[1]["close"] / out[0]["close"]
    assert 0.5 < ratio < 2.0, f"still discontinuous: {ratio}x"


def test_multiple_splits_compound():
    rows = [_row(1, 400.0), _row(2, 200.0, coef=2.0), _row(3, 100.0, coef=2.0)]

    out, _ = _split_adjust_stock_rows(rows)

    assert _closes(out) == [100.0, 100.0, 100.0]


def test_all_price_fields_adjusted_together_including_adjusted_close():
    """The dispatcher must adjust adjusted_close too -- every indicator
    function prefers that column when present, so an unadjusted
    adjusted_close would silently re-introduce the corruption this fixes."""
    rows = [
        _row(1, 100.0, open_=90.0, high=110.0, low=80.0,
             adjusted_close=100.0),
        _row(2, 50.0, coef=2.0, open_=50.0, high=50.0, low=50.0,
             adjusted_close=50.0),
    ]

    out, _ = _split_adjust_stock_rows(rows)

    assert (out[0]["open"], out[0]["high"], out[0]["low"]) == (45.0, 55.0, 40.0)
    assert out[0]["adjusted_close"] == 50.0


def test_volume_adjusted_inversely():
    rows = [_row(1, 100.0, volume=1000), _row(2, 50.0, coef=2.0)]

    out, _ = _split_adjust_stock_rows(rows)

    assert out[0]["volume"] == 2000
    assert out[0]["close"] * out[0]["volume"] == 100.0 * 1000


def test_no_splits_leaves_prices_unchanged():
    rows = [_row(1, 100.0), _row(2, 101.0), _row(3, 99.0)]

    out, dropped = _split_adjust_stock_rows(rows)

    assert dropped is None
    assert _closes(out) == [100.0, 101.0, 99.0]
    assert [r["volume"] for r in out] == [1000, 1000, 1000]


def test_input_order_does_not_matter():
    rows = list(reversed([_row(1, 100.0), _row(2, 50.0, coef=2.0)]))

    out, _ = _split_adjust_stock_rows(rows)

    by_date = {r["date"]: r["close"] for r in out}
    assert by_date[date(2024, 2, 1)] == 50.0


def test_empty_input_is_a_no_op():
    assert _split_adjust_stock_rows([]) == ([], None)


# --------------------------------------------------------------------------- #
# Fail closed: an unknown split history is not a known-absent one.
# --------------------------------------------------------------------------- #
def test_null_coefficient_drops_the_whole_series():
    rows = [_row(1, 100.0), _row(2, 50.0, coef=None)]

    out, dropped = _split_adjust_stock_rows(rows)

    assert out == []
    assert dropped is not None
    assert dropped["date"] == date(2024, 2, 2)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_nonsensical_coefficient_drops_the_whole_series(bad):
    rows = [_row(1, 100.0), _row(2, 50.0, coef=bad)]

    out, dropped = _split_adjust_stock_rows(rows)

    assert out == []
    assert dropped is not None


# --------------------------------------------------------------------------- #
# _fetch_from_stock_ohlcv: the dispatcher's actual DB-facing fetch.
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self._conn.executed.append((query, params))
        if "FROM stock_ohlcv" in query:
            self._result = list(self._conn.ohlcv_rows)
        elif "FROM stocks" in query:
            self._result = [(self._conn.symbol,)] if self._conn.symbol else []
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeConn:
    def __init__(self, ohlcv_rows, symbol="TEST"):
        self.ohlcv_rows = ohlcv_rows
        self.symbol = symbol
        self.executed = []

    def cursor(self):
        return _FakeCursor(self)


def _db_row(d, o, h, l, c, adj, vol, coef):
    return (d, o, h, l, c, adj, vol, coef)


def test_fetch_from_stock_ohlcv_applies_split_adjustment():
    rows = [
        _db_row(date(2024, 2, 1), 100.0, 100.0, 100.0, 100.0, 100.0, 1000, 1.0),
        _db_row(date(2024, 2, 2), 50.0, 50.0, 50.0, 50.0, 50.0, 1000, 2.0),
    ]
    conn = _FakeConn(rows)

    out = _fetch_from_stock_ohlcv(conn, data_id=1, column="close",
                                   start_date=None)

    assert [r["close"] for r in out] == [50.0, 50.0]
    assert [r["adjusted_close"] for r in out] == [50.0, 50.0]
    assert out[0]["volume"] == 2000


def test_fetch_from_stock_ohlcv_skips_symbol_with_unknown_split_history(caplog):
    rows = [
        _db_row(date(2024, 2, 1), 100.0, 100.0, 100.0, 100.0, 100.0, 1000, 1.0),
        _db_row(date(2024, 2, 2), 50.0, 50.0, 50.0, 50.0, 50.0, 1000, None),
    ]
    conn = _FakeConn(rows, symbol="ADIL")

    with caplog.at_level(logging.WARNING, logger="gefion.features.dispatcher"):
        out = _fetch_from_stock_ohlcv(conn, data_id=1, column="close",
                                       start_date=None)

    assert out == []
    assert any("ADIL" in r.message for r in caplog.records), caplog.text


def test_fetch_from_stock_ohlcv_no_splits_unchanged():
    rows = [
        _db_row(date(2024, 2, 1), 100.0, 100.0, 100.0, 100.0, 100.0, 1000, 1.0),
        _db_row(date(2024, 2, 2), 101.0, 101.0, 101.0, 101.0, 101.0, 1000, 1.0),
    ]
    conn = _FakeConn(rows)

    out = _fetch_from_stock_ohlcv(conn, data_id=1, column="close",
                                   start_date=None)

    assert [r["close"] for r in out] == [100.0, 101.0]
    assert [r["volume"] for r in out] == [1000, 1000]


# --------------------------------------------------------------------------- #
# End-to-end: a real indicator body sees a continuous series, not a jump.
# --------------------------------------------------------------------------- #
def test_indicator_sma_continuous_over_a_planted_split():
    import json
    from pathlib import Path

    from gefion.features.dispatcher import exec_sandboxed

    body = json.loads(
        Path("feature-functions/indicator_sma.json").read_text()
    )["function_body"]
    compute = exec_sandboxed(body, "compute")["compute"]

    # 20 flat $100 bars, then a 2:1 split drops the raw close to $50 for the
    # rest of the window -- a discontinuity an un-adjusted SMA would show.
    raw_rows = [_row(d, 100.0) for d in range(1, 21)]
    raw_rows += [_row(d, 50.0, coef=(2.0 if d == 21 else 1.0))
                 for d in range(21, 26)]
    adjusted, dropped = _split_adjust_stock_rows(raw_rows)
    assert dropped is None

    out = compute(adjusted, [{"period": 5}])
    values = {r["date"]: r["sma_5"] for r in out if "sma_5" in r}

    # The split bar's $50 close is already post-split and stays put; the
    # pre-split $100 bars are the ones that must come DOWN to match it. So a
    # correct adjustment makes the whole SMA series flat at $50 -- an
    # unadjusted feed would jump from $100 to $50 partway through the window.
    for d in range(5, 26):
        assert values[date(2024, 2, d)] == pytest.approx(50.0), (
            f"day {d}: SMA jumped -- split adjustment did not reach the indicator"
        )
