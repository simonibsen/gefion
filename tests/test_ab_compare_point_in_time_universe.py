"""The A/B must gate its trade universe point-in-time, not as-of-today.

`run_arm` resolved the trade universe with `universe_members(conn, name)` --
no `as_of`, so it defaults to `date.today()` -- and passed that flat list as
`symbols=`. `load_price_data_for_backtest` documents that explicit symbols
BYPASS the date-aware universe gate. So a 2023 backtest was run against 2026's
universe.

Measured on production for the epic #179 window:

    members as-of TODAY      : 3,641
    members as-of 2023-08-04 : 4,087

    ADIL  in-today=True  in-2023-08-04=False
    FFAI  in-today=True  in-2023-08-04=False
    MNTS  in-today=True  in-2023-08-04=False
    SMX   in-today=True  in-2023-08-04=False

All four names that blew up the A/B were sub-$1 on the trade date and were
correctly excluded by the universe's own `no-penny-stocks` rule (close < 1.0).
They are members TODAY only because of the reverse splits that killed the
account. The rule worked; the A/B never asked it.

Both biases are present at once: 184 symbols qualify today but were not
tradeable then (look-ahead), and the 2023 universe was LARGER (4,087 vs
3,641), so ~630 companies that existed then have delisted and are missing
entirely (survivorship).

The fix is to hand the loader the universe NAME so its date-aware gate binds
each bar to its own date, rather than pre-resolving a list.
"""
from datetime import date

from gefion.backtest import ab_compare


class _FakeEngine:
    def __init__(self, **kwargs):
        pass

    def run(self):
        return {"metrics": {}, "equity_curve": [], "trades": []}


class _Strat:
    def __init__(self, **kwargs):
        pass

    def generate_signals(self, *args, **kwargs):
        return []


class _Loader:
    """Captures how the price loader was called."""

    calls = []

    def __call__(self, *args, **kwargs):
        _Loader.calls.append(kwargs)
        return []


def _run(monkeypatch, resolver=None):
    _Loader.calls = []
    loader = _Loader()
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: None)
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest", loader)
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _Strat)
    if resolver is not None:
        monkeypatch.setattr(ab_compare, "_default_universe_resolver", resolver)

    config = ab_compare.MatchedConfig(
        start_date=date(2023, 7, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={"return_threshold": 0.02})
    # conn is a truthy sentinel: the resolver is stubbed, so it is never used
    # for real I/O, but `run_arm` branches on `conn is not None`.
    ab_compare.run_arm(
        ab_compare.ArmSpec("A", train_universe="u", trade_universe="nasdaq-only"),
        config, conn=object())
    return _Loader.calls[0]


def test_loader_receives_the_universe_name_not_a_resolved_list(monkeypatch):
    """The date-aware gate only runs when the loader is given the universe."""
    call = _run(monkeypatch, resolver=lambda conn, name, as_of=None: {"AAPL", "ADIL"})

    assert call.get("universe") == "nasdaq-only"


def test_loader_is_not_handed_explicit_symbols(monkeypatch):
    """Explicit symbols are a documented BYPASS of the universe gate. Passing
    them is what let a 2023 backtest trade 2026's universe."""
    call = _run(monkeypatch, resolver=lambda conn, name, as_of=None: {"AAPL", "ADIL"})

    assert not call.get("symbols"), (
        "passing symbols= bypasses the point-in-time universe gate")


def test_backtest_window_dates_still_bound_the_load(monkeypatch):
    call = _run(monkeypatch, resolver=lambda conn, name, as_of=None: {"AAPL"})

    assert call.get("start_date") == date(2023, 7, 1)
    assert call.get("end_date") == date(2023, 12, 31)


def test_prediction_membership_is_resolved_at_both_window_endpoints(monkeypatch):
    """Predictions must COVER the window. Resolving as-of-today alone silently
    omits every company that traded during the window and has since delisted --
    the survivorship half. `as_of` must be passed explicitly, never defaulted."""
    seen = []

    def _resolver(conn, name, as_of=None):
        seen.append(as_of)
        return {"AAPL"} if as_of == date(2023, 7, 1) else {"MSFT"}

    _run(monkeypatch, resolver=_resolver)

    assert date(2023, 7, 1) in seen, "window start not resolved"
    assert date(2023, 12, 31) in seen, "window end not resolved"
    assert None not in seen, (
        "as_of=None falls back to date.today() -- the bias this fixes")


def test_trade_universe_defaults_to_the_train_universe(monkeypatch):
    """ArmSpec leaves trade_universe unset for the plain case; the gate must
    still name a universe rather than falling through to ungated."""
    _Loader.calls = []
    loader = _Loader()
    monkeypatch.setattr(ab_compare, "_run_cli", lambda cmd: None)
    monkeypatch.setattr(
        "gefion.backtest.data_loader.load_price_data_for_backtest", loader)
    monkeypatch.setattr("gefion.backtest.engine.BacktestEngine", _FakeEngine)
    monkeypatch.setattr("gefion.strategies.ml_signal.MLSignalStrategy", _Strat)
    monkeypatch.setattr(ab_compare, "_default_universe_resolver",
                        lambda conn, name, as_of=None: {"AAPL"})

    config = ab_compare.MatchedConfig(
        start_date=date(2023, 7, 1), end_date=date(2023, 12, 31),
        split_spec={}, horizons=[30], horizon_days=30,
        hyperparams={"algorithm": "xgboost"},
        strategy_params={"return_threshold": 0.02})
    ab_compare.run_arm(ab_compare.ArmSpec("A", train_universe="u"),
                       config, conn=object())

    assert _Loader.calls[0].get("universe") == "u"
