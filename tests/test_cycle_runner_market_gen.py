"""Cycle-runner market-scope generation tests (014 T008/T026).

TDD: written FIRST. The market generation path writes ONLY candidates —
never feature_functions — with provenance; the gate is never shortened by
automation. Failure to generate is reported honestly (no empty candidates).
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
    schema.create_market_function_candidates_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mcg_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mcg_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


@pytest.fixture
def runner():
    from gefion.experiments.cycle_runner import CycleRunner
    return CycleRunner(schema.test_db_url())


def test_market_generation_writes_only_candidates(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: None)

    cid = runner.propose_market_candidate(
        "mcg-breadth-participation", "participation confirms trend")

    assert cid is not None
    from gefion.macro import candidates
    c = candidates.get_candidate(conn, cid)
    assert c["review_state"] == "pending"
    assert c["origin"] == "template"          # claude unavailable → fallback
    assert c["generator"] == "cycle_runner"
    assert c["principle_id"] == "mcg-breadth-participation"
    assert c["dry_run"] is not None           # dry-run stored at generation
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_functions WHERE name = %s",
                    (c["name"],))
        assert cur.fetchone()[0] == 0         # NEVER in feature_functions


def test_claude_path_records_claude_origin(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    body = "def compute(rows):\n    return float(len(rows))"
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: body)

    cid = runner.propose_market_candidate("mcg-claude-test", "test design")

    from gefion.macro import candidates
    c = candidates.get_candidate(conn, cid)
    assert c["origin"] == "claude"
    assert c["function_body"] == body


def test_total_generation_failure_is_honest(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: None)
    monkeypatch.setattr(cr, "_generate_market_body_template", lambda *a, **k: None)

    cid = runner.propose_market_candidate("mcg-nothing", "no template matches")

    assert cid is None
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM market_function_candidates "
                    "WHERE principle_id = 'mcg-nothing'")
        assert cur.fetchone()[0] == 0         # no empty candidate rows


def test_cycle_routes_market_scope_hypotheses_to_candidates():
    """Wiring: a hypothesis with scope='market' goes through
    propose_market_candidate, not the per-stock experiment path, and the
    cycle summary carries the candidate id."""
    import inspect
    from gefion.experiments.cycle_runner import CycleRunner

    source = inspect.getsource(CycleRunner.run_cycle)
    assert "propose_market_candidate" in source
    assert '"market"' in source or "'market'" in source


def test_market_templates_emit_market_contract():
    """Template bodies must define compute(rows) (the market contract),
    not the per-stock compute(df, **params)."""
    from gefion.experiments.cycle_runner import _generate_market_body_template

    body = _generate_market_body_template("mcg-breadth-participation")
    assert body is not None
    assert "def compute(rows" in body


# --- T026 (US3): composite-kind generation -----------------------------------------

def _seed_macro_series(conn, names):
    from gefion.macro import catalog
    for n in names:
        catalog.ensure_series(conn, name=n, provider="derived",
                              kind="derived", cadence="daily")


def test_composite_generation_declares_existing_series(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: None)
    _seed_macro_series(conn, ["mcg_in_a", "mcg_in_b"])

    cid = runner.propose_market_candidate(
        "mcg-vol-breadth-interaction", "composite over the pair",
        kind="composite", series=["mcg_in_a", "mcg_in_b"])

    from gefion.macro import candidates
    c = candidates.get_candidate(conn, cid)
    assert c["kind"] == "composite"
    assert c["inputs"] == {"series": ["mcg_in_a", "mcg_in_b"]}
    assert "def compute(row" in c["function_body"]   # composite contract
    assert c["dry_run"]["ok"] is True

    with conn.cursor() as cur:
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'mcg_in_%'")


def test_composite_generation_refuses_unknown_series(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: None)

    cid = runner.propose_market_candidate(
        "mcg-bad-composite", "x", kind="composite",
        series=["mcg_no_such_series"])

    assert cid is None   # honest refusal: only existing series may be declared


def test_composite_generation_requires_series(conn, runner, monkeypatch):
    from gefion.experiments import cycle_runner as cr
    monkeypatch.setattr(cr, "_generate_market_body_claude", lambda *a, **k: None)

    assert runner.propose_market_candidate(
        "mcg-no-inputs", "x", kind="composite") is None
