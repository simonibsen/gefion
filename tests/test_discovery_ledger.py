"""Ledger tests for agentic regime discovery (006, T009).

TDD: written FIRST. The ledger IS the honesty mechanism: pre-registration
must declare the three pluggable seams before anything runs, the status
lifecycle enforces candidate-freeze-before-evaluation (the T4 guard), every
candidate — losers included — is persisted, and counted_in_family invariants
make silent survivorship impossible (FR-104/105/106).
"""
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import ledger


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
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'ledgertest-%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'ledgertest-%'")
    c.close()


SEARCH_SPACE = {
    "atoms": [{"feature": "noise_0", "cmp": ">", "value": 0.0}],
    "depth": 1,
    "budget": 10,
    "tiers": ["interaction", "grammar"],
    "signal_source": "features",
    "grading_scheme": "walk_forward",
    "universe_filter": ["test_tickers", "asset_type:common"],
}

SEGREGATION = {
    "inner_start": "2020-01-06",
    "inner_end": "2021-06-30",
    "holdout_start": "2021-07-01",
    "holdout_end": "2021-11-19",
}


def _run(conn, name="ledgertest-a"):
    return ledger.create_run(conn, name=name, seed=42, search_space=SEARCH_SPACE,
                             segregation=SEGREGATION, dataset_version="synth-test")


# --- pre-registration --------------------------------------------------------

def test_create_run_pre_registers(conn):
    run_id = _run(conn)
    run = ledger.get_run(conn, run_id)
    assert run["status"] == "pre_registered"
    assert run["seed"] == 42
    assert run["search_space"]["signal_source"] == "features"
    assert run["search_space"]["grading_scheme"] == "walk_forward"
    assert run["search_space"]["universe_filter"] == ["test_tickers", "asset_type:common"]
    assert run["segregation"]["holdout_start"] == "2021-07-01"
    assert run["family_size"] is None


def test_create_run_requires_all_three_seams(conn):
    """A search space missing a declared seam is not a pre-registration."""
    for missing in ("signal_source", "grading_scheme", "universe_filter"):
        space = {k: v for k, v in SEARCH_SPACE.items() if k != missing}
        with pytest.raises(ledger.LedgerError):
            ledger.create_run(conn, name="ledgertest-bad", seed=1, search_space=space,
                              segregation=SEGREGATION, dataset_version="synth-test")


def test_get_run_by_name(conn):
    run_id = _run(conn, "ledgertest-byname")
    assert ledger.get_run(conn, "ledgertest-byname")["id"] == run_id


# --- status lifecycle (candidate freeze = the T4 guard) ----------------------

def test_status_happy_path(conn):
    run_id = _run(conn)
    for status in ("enumerated", "evaluated", "complete"):
        ledger.set_status(conn, run_id, status)
        assert ledger.get_run(conn, run_id)["status"] == status
    assert ledger.get_run(conn, run_id)["completed_at"] is not None


def test_status_cannot_skip_forward(conn):
    run_id = _run(conn)
    with pytest.raises(ledger.LedgerError):
        ledger.set_status(conn, run_id, "evaluated")  # skips enumerated


def test_status_cannot_move_backward(conn):
    run_id = _run(conn)
    ledger.set_status(conn, run_id, "enumerated")
    with pytest.raises(ledger.LedgerError):
        ledger.set_status(conn, run_id, "pre_registered")


def test_any_active_run_can_be_invalidated(conn):
    for prior in ("pre_registered", "enumerated", "evaluated"):
        run_id = _run(conn, f"ledgertest-inv-{prior}")
        if prior != "pre_registered":
            ledger.set_status(conn, run_id, "enumerated")
        if prior == "evaluated":
            ledger.set_status(conn, run_id, "evaluated")
        ledger.set_status(conn, run_id, "invalid")
        assert ledger.get_run(conn, run_id)["status"] == "invalid"


def test_complete_is_terminal(conn):
    run_id = _run(conn)
    for status in ("enumerated", "evaluated", "complete"):
        ledger.set_status(conn, run_id, status)
    with pytest.raises(ledger.LedgerError):
        ledger.set_status(conn, run_id, "invalid")


# --- candidate persistence (losers included) ---------------------------------

