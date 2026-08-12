"""Clamp equity at zero and mark the account blown (#255).

TDD: written FIRST. #217's maintenance-margin model is reactive -- it fires
per-bar off that bar's own mark-to-market price, so a book of correlated
shorts can gap straight through zero equity in a single bar before the check
ever gets a chance to de-risk it (confirmed diagnosis: 6-month A/B run,
v0.62.0, arm B `total_return: -1.958`, i.e. final equity of -51,178 on
100,000 initial). This module tests the clamp: once mark-to-market equity
reaches <= 0, positions are force-closed, equity is set to exactly 0.0, the
account is marked blown (with the date), and no further signals/trades
happen for the remainder of the run.

The clamp represents a broker's intervention, so it only fires where a
broker exists: an engine with an explicit maintenance-margin RiskManager
attached. With no risk manager at all, there is nothing to enforce a floor
and the engine reports true (possibly negative) equity -- see
`test_no_risk_manager_means_no_clamp` below, and the pre-existing invariant
this revision must not break, `test_negative_equity_is_represented_not_clamped`
in tests/test_backtest_short_risk.py.

Does NOT touch #217's maintenance-margin threshold, #211's gross cap, or the
liquidation logic itself -- those are a live, separate modelling question.
"""
import datetime as dt

from gefion.backtest.engine import BacktestEngine
from gefion.backtest.metrics import calculate_metrics
from gefion.backtest.portfolio import Portfolio
from gefion.backtest.risk import RiskLimits, RiskManager

D = dt.date


def _margin_rm():
    """A RiskManager with the (default) maintenance-margin broker attached."""
    return RiskManager(RiskLimits())


def _price_path(start_price, days, daily_growth):
    """`days` closes starting at start_price, compounding by daily_growth/day."""
    prices = []
    p = start_price
    for i in range(days):
        prices.append(p)
        p *= 1.0 + daily_growth
    return prices


def _prices(closes, symbol="AAA"):
    dates = [D(2025, 1, 1) + dt.timedelta(days=i) for i in range(len(closes))]
    return [{"symbol": symbol, "date": d, "close": c} for d, c in zip(dates, closes)]


def _short_once(shares, symbol="AAA"):
    def strat(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "short", "symbol": symbol, "shares": shares}]
        return []
    return strat


def _relentless_shorter(shares, symbol="AAA"):
    """Issues a fresh short signal EVERY bar -- proves the engine stops
    calling the strategy at all once blown, not just filtering its output."""
    calls = []

    def strat(current_date, portfolio, historical):
        calls.append(current_date)
        return [{"action": "short", "symbol": symbol, "shares": shares}]

    strat.calls = calls
    return strat


# --------------------------------------------------------------------------- #
# 1. The property that matters: total_return floors at exactly -1.0.
# --------------------------------------------------------------------------- #
def test_wipeout_floors_total_return_at_exactly_negative_one():
    """A short that moves violently enough to take equity negative in a
    single bar (the failure shape #217 can't catch in time) results in
    total_return == -1.0 exactly -- never below."""
    cash = 10_000.0
    closes = [100.0, 1000.0]  # 200 shares short @ 100 -> 10x overnight gap
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["metrics"]["total_return"] == -1.0
    assert result["metrics"]["max_drawdown"] == -1.0
    assert result["blown"] is True


def test_bad_but_surviving_run_never_gets_clamped_toward_the_floor():
    """A run that ends deeply negative but never actually breaches zero must
    report its real (ugly) number, not be nudged toward -1.0."""
    cash = 10_000.0
    # 4%/day rise for 60 days -- #217's own margin call de-risks this one
    # (confirmed by test_backtest_margin.py); it should not touch -1.0.
    closes = _price_path(100.0, days=60, daily_growth=0.04)
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["blown"] is False
    assert -1.0 < result["metrics"]["total_return"] < 0


