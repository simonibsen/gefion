"""#169: the backtest_run MCP wrapper must emit --horizon-days, not --horizon.

Source-inspection style (matches tests/test_mcp_regime.py): the MCP layer wraps
the CLI. `gefion backtest run` accepts --horizon-days (not --horizon), so a bare
--horizon in the handler is flag-drift that fails every ml_signal / ml_filter
backtest routed through MCP with "No such option: --horizon".
"""
import pathlib

SERVER = pathlib.Path("mcp-server/server.py")


def _backtest_run_body() -> str:
    src = SERVER.read_text()
    start = src.index("async def _backtest_run(")
    end = src.index("\nasync def ", start + 1)
    return src[start:end]


def test_backtest_run_emits_horizon_days_not_bare_horizon():
    body = _backtest_run_body()
    # The CLI option is --horizon-days; a bare --horizon is rejected outright.
    assert "'--horizon-days'" in body or '"--horizon-days"' in body, \
        "backtest_run handler must wrap --horizon-days"
    assert "['--horizon'," not in body and '["--horizon",' not in body, \
        "backtest_run handler must not emit bare --horizon (CLI rejects it; #169)"
