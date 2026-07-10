"""SPA re-verdict recording + command surfaces (010, T012/T014/T015 — US1).

TDD: written FIRST. The approved spa_reverdicts table (append-only, cascades
with its run — derived analysis, not audit), the ledger API, and the CLI/MCP
surfaces. A recorded row always implies verification passed.
"""
import os

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'sparev%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'sparev%'")
    c.close()


def _make_run(conn, name="sparev-run"):
    from gefion.regimes.discovery import ledger
    run_id = ledger.create_run(
        conn, name=name, seed=7,
        search_space={"signal_source": "features", "grading_scheme": "walk_forward",
                      "universe_filter": ["passthrough"], "atoms": [],
                      "signals": ["x"], "horizon_days": 1, "fdr_rate": 0.01,
                      "label_window": 60, "align_window": 60},
        segregation={"inner_start": "2024-01-01", "inner_end": "2024-06-01",
                     "holdout_start": "2024-06-02", "holdout_end": "2024-09-01"},
        dataset_version="dev")
    ledger.set_family_size(conn, run_id, 3)
    return run_id


def _result(p=0.2):
    return {"p_consistent": p, "p_lower": p / 2, "p_upper": min(1.0, p * 1.5),
            "statistic": 1.7, "family_size": 3, "iterations": 200, "seed": 7,
            "block_length": 4.5, "level": 0.01, "passed": p > 0.01,
            "verification": {"units": 3, "max_abs_divergence": 1e-12,
                             "all_match": True}}


# --- schema (T012) ------------------------------------------------------------------

def test_spa_reverdicts_table_shape(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name, is_nullable FROM information_schema.columns
               WHERE table_name = 'spa_reverdicts'""")
        cols = dict(cur.fetchall())
    for col in ("run_id", "p_consistent", "p_lower", "p_upper", "level",
                "passed", "iterations", "seed", "block_length", "family_size",
                "verification"):
        assert col in cols, f"missing column {col}"
        assert cols[col] == "NO", f"{col} must be NOT NULL"
    with conn.cursor() as cur:
        cur.execute("""SELECT indexname FROM pg_indexes
                       WHERE tablename = 'spa_reverdicts'""")
        assert "spa_reverdicts_run_created_idx" in {r[0] for r in cur.fetchall()}


def test_spa_reverdicts_cascade_with_run(conn):
    from gefion.regimes.discovery import ledger as dledger
    run_id = _make_run(conn, "sparev-cascade")
    from gefion.regimes.discovery.ledger import record_spa_reverdict
    record_spa_reverdict(conn, run_id, _result())
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE id = %s", (run_id,))
        cur.execute("SELECT count(*) FROM spa_reverdicts WHERE run_id = %s",
                    (run_id,))
        assert cur.fetchone()[0] == 0            # derived analysis cascades


# --- ledger API (T014) --------------------------------------------------------------

def test_recording_is_append_only(conn):
    from gefion.regimes.discovery.ledger import (list_spa_reverdicts,
                                                 latest_spa_reverdict,
                                                 record_spa_reverdict)
    run_id = _make_run(conn)
    record_spa_reverdict(conn, run_id, _result(p=0.30))
    record_spa_reverdict(conn, run_id, _result(p=0.005))     # re-run, new seed
    rows = list_spa_reverdicts(conn, run_id)
    assert len(rows) == 2                        # appended, nothing overwritten
    latest = latest_spa_reverdict(conn, run_id)
    assert latest["p_consistent"] == 0.005
    assert latest["passed"] is False             # 0.005 <= 0.01
    assert latest["verification"]["all_match"] is True


def test_latest_is_none_when_never_run(conn):
    from gefion.regimes.discovery.ledger import latest_spa_reverdict
    run_id = _make_run(conn, "sparev-never")
    assert latest_spa_reverdict(conn, run_id) is None


# --- surfaces (T015) ----------------------------------------------------------------

def test_cli_spa_command_exists_with_options():
    from typer.testing import CliRunner
    from gefion.cli import app
    result = CliRunner().invoke(app, ["regime", "discover", "spa", "--help"])
    assert result.exit_code == 0
    for opt in ("--iterations", "--seed", "--level", "--block-length"):
        assert opt in result.output


def test_mcp_spa_tool_exists():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    assert 'name="regime_discover_spa"' in server
    assert 'name == "regime_discover_spa"' in server
    assert "async def _regime_discover_spa(" in server