CANDS = [
    {"candidate_hash": "aaa1", "expression": {"leaf": "comparison"}, "tier": "grammar",
     "provenance": {"atoms": ["noise_0"]}},
    {"candidate_hash": "bbb2", "expression": {"op": "AND"}, "tier": "grammar",
     "provenance": None},
]


def test_record_candidates_only_before_freeze(conn):
    run_id = _run(conn)
    ids = ledger.record_candidates(conn, run_id, CANDS)
    assert len(ids) == 2
    ledger.set_status(conn, run_id, "enumerated")
    with pytest.raises(ledger.LedgerError):
        ledger.record_candidates(conn, run_id, [dict(CANDS[0], candidate_hash="ccc3")])


def test_results_only_after_freeze(conn):
    """No evaluation before the candidate set is frozen — selection-after-peek
    is impossible at the API level."""
    run_id = _run(conn)
    (cand_id, _) = ledger.record_candidates(conn, run_id, CANDS)
    with pytest.raises(ledger.LedgerError):
        ledger.record_result(conn, cand_id, results={"p": 0.5}, verdict="rejected")


def test_losers_are_persisted_and_visible(conn):
    run_id = _run(conn)
    ids = ledger.record_candidates(conn, run_id, CANDS)
    ledger.set_status(conn, run_id, "enumerated")
    ledger.record_result(conn, ids[0], results={"tests": [{"p": 0.9}]}, verdict="rejected")
    ledger.record_result(conn, ids[1], results={"reason": "low power"},
                         verdict="refused_low_power")
    rows = ledger.list_candidates(conn, run_id)
    assert {r["verdict"] for r in rows} == {"rejected", "refused_low_power"}
    losers = ledger.list_candidates(conn, run_id, verdict="refused_low_power")
    assert len(losers) == 1 and losers[0]["candidate_hash"] == "bbb2"


def test_counted_in_family_invariants(conn):
    """Refusals never enter the family; evaluated candidates always do."""
    run_id = _run(conn)
    ids = ledger.record_candidates(conn, run_id, CANDS)
    ledger.set_status(conn, run_id, "enumerated")
    ledger.record_result(conn, ids[0], results={}, verdict="rejected")
    ledger.record_result(conn, ids[1], results={}, verdict="refused_degenerate")
    rows = {r["candidate_hash"]: r for r in ledger.list_candidates(conn, run_id)}
    assert rows["aaa1"]["counted_in_family"] is True
    assert rows["bbb2"]["counted_in_family"] is False


def test_family_size_recorded(conn):
    run_id = _run(conn)
    ledger.set_family_size(conn, run_id, 37)
    assert ledger.get_run(conn, run_id)["family_size"] == 37


# --- diagnostics ledger -------------------------------------------------------

def test_diagnostics_with_sample_dependent_tagging(conn):
    run_id = _run(conn)
    ledger.record_diagnostic(conn, run_id, kind="min_sample_refusal",
                             detail={"effective_n": 3, "floor": 20},
                             sample_dependent=True)
    ledger.record_diagnostic(conn, run_id, kind="uncomputable_proposal",
                             detail={"feature": "vix_level", "reason": "not ingested"},
                             sample_dependent=False)
    all_rows = ledger.list_diagnostics(conn, run_id)
    assert len(all_rows) == 2
    sd = ledger.list_diagnostics(conn, run_id, sample_dependent=True)
    assert len(sd) == 1 and sd[0]["detail"]["effective_n"] == 3
    structural = ledger.list_diagnostics(conn, run_id, sample_dependent=False)
    assert len(structural) == 1 and structural[0]["kind"] == "uncomputable_proposal"
    # dataset provenance inherited from the run (FR-125)
    assert all(r["dataset_version"] == "synth-test" for r in all_rows)


def test_list_runs_filters_by_status(conn):
    a = _run(conn, "ledgertest-list-a")
    b = _run(conn, "ledgertest-list-b")
    ledger.set_status(conn, b, "enumerated")
    names = {r["name"] for r in ledger.list_runs(conn, status="pre_registered")
             if r["name"].startswith("ledgertest-list")}
    assert names == {"ledgertest-list-a"}
