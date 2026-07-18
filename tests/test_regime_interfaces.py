"""Interface-parity tests for regime slicing (005 T038, US4).

FR-013 / Constitution III: every regime operation must be reachable via CLI, MCP,
and (where applicable) the UI. The parity matrix in
specs/005-regime-slicing/contracts/interfaces.md is the source of truth; this test
keeps the three surfaces from drifting apart.
"""
import pathlib

import pytest
from typer.testing import CliRunner

from gefion.cli import app

REPO = pathlib.Path(__file__).parent.parent
runner = CliRunner()


# operation -> (CLI invocation prefix, MCP tool name, UI reference or None)
PARITY = {
    "define": (["regime", "define"], "regime_define", "views/regimes.py"),
    "list": (["regime", "list"], "regime_list", "views/regimes.py"),
    "show": (["regime", "show"], "regime_show", "views/regimes.py"),
    "compute": (["regime", "compute"], "regime_compute", "views/regimes.py"),
    "labels": (["regime", "labels"], "regime_labels", "views/regimes.py"),
    "archive": (["regime", "archive"], "regime_archive", "views/regimes.py"),
    "export": (["regime", "export"], "regime_definitions_export", None),
    "import": (["regime", "import"], "regime_definitions_import", None),
    "interaction": (["regime", "interaction"], "regime_interaction", "views/regimes.py"),
}


@pytest.mark.parametrize("op", sorted(PARITY))
def test_cli_surface_exists(op):
    cli_path = PARITY[op][0]
    result = runner.invoke(app, cli_path + ["--help"])
    assert result.exit_code == 0, f"CLI surface missing for {op}: gefion {' '.join(cli_path)}"


@pytest.mark.parametrize("op", sorted(PARITY))
def test_mcp_surface_exists(op):
    tool = PARITY[op][1]
    server = (REPO / "mcp-server" / "server.py").read_text()
    assert f'name="{tool}"' in server, f"MCP tool {tool} missing for {op}"
    assert f'name == "{tool}"' in server, f"MCP dispatch missing for {tool}"


def test_ui_surface_exists():
    """The Regimes page provides the UI door for regime operations."""
    view = REPO / "src" / "gefion" / "ui" / "views" / "regimes.py"
    assert view.exists()
    content = view.read_text()
    assert "def render_regimes(" in content
    app_py = (REPO / "src" / "gefion" / "ui" / "app.py").read_text()
    assert "render_regimes" in app_py


def test_sliced_backtest_parity():
    """--by-regime exists on the backtest CLI, MCP tool, and UI view."""
    result = runner.invoke(app, ["backtest", "run", "--help"])
    assert "--by-regime" in result.output
    server = (REPO / "mcp-server" / "server.py").read_text()
    assert '"by_regime"' in server
    view = (REPO / "src" / "gefion" / "ui" / "views" / "backtest.py").read_text()
    assert "by_regime" in view or "--by-regime" in view


def test_conditional_experiment_parity():
    """--by-regime exists on experiment run CLI, MCP tool, and the results UI."""
    result = runner.invoke(app, ["experiment", "run", "--help"])
    assert "--by-regime" in result.output
    server = (REPO / "mcp-server" / "server.py").read_text()
    start = server.index("async def _experiment_run(")
    body = server[start:server.index("\nasync def ", start + 1)]
    assert "--by-regime" in body
    view = (REPO / "src" / "gefion" / "ui" / "views" / "experiments.py").read_text()
    assert "_render_by_regime_verdicts" in view


def test_operator_skill_mentions_regime_tools():
    """Constitution III: new MCP tools require the /gefion operator skill to be updated."""
    skill = (REPO / ".claude" / "commands" / "gefion.md").read_text()
    assert "regime" in skill.lower(), (
        "/gefion operator skill does not mention the regime tools — update its "
        "tool routing (Constitution III)"
    )


# --- 014: candidate gate + composite parity (T015/T016/T023) -----------------------

CANDIDATE_TOOLS = [
    "macro_candidate_list", "macro_candidate_show",
    "macro_candidate_approve", "macro_candidate_reject",
    "macro_propose",
    "macro_register_composite",
]


@pytest.mark.parametrize("tool", CANDIDATE_TOOLS)
def test_candidate_mcp_surface_exists(tool):
    server = (REPO / "mcp-server" / "server.py").read_text()
    assert f'name="{tool}"' in server, f"MCP tool {tool} missing"
    assert f'name == "{tool}"' in server, f"MCP dispatch missing for {tool}"


def test_candidate_ui_surface_exists():
    """The UI presents the pending queue + review packet (read-only —
    decisions are deliberate CLI/MCP acts)."""
    view = REPO / "src" / "gefion" / "ui" / "views" / "candidates.py"
    assert view.exists()
    content = view.read_text()
    assert "def render_candidates(" in content
    app_py = (REPO / "src" / "gefion" / "ui" / "app.py").read_text()
    assert "render_candidates" in app_py
