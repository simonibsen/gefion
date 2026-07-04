"""Tests for regime slicing MCP tools (005 T015).

Source-inspection style (matches tests/test_mcp_experiment_framework.py): the
MCP layer wraps the CLI, so we assert each tool is defined, dispatched, backed
by a handler, and wraps the correct `regime` CLI command.
"""
import pathlib

SERVER = pathlib.Path("mcp-server/server.py")


def _src() -> str:
    return SERVER.read_text()


REGIME_TOOLS = {
    "regime_define": ("regime", "define"),
    "regime_list": ("regime", "list"),
    "regime_show": ("regime", "show"),
    "regime_compute": ("regime", "compute"),
    "regime_labels": ("regime", "labels"),
    "regime_archive": ("regime", "archive"),
    "regime_definitions_export": ("regime", "export"),
    "regime_definitions_import": ("regime", "import"),
}


def test_all_regime_tools_are_defined():
    src = _src()
    for tool in REGIME_TOOLS:
        assert f'name="{tool}"' in src, f"tool {tool} not defined in list_tools()"


def test_all_regime_tools_are_dispatched():
    src = _src()
    for tool in REGIME_TOOLS:
        assert f'name == "{tool}"' in src, f"tool {tool} not dispatched in call_tool()"


def test_all_regime_handlers_exist():
    src = _src()
    for tool in REGIME_TOOLS:
        assert f"async def _{tool}(" in src, f"handler _{tool} missing"


def test_regime_handlers_wrap_correct_cli_command():
    src = _src()
    for tool, cli in REGIME_TOOLS.items():
        # handler body should reference the regime CLI subcommand it wraps
        needle = f'"{cli[0]}", "{cli[1]}"'
        assert needle in src, f"{tool} handler does not wrap CLI {' '.join(cli)}"
