"""Determinism regression tests for backtest/data_loader.py symbol selection.

Same bug class as ml_signal.py's untiebreaked ORDER BY (see
test_backtest_determinism.py): `ORDER BY COUNT(*) DESC LIMIT N` picks the
symbol universe for a backtest, but ties in COUNT(*) are underdetermined --
Postgres may return tied rows in a different order on a different run/plan,
silently changing WHICH symbols make the cut at the LIMIT boundary.

These are source-level checks (no DB needed) that the tiebreaker survives
future edits to the query text, mirroring the existing
TestMLSignalUnifiedPredictionsTable pattern in test_strategy_ml_signal.py.
"""
import inspect

from gefion.backtest.data_loader import (
    get_available_symbols,
    load_price_data_for_backtest,
)


def _order_by_lines(source: str):
    return [l for l in source.splitlines() if "ORDER BY" in l]


class TestSymbolLimitQueriesHaveTotalOrder:
    def test_load_price_data_symbol_limit_subquery_has_tiebreaker(self):
        source = inspect.getsource(load_price_data_for_backtest)
        count_order_lines = [
            l for l in _order_by_lines(source) if "COUNT(*)" in l
        ]
        assert count_order_lines, "expected an ORDER BY COUNT(*) clause"
        for line in count_order_lines:
            assert "s.id" in line or "s.symbol" in line, (
                "ORDER BY COUNT(*) DESC alone is underdetermined for ties "
                f"at the LIMIT boundary: {line!r}"
            )

    def test_get_available_symbols_has_tiebreaker(self):
        source = inspect.getsource(get_available_symbols)
        count_order_lines = [
            l for l in _order_by_lines(source) if "COUNT(*)" in l
        ]
        assert count_order_lines, "expected an ORDER BY COUNT(*) clause"
        for line in count_order_lines:
            assert "s.symbol" in line or "s.id" in line, (
                "ORDER BY COUNT(*) DESC alone is underdetermined for ties "
                f"at the LIMIT boundary: {line!r}"
            )
