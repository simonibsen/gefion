"""The budget gate (010, T018/T019 — US4).

TDD: written FIRST. Raising per-cycle budget above V1_MAX_BUDGET or grammar
depth above V1_MAX_DEPTH requires that the 2 most recent completed runs on the
same dataset version each carry a PASSING latest SPA re-verdict. Refusals name
the gate and the satisfying command; satisfaction is recorded in the new run's
pre-registration; within-cap starts are unaffected.
"""
import os
import sys

import psycopg
import pytest

from gefion.db import schema

sys.path.insert(0, os.path.dirname(__file__))
from discovery_synth import make_universe  # noqa: E402


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
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'spagate%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'spagate%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-spagate%'")
    c.close()


def _completed_run(conn, name, dataset_version="synth-gate", admitted=0,
                   family_size=3):
    from gefion.regimes.discovery import ledger
    run_id = ledger.create_run(
        conn, name=name, seed=3,
        search_space={"signal_source": "features", "grading_scheme": "walk_forward",
                      "universe_filter": ["passthrough"], "atoms": [],
                      "signals": ["x"], "horizon_days": 1, "fdr_rate": 0.01,
                      "label_window": 60, "align_window": 60},
        segregation={"inner_start": "2024-01-01", "inner_end": "2024-06-01",
                     "holdout_start": "2024-06-02", "holdout_end": "2024-09-01"},
        dataset_version=dataset_version)
    cand_ids = []
    if admitted:
        cand_ids = ledger.record_candidates(conn, run_id, [
            {"candidate_hash": f"{name}-c{i}", "tier": "interaction",
             "expression": {"kind": "interaction", "signal": "x",
                            "conditioning": "y"},
             "provenance": {"atom_features": ["y"]}}
            for i in range(admitted)])
    ledger.set_status(conn, run_id, "enumerated")
    for cid in cand_ids:
        ledger.record_result(conn, cid, {"tests": []}, "admitted")
    for status in ("evaluated", "complete"):
        ledger.set_status(conn, run_id, status)
    ledger.set_family_size(conn, run_id, family_size)
    return run_id


def _reverdict(conn, run_id, p):
    from gefion.regimes.discovery.ledger import record_spa_reverdict
    return record_spa_reverdict(conn, run_id, {
        "p_consistent": p, "p_lower": p / 2, "p_upper": min(1.0, p * 1.5),
        "level": 0.01, "passed": p <= 0.01, "iterations": 200, "seed": 3,
        "block_length": 4.0, "family_size": 3,
        "verification": {"units": 3, "max_abs_divergence": 0.0,
                         "all_match": True}})
    # NB: passed means SUPPORTED (research R9): p <= level


def _config(budget, depth, name="spagate-new"):
    from gefion.regimes.discovery import runner
    return runner.DiscoveryConfig(
        name=name, seed=9, atoms=[{"feature": "noise_0", "form": "tercile"}],
        signals=["noise_1"], depth=depth, budget=budget,
        tiers=("interaction",), universe_filter="passthrough",
        holdout_weeks=6)


def test_v1_caps_are_named_constants():
    from gefion.regimes.discovery import runner
    assert runner.V1_MAX_BUDGET == 200
    assert runner.V1_MAX_DEPTH == 2


def test_within_cap_is_unaffected(conn):
    from gefion.regimes.discovery import runner
    # at the caps exactly, no completed runs, no reverdicts: no gate at all
    gate = runner.check_budget_gate(
        conn, _config(budget=runner.V1_MAX_BUDGET, depth=runner.V1_MAX_DEPTH),
        "synth-gate")
    assert gate is None


def test_above_cap_refused_without_coherent_spa(conn):
    from gefion.regimes.discovery import runner
    cfg = _config(budget=runner.V1_MAX_BUDGET + 1, depth=1)
    # (a) no completed runs at all
    with pytest.raises(runner.DiscoveryError) as exc:
        runner.check_budget_gate(conn, cfg, "synth-gate")
    msg = str(exc.value)
    assert "gate" in msg.lower()
    assert "regime discover spa" in msg          # names the satisfying command
    # (b) two completed runs, neither SPA-checked: machinery not demonstrated
    r1 = _completed_run(conn, "spagate-a")
    r2 = _completed_run(conn, "spagate-b", admitted=2)
    with pytest.raises(runner.DiscoveryError):
        runner.check_budget_gate(conn, cfg, "synth-gate")
    # (c) INCOHERENT: a run with admissions whose latest SPA is unsupported --
    # BH admitted what SPA cannot distinguish from search luck (R9)
    _reverdict(conn, r1, p=0.40)                 # 0 admissions: coherent
    _reverdict(conn, r2, p=0.30)                 # 2 admissions, unsupported
    with pytest.raises(runner.DiscoveryError) as exc:
        runner.check_budget_gate(conn, cfg, "synth-gate")
    assert "admission" in str(exc.value).lower()
    # depth alone also trips the gate
    with pytest.raises(runner.DiscoveryError):
        runner.check_budget_gate(
            conn, _config(budget=10, depth=runner.V1_MAX_DEPTH + 1), "synth-gate")