# --------------------------------------------------------------------------- #
# 2. Blown state is terminal.
# --------------------------------------------------------------------------- #
def test_blown_account_stops_trading_for_remainder_of_run():
    cash = 10_000.0
    closes = [100.0, 1000.0, 1000.0, 1000.0, 1000.0]
    prices = _prices(closes)
    strat = _relentless_shorter(200)
    engine = BacktestEngine(
        price_data=prices, strategy=strat, initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["blown"] is True
    blown_date = result["blown_date"]
    assert blown_date is not None

    # No trade recorded after the blow-up bar.
    assert all(t["date"] <= blown_date for t in result["trades"])
    # The engine actually stopped iterating -- it never even called the
    # strategy for a date past the blow-up.
    assert strat.calls[-1] == blown_date
    assert len(strat.calls) < len(prices)
    # The last equity_curve point IS the blow-up bar, clamped to zero.
    assert result["equity_curve"][-1] == {"date": blown_date, "equity": 0.0}
    # No positions remain -- exposure is flat zero on the terminal bar.
    assert result["exposure"][-1]["gross"] == 0.0
    assert result["exposure"][-1]["long"] == 0.0
    assert result["exposure"][-1]["short"] == 0.0


# --------------------------------------------------------------------------- #
# 3. Flag and date are reported on the engine result.
# --------------------------------------------------------------------------- #
def test_engine_result_carries_blown_flag_and_date():
    cash = 10_000.0
    closes = [100.0, 1000.0]
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["blown"] is True
    assert result["blown_date"] == D(2025, 1, 2)


def test_engine_result_reports_blown_false_and_no_date_when_untouched():
    cash = 10_000.0
    closes = _price_path(100.0, days=10, daily_growth=0.001)  # flat, boring
    prices = _prices(closes)

    def noop_strat(current_date, portfolio, historical):
        return []

    engine = BacktestEngine(
        price_data=prices, strategy=noop_strat, initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["blown"] is False
    assert result["blown_date"] is None


# --------------------------------------------------------------------------- #
# 4. A surviving account is untouched.
# --------------------------------------------------------------------------- #
def test_surviving_account_reports_ordinary_return_not_flagged():
    cash = 10_000.0
    # A mild, gradual decline in the underlying -- a short here PROFITS and
    # never comes close to zero equity.
    closes = _price_path(100.0, days=20, daily_growth=-0.01)
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(50), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=_margin_rm(),
    )
    result = engine.run()

    assert result["blown"] is False
    assert result["blown_date"] is None
    assert result["metrics"]["total_return"] > 0  # a profitable short
    assert len(result["equity_curve"]) == len(prices)  # ran to completion


# --------------------------------------------------------------------------- #
# 5. long_only must be byte-identical -- it structurally cannot reach the
#    floor (cash-funded buys keep equity >= 0 always), so this is a no-op.
# --------------------------------------------------------------------------- #
def test_long_only_byte_identical_despite_severe_crash():
    """Even a brutal, sustained crash cannot drive a cash-funded long_only
    book's equity to <= 0 -- assert the engine's equity curve matches a
    hand-computed reference exactly, proving the #255 floor never engaged."""
    closes = _price_path(100.0, days=40, daily_growth=-0.10)
    prices = _prices(closes)
    shares = 50

    def buy_and_hold(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "buy", "symbol": "AAA", "shares": shares}]
        return []

    engine = BacktestEngine(
        price_data=prices, strategy=buy_and_hold, initial_cash=10_000.0,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_only",
    )
    result = engine.run()

    cash_after_buy = 10_000.0 - shares * closes[0]
    expected_equity_curve = [
        {"date": row["date"], "equity": cash_after_buy + shares * px}
        for row, px in zip(prices, closes)
    ]

    assert result["equity_curve"] == expected_equity_curve
    assert result["blown"] is False
    assert result["blown_date"] is None
    assert len(result["equity_curve"]) == len(prices)


