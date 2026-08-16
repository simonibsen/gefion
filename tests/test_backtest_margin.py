"""Maintenance margin + forced liquidation (#217).

TDD: written FIRST. #211 bounds gross exposure at ENTRY only; nothing intervened
on the mark-to-market path *between* rebalances, so a losing long/short book
could compound past -100% (an impossible, unfunded account). A broker calls
margin before that happens: per-bar, if equity / gross_exposure drops below
`RiskLimits.maintenance_margin`, close just enough of the largest unrealised
loser(s) to restore the ratio above maintenance + a small buffer.
"""
import datetime as dt

from gefion.backtest.costs import TransactionCosts
from gefion.backtest.engine import BacktestEngine
from gefion.backtest.portfolio import Portfolio
from gefion.backtest.risk import RiskLimits, RiskManager

D = dt.date


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


def test_equity_never_goes_below_negative_100_pct():
    """A short sized to #211's own long_short default budget (2x equity),
    against a price that keeps rising for many bars, must not be allowed to
    compound past a -100% return -- the account would be unfundable."""
    cash = 10_000.0
    # 200 shares @ 100 = 20_000 notional = 2x equity, i.e. the max_gross_exposure
    # default budget for long_short -- an aggressive but not out-of-budget book.
    closes = _price_path(100.0, days=60, daily_growth=0.04)  # ~4%/day, sharp rise
    prices = _prices(closes)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short",
    )  # no risk_manager passed -- margin call must be on by default
    result = engine.run()

    assert result["metrics"]["total_return"] > -1.0
    assert any(t.get("reason") == "margin_call" for t in result["trades"]), \
        "expected at least one forced liquidation along the sharp rise"
    assert any(e["action"] == "forced_cover" for e in result["margin_events"])


