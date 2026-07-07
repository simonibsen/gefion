"""Interface-parity tests for agentic regime discovery (006, T016/T017 — US1).

FR-115 / Constitution III: every discovery operation is reachable via CLI,
MCP, and UI. The matrix in specs/006-agentic-regime-discovery/contracts/
interfaces.md is the source of truth; operations join this test as their
increment lands (US1: start/list/show).
"""
import os
import pathlib

import psycopg
import pytest
from typer.testing import CliRunner

from gefion.cli import app
from gefion.db import schema

REPO = pathlib.Path(__file__).parent.parent
runner = CliRunner()


# operation -> (CLI invocation prefix, MCP tool name, UI hook in views/regimes.py)
PARITY = {
    "start": (["regime", "discover", "start"], "regime_discover_start", "_render_discovery_start"),
    "list": (["regime", "discover", "list"], "regime_discover_list", "_render_discovery_tab"),
    "show": (["regime", "discover", "show"], "regime_discover_show", "_render_discovery_run_detail"),
}


@pytest.mark.parametrize("op", sorted(PARITY))
def test_cli_surface_exists(op):
    cli_path = PARITY[op][0]
    result = runner.invoke(app, cli_path + ["--help"])
    assert result.exit_code == 0, f"CLI surface missing: gefion {' '.join(cli_path)}"


@pytest.mark.parametrize("op", sorted(PARITY))
def test_mcp_surface_exists(op):
    tool = PARITY[op][1]
    server = (REPO / "mcp-server" / "server.py").read_text()
    assert f'name="{tool}"' in server, f"MCP tool {tool} missing for {op}"
    assert f'name == "{tool}"' in server, f"MCP dispatch missing for {tool}"


@pytest.mark.parametrize("op", sorted(PARITY))
def test_ui_surface_exists(op):
    hook = PARITY[op][2]
    view = (REPO / "src" / "gefion" / "ui" / "views" / "regimes.py").read_text()
    assert hook in view, f"UI hook {hook} missing for {op}"


def test_start_declares_contract_options():
    result = runner.invoke(app, ["regime", "discover", "start", "--help"])
    for opt in ("--name", "--atoms", "--depth", "--budget", "--tier",
                "--signal-source", "--grading-scheme", "--universe-filter",
                "--fresh-holdout", "--seed", "--dataset", "--json"):
        assert opt in result.output, f"contract option {opt} missing on start"


def test_mcp_start_is_not_read_only():
    """regime_discover_start mutates and can run long — it must not be in a
    read-only allowlist bucket (contracts/mcp.md)."""
    server = (REPO / "mcp-server" / "server.py").read_text()
    start = server.index('name="regime_discover_start"')
    block = server[start:start + 2000]
    assert "readOnlyHint" not in block or '"readOnlyHint": False' in block


# --- functional CLI reads against the test DB --------------------------------

def _db_available():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        return False
    try:
        psycopg.connect(schema.test_db_url()).close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
def db_url():
    if not _db_available():
        pytest.skip("DB tests disabled or DB unavailable")
    return schema.test_db_url()


def test_discover_list_json(db_url):
    result = runner.invoke(app, ["regime", "discover", "list", "--db-url", db_url, "--json"])
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert isinstance(payload["data"]["runs"], list)


def test_discover_show_unknown_run_is_honest(db_url):
    result = runner.invoke(
        app, ["regime", "discover", "show", "no-such-run", "--db-url", db_url])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