# --------------------------------------------------------------------------- #
# 6. Boundary: equity landing exactly at 0.0 is blown; a hair above is not.
# --------------------------------------------------------------------------- #
def _stub_engine():
    return BacktestEngine(
        price_data=[], strategy=lambda *a: [], initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 1), mode="long_short",
    )


def test_boundary_equity_exactly_zero_counts_as_blown():
    engine = _stub_engine()
    portfolio = Portfolio(initial_cash=10_000.0)
    portfolio.positions = {"AAA": {"shares": -100, "avg_price": 100.0}}
    prices = {"AAA": 100.0}
    equity = portfolio.calculate_equity(prices)
    assert equity == 0.0  # sanity: scenario really lands exactly at zero

    clamped_equity, blown = engine._apply_equity_floor(
        portfolio, prices, equity, D(2025, 1, 1))

    assert blown is True
    assert clamped_equity == 0.0
    assert portfolio.positions == {}
    assert portfolio.cash == 0.0


def test_boundary_equity_a_hair_above_zero_is_not_blown():
    engine = _stub_engine()
    portfolio = Portfolio(initial_cash=10_000.0)
    portfolio.positions = {"AAA": {"shares": -100, "avg_price": 100.0}}
    prices = {"AAA": 99.99}  # equity = 10_000 - 9_999 = 1.0 > 0
    equity = portfolio.calculate_equity(prices)
    assert equity > 0.0

    clamped_equity, blown = engine._apply_equity_floor(
        portfolio, prices, equity, D(2025, 1, 1))

    assert blown is False
    assert clamped_equity == equity
    assert portfolio.positions == {"AAA": {"shares": -100, "avg_price": 100.0}}


def test_boundary_reproduced_end_to_end_via_calculate_metrics():
    """Sanity-check that the property in test 1 is not an artifact of
    calculate_metrics' own rounding -- feeding a synthetic equity_curve whose
    last point is exactly 0.0 reproduces total_return == -1.0."""
    curve = [{"date": D(2025, 1, 1), "equity": 10_000.0},
             {"date": D(2025, 1, 2), "equity": 0.0}]
    metrics = calculate_metrics(curve, initial_capital=10_000.0)
    assert metrics["total_return"] == -1.0
    assert metrics["max_drawdown"] == -1.0


# --------------------------------------------------------------------------- #
# 7. The floor represents a broker's intervention -- with no margin model
#    attached, there is no broker, so equity is represented faithfully, even
#    negative. This is the invariant a prior revision of #255 broke; also
#    asserted (as the pre-existing, deliberate spec-009 test that caught it)
#    in tests/test_backtest_short_risk.py::test_negative_equity_is_represented_not_clamped.
# --------------------------------------------------------------------------- #
def test_no_risk_manager_means_no_clamp():
    """long_short with NO risk_manager at all: equity goes negative and is
    reported as-is -- there's no broker to foreclose the account."""
    cash = 10_000.0
    closes = [100.0, 1000.0]  # 200 shares short @ 100 -> 10x overnight gap
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", risk_manager=None,
    )
    result = engine.run()

    assert result["blown"] is False
    assert result["blown_date"] is None
    assert result["equity_curve"][-1]["equity"] == 10_000.0 - 200 * (1000.0 - 100.0)
    assert result["metrics"]["total_return"] < -1.0  # allowed to go past -100%


def test_has_margin_model_false_when_risk_manager_has_no_maintenance_margin():
    """An explicit RiskManager that opted OUT of maintenance margin
    (maintenance_margin=None) is not a broker either -- no clamp."""
    rm = RiskManager(RiskLimits(maintenance_margin=None))
    engine = BacktestEngine(
        price_data=[], strategy=lambda *a: [], initial_cash=10_000.0,
        start_date=D(2025, 1, 1), end_date=D(2025, 1, 1), mode="long_short",
        risk_manager=rm,
    )
    assert engine._has_margin_model() is False