def test_partial_liquidation_restores_ratio_without_closing_everything():
    """A mild breach is de-risked just enough -- the position survives, and the
    post-liquidation ratio clears the maintenance threshold."""
    limits = RiskLimits(maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    portfolio.cash = 12_400.0
    portfolio.positions = {"AAA": {"shares": -100, "avg_price": 100.0}}
    prices = {"AAA": 100.0}

    equity = portfolio.calculate_equity(prices)
    gross = 100 * prices["AAA"]
    assert equity / gross < 0.25  # sanity: scenario is actually breached

    exits = rm.generate_exit_signals(portfolio, prices)
    assert len(exits) == 1
    assert exits[0]["symbol"] == "AAA"
    assert exits[0]["action"] == "cover"
    assert 0 < exits[0]["shares"] < 100  # partial -- not a full close

    portfolio.cover(symbol="AAA", shares=exits[0]["shares"], price=100.0,
                     date=D(2025, 1, 1))

    assert portfolio.positions  # book still holds the position
    new_equity = portfolio.calculate_equity(prices)
    new_gross = abs(portfolio.positions["AAA"]["shares"]) * prices["AAA"]
    assert new_equity / new_gross >= 0.25


def test_no_refire_immediately_after_liquidation():
    """The bar right after a liquidation, at the same prices, does not
    trigger a second one -- proves the restore buffer isn't razor-thin."""
    limits = RiskLimits(maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    portfolio.cash = 12_400.0
    portfolio.positions = {"AAA": {"shares": -100, "avg_price": 100.0}}
    prices = {"AAA": 100.0}

    exits = rm.generate_exit_signals(portfolio, prices)
    assert exits  # first call breaches
    portfolio.cover(symbol="AAA", shares=exits[0]["shares"], price=100.0,
                     date=D(2025, 1, 1))

    refire = rm.generate_exit_signals(portfolio, prices)
    assert refire == []


def test_liquidation_order_is_largest_unrealised_loser_first():
    """Given two losing shorts, the position causing the bigger loss closes
    first."""
    limits = RiskLimits(maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    # AAA: entry 100 -> 140, loss 2_000. BBB: entry 100 -> 110, loss 500.
    portfolio.positions = {
        "AAA": {"shares": -50, "avg_price": 100.0},
        "BBB": {"shares": -50, "avg_price": 100.0},
    }
    prices = {"AAA": 140.0, "BBB": 110.0}
    # cash chosen so equity / gross breaches 0.25
    gross = 50 * 140.0 + 50 * 110.0
    portfolio.cash = 2_000.0 + gross  # equity = 2_000

    exits = rm.generate_exit_signals(portfolio, prices)
    assert exits
    assert exits[0]["symbol"] == "AAA"  # the bigger loser closes first


def test_liquidation_tie_break_is_order_independent():
    """Two positions with EXACTLY equal unrealised loss must liquidate in a
    deterministic order regardless of the order `portfolio.positions` happens
    to iterate in -- `sorted(unrealized, key=unrealized.get)` is stable, so
    without a secondary key a tie silently inherits dict insertion order."""
    limits = RiskLimits(maintenance_margin=0.25)

    def _exits_for(order):
        rm = RiskManager(limits)
        portfolio = Portfolio(initial_cash=0.0)
        all_positions = {
            "AAA": {"shares": -50, "avg_price": 100.0},
            "BBB": {"shares": -50, "avg_price": 100.0},
        }
        portfolio.positions = {sym: all_positions[sym] for sym in order}
        prices = {"AAA": 140.0, "BBB": 140.0}  # identical unrealised loss
        gross = 50 * 140.0 + 50 * 140.0
        portfolio.cash = 2_000.0 + gross  # same breach as the test above
        return rm.generate_exit_signals(portfolio, prices)

    order_forward = [e["symbol"] for e in _exits_for(["AAA", "BBB"])]
    order_reversed = [e["symbol"] for e in _exits_for(["BBB", "AAA"])]

    assert order_forward == order_reversed, (
        "tie-break order must not depend on portfolio.positions dict order"
    )


def test_long_only_byte_identical_with_or_without_default_margin_risk_manager():
    """long_only must be unaffected: cash-funded buys keep equity/gross >= 1
    always, so the maintenance-margin check (default-on for long_short) is a
    structural no-op there -- assert exact equality, not just that it passes."""
    closes = _price_path(100.0, days=20, daily_growth=-0.05)  # a real drawdown
    prices = _prices(closes)

    def buy_and_hold(current_date, portfolio, historical):
        if current_date == D(2025, 1, 1):
            return [{"action": "buy", "symbol": "AAA", "shares": 50}]
        return []

    def run(risk_manager):
        return BacktestEngine(
            price_data=list(prices), strategy=buy_and_hold, initial_cash=10_000.0,
            start_date=prices[0]["date"], end_date=prices[-1]["date"],
            mode="long_only", risk_manager=risk_manager,
        ).run()

    baseline = run(None)
    with_default_margin_rm = run(RiskManager(RiskLimits()))

    assert with_default_margin_rm["equity_curve"] == baseline["equity_curve"]
    assert with_default_margin_rm["trades"] == baseline["trades"]
    assert with_default_margin_rm["metrics"] == baseline["metrics"]


def test_margin_call_liquidation_pays_costs():
    """A forced liquidation is a real trade -- it must incur commission like
    any ordinary exit, not a costless teleport out of the position."""
    cash = 10_000.0
    closes = _price_path(100.0, days=60, daily_growth=0.04)
    prices = _prices(closes)
    costs = TransactionCosts(commission_per_trade=5.0)
    engine = BacktestEngine(
        price_data=prices, strategy=_short_once(200), initial_cash=cash,
        start_date=prices[0]["date"], end_date=prices[-1]["date"],
        mode="long_short", costs=costs,
    )
    result = engine.run()

    margin_trades = [t for t in result["trades"] if t.get("reason") == "margin_call"]
    assert margin_trades
    assert all(t.get("cost", 0) > 0 for t in margin_trades)


def test_stop_loss_and_margin_call_same_bar_does_not_double_liquidate():
    """#252: a stop-loss exit and a margin breach landing on the SAME bar must
    not cause the margin check to also close an untouched position. AAA moves
    far enough (15%) to trip its own 10% stop-loss; BBB only moves 5% and
    would never trip a stop or a margin call on its own. Once AAA's exposure
    is (correctly) excluded from the margin sizing pass, equity/gross for the
    remaining book alone already clears maintenance_margin + buffer, so BBB
    must be left alone."""
    limits = RiskLimits(stop_loss_pct=0.10, maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    portfolio.positions = {
        "AAA": {"shares": -100, "avg_price": 100.0},
        "BBB": {"shares": -100, "avg_price": 100.0},
    }
    prices = {"AAA": 115.0, "BBB": 105.0}  # AAA -15%, BBB -5%
    portfolio.cash = 24_992.0  # equity ~= 2_992 -> equity/gross(full) ~= 0.136

    equity = portfolio.calculate_equity(prices)
    gross_full = 100 * prices["AAA"] + 100 * prices["BBB"]
    assert equity / gross_full < 0.25  # sanity: full book looks breached

    exits = rm.generate_exit_signals(portfolio, prices)

    assert len(exits) == 1
    assert exits[0]["symbol"] == "AAA"
    assert exits[0]["reason"] == "stop_loss"
    assert not any(e["reason"] == "margin_call" for e in exits), \
        "BBB must not be liquidated for a margin shortfall that no longer " \
        "exists once AAA's exposure leaves the book"


def test_stop_loss_alone_insufficient_still_triggers_margin_call():
    """Same shape as above, but the remaining book (after AAA's stop-loss
    exposure is excluded) still breaches maintenance margin -- the fix must
    not disable margin calls outright, only stop over-closing. The margin
    call should close only what's necessary on BBB, not all of it."""
    limits = RiskLimits(stop_loss_pct=0.10, maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    portfolio.positions = {
        "AAA": {"shares": -100, "avg_price": 100.0},
        "BBB": {"shares": -100, "avg_price": 100.0},
    }
    prices = {"AAA": 115.0, "BBB": 105.0}  # AAA -15%, BBB -5%
    portfolio.cash = 24_000.0  # equity = 2_000 -- deep enough that BBB alone
                                # (gross 10_500) still breaches 0.25 + buffer

    equity = portfolio.calculate_equity(prices)
    assert equity == 2_000.0
    gross_bbb_only = 100 * prices["BBB"]
    assert equity / gross_bbb_only < 0.27  # sanity: still breached post-AAA

    exits = rm.generate_exit_signals(portfolio, prices)

    stop_loss_exits = [e for e in exits if e["reason"] == "stop_loss"]
    margin_exits = [e for e in exits if e["reason"] == "margin_call"]

    assert len(stop_loss_exits) == 1
    assert stop_loss_exits[0]["symbol"] == "AAA"

    assert len(margin_exits) == 1
    assert margin_exits[0]["symbol"] == "BBB"
    assert 0 < margin_exits[0]["shares"] < 100  # partial, not a full close
    assert margin_exits[0]["shares"] == 30  # exact -- sized to just the shortfall


def test_no_stop_loss_configured_margin_behaviour_is_byte_identical():
    """Regression: with stop_loss_pct=None, already_exiting is always empty,
    so the #252 fix (excluding already_exiting from the sizing pass) must be
    a complete no-op here. Exact expected values are hand-computed from the
    unchanged formula to prove the bound didn't move."""
    limits = RiskLimits(stop_loss_pct=None, maintenance_margin=0.25)
    rm = RiskManager(limits)

    portfolio = Portfolio(initial_cash=0.0)
    portfolio.positions = {
        "AAA": {"shares": -50, "avg_price": 100.0},
        "BBB": {"shares": -50, "avg_price": 100.0},
    }
    prices = {"AAA": 140.0, "BBB": 110.0}
    gross = 50 * 140.0 + 50 * 110.0
    portfolio.cash = 2_000.0 + gross  # equity = 2_000

    exits = rm.generate_exit_signals(portfolio, prices)

    assert len(exits) == 1
    assert exits[0]["symbol"] == "AAA"
    assert exits[0]["action"] == "cover"
    assert exits[0]["reason"] == "margin_call"
    assert exits[0]["shares"] == 37  # hand-computed: unchanged close_budget math
