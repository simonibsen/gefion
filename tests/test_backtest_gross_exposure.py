"""Gross-exposure budget for long_short backtests (#211).

The engine must bound total gross notional (long + short, marked-to-market) to
`equity × max_gross_exposure`, so `total_return`/drawdown stay physical no matter
how many/large the positions the strategy requests. Root cause (#211): `short()`
credited proceeds with no capital constraint (unlike `buy()`), and the engine
only *reported* gross exposure (`_bar_exposure`) without enforcing it — so an
uncapped short book produced a −385× (non-physical) return.

Policy (decided on the issue): dollar-neutral, `max_gross_exposure = 2.0` for
long_short (100% long / 100% short); `1.0` for long_only (a no-op — `buy` already
self-limits to cash, preserving the reproducibility gate). Clamp, never refuse.
"""
from __future__ import annotations

from datetime import date, timedelta

from gefion.backtest.engine import BacktestEngine


def _prices(symbols, start, days, p0=100.0, p_end=140.0):
    """Linear price path p0 -> p_end over `days` trading days, all symbols."""
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        px = p0 + (p_end - p0) * (i / (days - 1))
        for s in symbols:
            rows.append({"symbol": s, "date": d, "close": px})
    return rows


def _short_all_when_flat(symbols, shares_per_name):
    """Strategy: short each symbol once (while flat) with a fixed share count.
    With shares_per_name × p0 ≈ 100% of capital per name and N symbols, the
    *requested* book is N× capital — far over any sane budget."""
    def strat(current_date, portfolio, historical_prices):
        sigs = []
        for s in symbols:
            if portfolio.get_position(s).get("shares", 0) == 0:
                sigs.append({"action": "short", "symbol": s, "shares": shares_per_name})
        return sigs
    return strat


def _run(symbols, days=5, cash=100_000.0, shares_per_name=1000, mode="long_short",
         max_gross_exposure=None):
    start = date(2022, 1, 3)
    price_data = _prices(symbols, start, days)
    engine = BacktestEngine(
        price_data=price_data,
        strategy=_short_all_when_flat(symbols, shares_per_name),
        initial_cash=cash,
        start_date=start,
        end_date=start + timedelta(days=days - 1),
        mode=mode,
        max_gross_exposure=max_gross_exposure,
    )
    return engine.run()


def test_long_short_gross_exposure_is_bounded():
    """5 shorts each requesting ~100% of capital → 500% requested; the engine
    must clamp gross to the 2.0 budget and keep total_return physical."""
    symbols = [f"S{i}" for i in range(5)]
    result = _run(symbols)  # default long_short budget = 2.0

    # Opening trades are clamped down from the requested 1000 shares...
    shorts = [t for t in result["trades"] if t["action"] == "short"]
    assert shorts and all(t["shares"] < 1000 for t in shorts), \
        "over-budget shorts must be clamped, not executed at requested size"

    # ...so ENTRY gross exposure (first bar, before adverse drift) fits the 2.0
    # budget. The gross/equity *ratio* drifts up afterward as the losing book's
    # equity shrinks — bounding that intra-period drift is the margin model (#217).
    entry_gross = result["exposure"][0]["gross"]
    assert entry_gross <= 2.0 + 1e-6, f"entry gross {entry_gross} exceeded 2.0"

    # And total_return is physical — bounded, not the −385× the bug produced.
    # (+40% move on a 2x-gross book => ~ -0.8.)
    tr = result["metrics"]["total_return"]
    assert -1.0 < tr < 0.0, f"total_return {tr} is non-physical"


def test_max_gross_exposure_param_respected():
    """An explicit budget is honored (here 1.0 => at most 100% gross)."""
    symbols = [f"S{i}" for i in range(5)]
    result = _run(symbols, max_gross_exposure=1.0)
    entry_gross = result["exposure"][0]["gross"]
    assert entry_gross <= 1.0 + 1e-6, f"entry gross {entry_gross} exceeded 1.0"


def test_gross_cap_is_noop_for_a_book_within_budget():
    """A single small short (well under budget) is NOT clamped — the requested
    size executes in full, so the cap never touches well-behaved books."""
    symbols = ["S0"]
    # one short of 100 shares ≈ 100*100/100_000 = 10% of capital, far under 2.0
    result = _run(symbols, shares_per_name=100)
    shorts = [t for t in result["trades"] if t["action"] == "short"]
    assert len(shorts) == 1
    assert shorts[0]["shares"] == 100, "within-budget short must not be clamped"


def test_long_only_unaffected_by_gross_cap():
    """long_only must be byte-identical regardless of max_gross_exposure — it is
    enforced only in long_short mode (buy() already self-limits to cash)."""
    symbols = [f"S{i}" for i in range(3)]
    start = date(2022, 1, 3)
    days = 5
    price_data = _prices(symbols, start, days)

    def buy_all_when_flat(current_date, portfolio, historical_prices):
        return [
            {"action": "buy", "symbol": s, "shares": 100}
            for s in symbols
            if portfolio.get_position(s).get("shares", 0) == 0
        ]

    def run(mge):
        return BacktestEngine(
            price_data=list(price_data),
            strategy=buy_all_when_flat,
            initial_cash=100_000.0,
            start_date=start,
            end_date=start + timedelta(days=days - 1),
            mode="long_only",
            max_gross_exposure=mge,
        ).run()

    baseline = run(None)["metrics"]
    tight = run(0.5)["metrics"]  # a 0.5 cap would clamp *if* it applied to long_only
    assert tight["total_return"] == baseline["total_return"]
    assert tight["max_drawdown"] == baseline["max_drawdown"]
