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
    "ledger": (["regime", "discover", "ledger"], "regime_discover_ledger", "_render_discovery_ledger"),
    "verdicts": (["regime", "discover", "verdicts"], "regime_discover_verdicts", "_render_discovery_verdicts"),
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


@pytest.fixture
def completed_run(db_url):
    """A tiny completed discovery run in the test DB (losers included)."""
    import psycopg
    from gefion.regimes.discovery import runner as drunner, segregation
    from tests.discovery_synth import make_universe
    conn = psycopg.connect(db_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name = 'ifacetest-run'")
    u = make_universe(seed=51, n_days=400, n_features=3)
    market = segregation.MarketData(features=u.features,
                                    forward_returns=u.forward_returns,
                                    dataset_version="synth-test")
    cfg = drunner.DiscoveryConfig(
        name="ifacetest-run", seed=51,
        atoms=[{"feature": "noise_1", "cmp": ">", "value": 0.0}],
        signals=["noise_0"], depth=1, budget=10, tiers=("grammar",),
        holdout_weeks=13, min_effective_n=3, universe_filter="passthrough")
    summary = drunner.run_discovery(conn, cfg, market)
    yield summary
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name = 'ifacetest-run'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-ifacetest-run-%'")
    conn.close()


def test_discover_ledger_shows_losers(db_url, completed_run):
    import json
    result = runner.invoke(
        app, ["regime", "discover", "ledger", "ifacetest-run", "--db-url", db_url, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["data"]
    rows = payload["candidates"]
    assert rows, "losers must be visible in the ledger"
    assert all(r["verdict"] is not None for r in rows)
    # verdict filter works
    result = runner.invoke(
        app, ["regime", "discover", "ledger", "ifacetest-run",
              "--verdict", "admitted", "--db-url", db_url, "--json"])
    assert result.exit_code == 0
    filtered = json.loads(result.output)["data"]["candidates"]
    assert all(r["verdict"] == "admitted" for r in filtered)


def test_discover_verdicts_shows_family_size_beside_survivors(db_url, completed_run):
    import json
    result = runner.invoke(
        app, ["regime", "discover", "verdicts", "ifacetest-run", "--db-url", db_url, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    data = payload["data"] if "data" in payload else payload
    assert "family_size" in data       # survivors are never shown without it
    assert "admitted" in data
    assert isinstance(data["admitted"], list)
