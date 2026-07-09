"""Strategies act on both directions (009, T014 — US5).

TDD: a strategy's bearish branch emits `short`/`cover` in `long_short` mode and
flattens (no short) in `long_only` — the same signal, mode-gated. mean_reversion
(short the overbought) is US5's named example; the CLI surface is asserted here
too.
"""
import datetime as dt

from gefion.strategies.mean_reversion import MeanReversionStrategy

D = dt.date


def _rising_prices(symbol="AAA", n=20, start=100.0, step=2.0):
    """Steadily rising series → all gains → RSI ≈ 100 (overbought)."""
    return [{"symbol": symbol, "date": D(2025, 1, 1) + dt.timedelta(days=i),
             "close": start + i * step} for i in range(n)]


def _signals(mode):
    strat = MeanReversionStrategy(rsi_period=14, position_size=0.2, mode=mode)
    prices = _rising_prices()
    return strat.generate_signals(
        current_date=prices[-1]["date"], portfolio={}, price_data=prices,
        initial_cash=10_000.0)


def test_mean_reversion_shorts_the_overbought_in_long_short():
    shorts = [s for s in _signals("long_short") if s["action"] == "short"]
    assert shorts and shorts[0]["symbol"] == "AAA"
    assert shorts[0]["shares"] > 0


def test_mean_reversion_long_only_never_shorts():
    assert all(s["action"] != "short" for s in _signals("long_only"))


def test_mean_reversion_defaults_to_long_only():
    strat = MeanReversionStrategy()
    assert strat.mode == "long_only"


def test_backtest_run_cli_exposes_mode_flag():
    from typer.testing import CliRunner
    from gefion.cli import app
    result = CliRunner().invoke(app, ["backtest", "run", "--help"])
    assert result.exit_code == 0
    for opt in ("--mode", "--borrow-rate", "--max-short-exposure"):
        assert opt in result.output


def test_backtest_run_mcp_has_mode_arg():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    # the backtest_run tool + handler thread a mode argument
    assert '"mode"' in server or "'mode'" in server
    assert "long_short" in server
