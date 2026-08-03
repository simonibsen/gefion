"""#197: the backtest_ab_compare MCP tool must wrap `backtest ab-compare`.

Source-inspection style (matches tests/test_mcp_backtest_flags.py): the MCP
layer shells out to the CLI, so the tool must be registered, dispatched, and
map its args to the real CLI flags (--arm-a / --arm-b / --attribution).
"""
import pathlib

SERVER = pathlib.Path("mcp-server/server.py")


def _ab_compare_body() -> str:
    src = SERVER.read_text()
    start = src.index("async def _backtest_ab_compare(")
    end = src.index("\nasync def ", start + 1)
    return src[start:end]


def test_tool_registered():
    src = SERVER.read_text()
    assert 'name="backtest_ab_compare"' in src, \
        "backtest_ab_compare must be registered in list_tools()"


def test_tool_dispatched():
    src = SERVER.read_text()
    assert 'name == "backtest_ab_compare"' in src, \
        "backtest_ab_compare must be routed in call_tool()"
    assert "_backtest_ab_compare(arguments)" in src


def test_handler_wraps_ab_compare_subcommand():
    body = _ab_compare_body()
    assert "'ab-compare'" in body or '"ab-compare"' in body, \
        "handler must invoke the `backtest ab-compare` subcommand"


def test_handler_maps_arm_flags():
    body = _ab_compare_body()
    assert "'--arm-a'" in body or '"--arm-a"' in body
    assert "'--arm-b'" in body or '"--arm-b"' in body


def test_handler_maps_attribution_flag():
    body = _ab_compare_body()
    assert "'--attribution'" in body or '"--attribution"' in body


def test_handler_maps_dates():
    body = _ab_compare_body()
    assert "'--start-date'" in body or '"--start-date"' in body
    assert "'--end-date'" in body or '"--end-date"' in body
