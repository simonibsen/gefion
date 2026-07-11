"""The horizon is part of the claim (owner, 2026-07-11).

TDD: written FIRST. Every discovery verdict is horizon-specific — "edge at 1
day out" and "edge at 20 days out" are different findings. The verdicts
surface states the horizon, and an admitted regime's descriptive_metadata
carries it into the landed artifact.
"""
import os
import pathlib

import psycopg
import pytest

from gefion.db import schema


def test_verdicts_output_states_horizon():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.regimes.discovery import ledger
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'horiz-%'")
    run_id = ledger.create_run(
        conn, name="horiz-run", seed=1,
        search_space={"signal_source": "features", "grading_scheme": "walk_forward",
                      "universe_filter": ["passthrough"], "atoms": [],
                      "signals": ["x"], "horizon_days": 20, "fdr_rate": 0.01,
                      "label_window": 60, "align_window": 60},
        segregation={"inner_start": "2024-01-01", "inner_end": "2024-06-01",
                     "holdout_start": "2024-06-02", "holdout_end": "2024-09-01"},
        dataset_version="dev")
    r = CliRunner().invoke(app, ["regime", "discover", "verdicts", str(run_id),
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    assert "20-day horizon" in r.output          # the claim states its window
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'horiz-%'")
    conn.close()


def test_admitted_definitions_carry_horizon():
    """Both register paths write horizon_days into descriptive_metadata."""
    src = (pathlib.Path(__file__).parent.parent / "src" / "gefion" / "regimes"
           / "discovery" / "runner.py").read_text()
    assert src.count('"horizon_days": config.horizon_days') >= 3, (
        "search_space + both admitted-definition metadata blocks must carry "
        "the horizon")