def test_latest_reverdict_governs(conn):
    from gefion.regimes.discovery import runner
    r1 = _completed_run(conn, "spagate-a")
    r2 = _completed_run(conn, "spagate-b", admitted=1)
    _reverdict(conn, r1, p=0.40)
    _reverdict(conn, r2, p=0.004)                # older: supported
    _reverdict(conn, r2, p=0.30)                 # latest: unsupported — governs
    with pytest.raises(runner.DiscoveryError):
        runner.check_budget_gate(
            conn, _config(budget=runner.V1_MAX_BUDGET + 1, depth=1), "synth-gate")


def test_above_cap_accepted_when_coherent(conn):
    """Coherence (R9): an all-reject run with large p AND an admitting run
    whose family SPA supports -- both coherent, gate opens and records."""
    from gefion.regimes.discovery import runner
    r1 = _completed_run(conn, "spagate-a")                   # 0 admissions
    r2 = _completed_run(conn, "spagate-b", admitted=2)       # supported below
    v1 = _reverdict(conn, r1, p=0.40)    # unsupported but 0 admissions: coherent
    v2 = _reverdict(conn, r2, p=0.004)   # supported: coherent
    gate = runner.check_budget_gate(
        conn, _config(budget=runner.V1_MAX_BUDGET + 1, depth=1), "synth-gate")
    assert gate["gate"] == "spa"
    assert sorted(gate["runs"]) == sorted([r1, r2])
    assert sorted(gate["reverdict_ids"]) == sorted([v1, v2])


def test_family_zero_runs_are_skipped_for_the_gate(conn):
    """A family-0 run can never carry a re-verdict (nothing to test) -- it is
    skipped when selecting the relevant prior runs, not a permanent blocker."""
    from gefion.regimes.discovery import runner
    r1 = _completed_run(conn, "spagate-a")
    r2 = _completed_run(conn, "spagate-b", admitted=1)
    _reverdict(conn, r1, p=0.40)
    _reverdict(conn, r2, p=0.004)
    _completed_run(conn, "spagate-empty", family_size=0)     # most recent
    gate = runner.check_budget_gate(
        conn, _config(budget=runner.V1_MAX_BUDGET + 1, depth=1), "synth-gate")
    assert sorted(gate["runs"]) == sorted([r1, r2])


def test_gate_scoped_to_dataset_version(conn):
    from gefion.regimes.discovery import runner
    # two coherent runs — on a DIFFERENT dataset version
    for name in ("spagate-x", "spagate-y"):
        rid = _completed_run(conn, name, dataset_version="synth-other")
        _reverdict(conn, rid, p=0.40)
    with pytest.raises(runner.DiscoveryError):
        runner.check_budget_gate(
            conn, _config(budget=runner.V1_MAX_BUDGET + 1, depth=1), "synth-gate")


def test_run_discovery_threads_gate_into_preregistration(conn):
    """Integration: an above-cap run only starts through the gate, and the
    satisfaction is recorded in the new run's search_space (FR-1010)."""
    from gefion.regimes.discovery import ledger, runner, segregation
    u = make_universe(seed=5, n_days=500, n_features=3, feature_prefix="noise")
    market = segregation.MarketData(features=u.features,
                                    forward_returns=u.forward_returns,
                                    dataset_version="synth-gate")
    cfg = _config(budget=runner.V1_MAX_BUDGET + 1, depth=1, name="spagate-run")
    # without satisfaction: refused, and no run row is created
    with pytest.raises(runner.DiscoveryError):
        runner.run_discovery(conn, cfg, market)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_discovery_runs WHERE name = %s",
                    ("spagate-run",))
        assert cur.fetchone()[0] == 0
    # with satisfaction: starts, and the gate is in the pre-registration
    for name in ("spagate-a", "spagate-b"):
        rid = _completed_run(conn, name)
        _reverdict(conn, rid, p=0.40)
    summary = runner.run_discovery(conn, cfg, market)
    run = ledger.get_run(conn, summary["run_id"])
    gate = run["search_space"]["gate"]
    assert gate["gate"] == "spa"
    assert len(gate["runs"]) == 2 and len(gate["reverdict_ids"]) == 2
