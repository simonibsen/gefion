"""Cross-sectional decile strategy (the run-13 finding's proper vehicle).

TDD: written FIRST. Rank the universe by each stock's own signal (stochastic-K
computed causally from its price history), long the top decile, short the
bottom (long_short) — and, when gated by a regime, act ONLY while the market
state holds (flat otherwise). Every rebalance CLOSES the whole book first:
no stacking, so no -91M leverage spirals by construction.
"""
import datetime as dt


def _prices(vals, start=dt.date(2024, 1, 1)):
    out = []
    for i, v in enumerate(vals):
        v = float(v)
        out.append({"date": start + dt.timedelta(days=i),
                    "close": v, "high": v * 1.02, "low": v * 0.98,
                    "volume": 1000})
    return out


def _universe(n=30, days=40):
    """Symbols S00..S29 with monotone-distinct trends so decile ranks are
    deterministic: higher index = stronger uptrend = higher stoch-K."""
    data = {}
    for j in range(n):
        drift = (j - n / 2) * 0.002
        data[f"S{j:02d}"] = _prices([100 * (1 + drift) ** i for i in range(days)])
    return data


class _Portfolio:
    def __init__(self):
        self.positions = {}
        self.cash = 100_000.0


def test_stoch_k_causal_and_guarded():
    from gefion.strategies.cross_sectional import calculate_stoch_k
    up = _prices([100 + i for i in range(30)])
    assert calculate_stoch_k(up, period=14) > 80          # rising -> high K
    flat = _prices([100.0] * 30)
    flat = [{**p, "high": 100.0, "low": 100.0} for p in flat]
    assert calculate_stoch_k(flat, period=14) is None     # zero range: skip
    assert calculate_stoch_k(up[:3], period=14) is None   # insufficient history


def test_long_short_book_is_top_and_bottom_decile():
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    strat = CrossSectionalDecileStrategy(rebalance_days=20, mode="long_short")
    data = _universe()
    d = dt.date(2024, 2, 9)                               # 40 days in
    signals = strat.generate_signals(d, _Portfolio(), data, 100_000.0)
    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}
    assert buys == {f"S{j:02d}" for j in range(27, 30)}   # top decile of 30
    assert shorts == {f"S{j:02d}" for j in range(0, 3)}   # bottom decile
    assert not buys & shorts


def test_long_only_mode_never_shorts():
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    strat = CrossSectionalDecileStrategy(mode="long_only")
    signals = strat.generate_signals(dt.date(2024, 2, 9), _Portfolio(),
                                     _universe(), 100_000.0)
    assert signals and all(s["action"] == "buy" for s in signals)


def test_rebalance_closes_whole_book_first():
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    strat = CrossSectionalDecileStrategy(rebalance_days=20, mode="long_short")
    p = _Portfolio()
    p.positions = {"OLD1": {"shares": 10, "avg_price": 5.0},
                   "OLD2": {"shares": -10, "avg_price": 5.0}}
    signals = strat.generate_signals(dt.date(2024, 2, 9), p, _universe(),
                                     100_000.0)
    closes = [(s["action"], s["symbol"]) for s in signals
              if s["symbol"] in ("OLD1", "OLD2")]
    assert ("sell", "OLD1") in closes                     # long closed
    assert ("cover", "OLD2") in closes                    # short covered
    # and closes are emitted BEFORE any new opens
    first_open = next(i for i, s in enumerate(signals)
                      if s["action"] in ("buy", "short"))
    last_close = max(i for i, s in enumerate(signals)
                     if s["action"] in ("sell", "cover"))
    assert last_close < first_open


def test_regime_gate_flat_out_of_state():
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    d = dt.date(2024, 2, 9)
    strat = CrossSectionalDecileStrategy(
        rebalance_days=20, mode="long_short",
        gate_labels={d: "low"}, gate_bucket="high")
    p = _Portfolio()
    p.positions = {"OLD1": {"shares": 10, "avg_price": 5.0}}
    signals = strat.generate_signals(d, p, _universe(), 100_000.0)
    # out of state: close everything, open NOTHING
    assert [s["action"] for s in signals] == ["sell"]
    # in state: opens normally
    strat2 = CrossSectionalDecileStrategy(
        rebalance_days=20, mode="long_short",
        gate_labels={d: "high"}, gate_bucket="high")
    assert any(s["action"] == "buy"
               for s in strat2.generate_signals(d, _Portfolio(), _universe(),
                                                100_000.0))


def test_no_rebalance_between_scheduled_days():
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    strat = CrossSectionalDecileStrategy(rebalance_days=20, mode="long_short")
    data = _universe()
    d1 = dt.date(2024, 2, 9)
    assert strat.generate_signals(d1, _Portfolio(), data, 100_000.0)
    d2 = d1 + dt.timedelta(days=3)                        # too soon
    assert strat.generate_signals(d2, _Portfolio(), data, 100_000.0) == []


def test_gross_exposure_capped():
    """Each side sized from allocation; gross (long+|short|) never exceeds
    initial_cash * allocation_pct — the -91M lesson, structural."""
    from gefion.strategies.cross_sectional import CrossSectionalDecileStrategy
    strat = CrossSectionalDecileStrategy(rebalance_days=20, mode="long_short",
                                         allocation_pct=0.8)
    data = _universe()
    signals = strat.generate_signals(dt.date(2024, 2, 9), _Portfolio(), data,
                                     100_000.0)
    last = {s: h[-1]["close"] for s, h in data.items()}
    gross = sum(abs(sig["shares"]) * last[sig["symbol"]] for sig in signals
                if sig["action"] in ("buy", "short"))
    assert gross <= 80_000.0 + 1e-6


def test_cli_surface_has_new_flags():
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, ["backtest", "run", "--help"])
    assert r.exit_code == 0
    for opt in ("cross_sectional_decile", "--top-liquid", "--gate-regime",
                "--gate-bucket"):
        assert opt in r.output


def test_registered_in_builtin_strategies():
    from gefion.strategies.dispatcher import BUILTIN_STRATEGIES
    info = BUILTIN_STRATEGIES["cross_sectional_decile"]
    assert info["class_name"] == "CrossSectionalDecileStrategy"
