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
    """Refusals never enter the family; outer-evaluated candidates always do;
    an inner-screen rejection (no outer test spent) may opt out — but
    admitted/refused flags can never be overridden."""
    run_id = _run(conn)
    ids = ledger.record_candidates(conn, run_id, CANDS + [
        {"candidate_hash": "ccc3", "expression": {"leaf": "comparison"},
         "tier": "grammar", "provenance": None},
        {"candidate_hash": "ddd4", "expression": {"leaf": "comparison"},
         "tier": "grammar", "provenance": None},
    ])
    ledger.set_status(conn, run_id, "enumerated")
    ledger.record_result(conn, ids[0], results={}, verdict="rejected")
    ledger.record_result(conn, ids[1], results={}, verdict="refused_degenerate")
    # inner-screen rejection: discovery chose not to spend the holdout on it
    ledger.record_result(conn, ids[2], results={"selected": False},
                         verdict="rejected", in_family=False)
    ledger.record_result(conn, ids[3], results={}, verdict="admitted")
    rows = {r["candidate_hash"]: r for r in ledger.list_candidates(conn, run_id)}
    assert rows["aaa1"]["counted_in_family"] is True
    assert rows["bbb2"]["counted_in_family"] is False
    assert rows["ccc3"]["counted_in_family"] is False
    assert rows["ddd4"]["counted_in_family"] is True
    # forced flags cannot be overridden
    with pytest.raises(ledger.LedgerError):
        ledger.record_result(conn, ids[3], results={}, verdict="admitted", in_family=False)
    with pytest.raises(ledger.LedgerError):
        ledger.record_result(conn, ids[1], results={}, verdict="refused_degenerate",
                             in_family=True)


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


# =============================================================================
# T015 — minimal runner path: pre-register → enumerate → freeze → evaluate
# (tier 1) → FDR → ledger. Written FIRST (RED) against runner.run_discovery.
# =============================================================================

from gefion.regimes.discovery import runner, segregation  # noqa: E402
from tests.discovery_synth import make_universe, plant_regime_edge  # noqa: E402


def _market_from(u):
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-test")


def _config(**overrides):
    base = dict(
        name="ledgertest-runner",
        seed=42,
        atoms=[{"feature": "noise_1", "form": "tercile"},
               {"feature": "noise_2", "cmp": ">", "value": 0.0}],
        depth=1,
        budget=10,
        tiers=("interaction",),
        signals=["noise_0"],
        holdout_weeks=13,
        universe_filter="passthrough",
    )
    base.update(overrides)
    return runner.DiscoveryConfig(**base)


def test_runner_end_to_end_on_noise(conn):
    u = make_universe(seed=42, n_days=400, n_features=4)
    summary = runner.run_discovery(conn, _config(), _market_from(u))

    run = ledger.get_run(conn, summary["run_id"])
    assert run["status"] == "complete"
    # pre-registration carries the full declared search space
    space = run["search_space"]
    assert space["signal_source"] == "features"
    assert space["grading_scheme"] == "walk_forward"
    assert space["universe_filter"] == ["passthrough"]
    assert space["tiers"] == ["interaction"]
    assert space["depth"] == 1 and space["budget"] == 10
    assert space["fdr_rate"] == runner.DISCOVERY_FDR_RATE
    assert space["inner_screen"] == runner.INNER_SCREEN_PVALUE
    assert len(space["atoms"]) == 2
    # segregation boundaries recorded
    assert run["segregation"]["holdout_start"] > run["segregation"]["inner_end"]

    # every candidate persisted with results (inner screen + selection) and a verdict
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert len(cands) == 2 and all(c["tier"] == "interaction" for c in cands)
    for c in cands:
        assert c["verdict"] is not None
        assert "inner" in c["results"] and "selected" in c["results"]

    # family = outer tests actually spent (selected candidates only)
    outer_pvalued = sum(1 for c in cands for t in c["results"]["tests"]
                        if t["pvalue"] is not None)
    assert run["family_size"] == summary["family_size"] == outer_pvalued
    assert summary["n_selected"] == sum(1 for c in cands if c["results"]["selected"])
    assert summary["n_admitted"] == 0  # pure noise


def test_runner_recovers_planted_interaction(conn):
    # seed 8 has clean decoy separation; the recovery RATE across many seeds
    # (where a borderline decoy may legitimately pass BH) is SC-102's job in
    # test_discovery_negative_control.py — this test checks the plumbing.
    u = plant_regime_edge(make_universe(seed=8, n_days=400, n_features=4), "noise_0")
    cfg = _config(name="ledgertest-planted",
                  atoms=[{"feature": "planted_cond", "form": "tercile"},
                         {"feature": "noise_1", "form": "tercile"},
                         {"feature": "noise_2", "cmp": ">", "value": 0.0}])
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    admitted = ledger.list_candidates(conn, summary["run_id"], verdict="admitted")
    assert len(admitted) == 1
    assert admitted[0]["provenance"]["atom_features"] == ["planted_cond"]
    # the admitted edge is an ordinary machine-origin regime definition
    from gefion.regimes.definitions import load_definition
    name = summary["admitted"][0]["definition"]
    defn = load_definition(conn, name)
    assert defn is not None and defn.origin == "machine"
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_definitions WHERE name = %s", (name,))


def test_runner_budget_truncation_is_recorded(conn):
    u = make_universe(seed=8, n_days=400, n_features=4)
    cfg = _config(name="ledgertest-budget", budget=1)
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert len(cands) == 1
    diags = ledger.list_diagnostics(conn, summary["run_id"], sample_dependent=False)
    kinds = {d["kind"] for d in diags}
    assert "budget_exhausted" in kinds
    detail = next(d for d in diags if d["kind"] == "budget_exhausted")["detail"]
    assert detail["enumerated"] == 2 and detail["budget"] == 1


def test_runner_rejects_uncomputable_atoms_at_proposal(conn):
    u = make_universe(seed=9, n_days=400, n_features=2)
    cfg = _config(name="ledgertest-uncomputable",
                  atoms=[{"feature": "vix_level", "cmp": ">", "value": 20},
                         {"feature": "noise_1", "form": "tercile"}])
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert len(cands) == 1  # vix atom never attempted
    diags = ledger.list_diagnostics(conn, summary["run_id"])
    unc = [d for d in diags if d["kind"] == "uncomputable_proposal"]
    assert len(unc) == 1 and unc[0]["sample_dependent"] is False
    assert unc[0]["detail"]["feature"] == "vix_level"


def test_runner_rejects_entangled_atoms(conn):
    """A regime conditioned on the target signal itself is maximally suspect —
    v1 rejects it at proposal time (FR-114)."""
    u = make_universe(seed=10, n_days=400, n_features=2)
    cfg = _config(name="ledgertest-entangled",
                  atoms=[{"feature": "noise_0", "form": "tercile"},  # == the signal
                         {"feature": "noise_1", "form": "tercile"}])
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert len(cands) == 1
    diags = ledger.list_diagnostics(conn, summary["run_id"])
    assert any(d["kind"] == "entangled" for d in diags)


# --- US2 (T021/T022): tier-2 through the runner; the family counts the losers


def test_runner_tier2_family_counts_every_bucket_test(conn):
    """N grammar candidates x S signals x B buckets — recorded family equals
    the p-valued evaluations; refusals recorded but never counted (SC-103)."""
    u = make_universe(seed=31, n_days=500, n_features=5)
    cfg = _config(
        name="ledgertest-t2-family",
        atoms=[{"feature": "noise_1", "cmp": ">", "value": 0.0},
               {"feature": "noise_2", "cmp": ">", "value": 0.0}],
        depth=2, tiers=("grammar",), signals=["noise_0"],
        holdout_weeks=26, min_effective_n=3)
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    run = ledger.get_run(conn, summary["run_id"])
    cands = ledger.list_candidates(conn, summary["run_id"])
    # 2 singles + AND + OR = exact enumeration
    assert len(cands) == 4 and all(c["tier"] == "grammar" for c in cands)

    all_tests = [t for c in cands for t in c["results"]["tests"]]
    pvalued = [t for t in all_tests if t["pvalue"] is not None]
    assert run["family_size"] == len(pvalued)
    # every candidate carries its inner-screen evidence (2 buckets per
    # boolean candidate), selected or not — nothing dropped
    for c in cands:
        assert len(c["results"]["inner"]) >= 2
        if not c["results"]["selected"]:
            assert c["results"]["tests"] == []       # no outer test spent
            assert c["counted_in_family"] is False   # and none counted
    # zero survivors on pure noise for this seed; losers all persisted
    assert summary["n_admitted"] == 0
    assert all(c["verdict"] in ("rejected", "refused_low_power") for c in cands)


def test_runner_tier2_recovers_planted_regime_and_rejects_decoys(conn):
    u = plant_regime_edge(
        make_universe(seed=32, n_days=500, n_features=4), "noise_0",
        episode_len=10, cancel=True)
    cfg = _config(
        name="ledgertest-t2-planted",
        atoms=[{"feature": "planted_cond", "cmp": ">", "value": 0.0},
               {"feature": "noise_1", "cmp": ">", "value": 0.0},
               {"feature": "noise_2", "cmp": ">", "value": 0.0}],
        depth=1, tiers=("grammar",), signals=["noise_0"],
        holdout_weeks=26, min_effective_n=5)
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    admitted = ledger.list_candidates(conn, summary["run_id"], verdict="admitted")
    assert len(admitted) == 1
    assert admitted[0]["provenance"]["atom_features"] == ["planted_cond"]
    # the surviving test is the in-regime bucket
    surviving = [t for t in admitted[0]["results"]["tests"] if t.get("survived")]
    assert surviving and all(t["bucket"] == "true" for t in surviving)
    for row in summary["admitted"]:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM regime_definitions WHERE name = %s",
                        (row["definition"],))


def test_runner_verdicts_derive_only_from_the_recorded_family(conn):
    """Post-hoc cherry-picking is impossible: every test in every candidate's
    results carries the family's fdr_rate and a survived flag; family_size on
    the run equals the p-valued tests across ALL candidates (T022)."""
    u = make_universe(seed=33, n_days=500, n_features=4)
    cfg = _config(name="ledgertest-t2-audit",
                  atoms=[{"feature": "noise_1", "form": "tercile"},
                         {"feature": "noise_2", "cmp": ">", "value": 0.0}],
                  depth=1, tiers=("interaction", "grammar"),
                  signals=["noise_0", "noise_3"],
                  holdout_weeks=26, min_effective_n=3)
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    run = ledger.get_run(conn, summary["run_id"])
    cands = ledger.list_candidates(conn, summary["run_id"])
    # interaction and grammar hypotheses ledger separately even for one atom
    assert {c["tier"] for c in cands} == {"interaction", "grammar"}
    total_pvalued = sum(1 for c in cands for t in c["results"]["tests"]
                        if t["pvalue"] is not None)
    assert run["family_size"] == total_pvalued
    for c in cands:
        assert c["results"]["fdr_rate"] == cfg.fdr_rate
        for t in c["results"]["tests"]:
            assert "survived" in t


# --- US3 (T033): expressive tier — reserve-gated freeform + detectors


FREEFORM_PLANTED = {"leaf": "comparison", "feature": "planted_cond",
                    "cmp": ">", "value": 0.0, "scope": "market"}

DETECTOR_CODE = '''
import numpy as np

def fit(series, seed=0):
    values = [v for _, v in series]
    return {"cut": float(np.median(values))}

def label(series, params):
    return [(d, "high" if v > params["cut"] else "low") for d, v in series]
'''


def _expressive_cfg(u, name, justification=None):
    return runner.DiscoveryConfig(
        name=name, seed=8,
        atoms=[{"feature": "noise_1", "cmp": ">", "value": 0.0}],
        signals=["noise_0"], depth=1, budget=20, tiers=("expressive",),
        holdout_weeks=13, min_effective_n=3, universe_filter="passthrough",
        fresh_holdout=(u.dates[300], u.dates[380]),
        freeform=[FREEFORM_PLANTED],
        detectors=[{"name": "det-planted", "code": DETECTOR_CODE,
                    "feature": "planted_cond",
                    "provenance": {"principle_id": "regime-detection-hmm"}}],
        reserve_justification=justification,
    )


def test_expressive_tier_requires_a_reserve(conn):
    u = make_universe(seed=8, n_days=500, n_features=2)
    cfg = _expressive_cfg(u, "ledgertest-exp-noreserve")
    cfg.fresh_holdout = None
    with pytest.raises(runner.DiscoveryError):
        runner.run_discovery(conn, cfg, _market_from(u))


def test_expressive_run_consumes_reserve_and_ledgers_everything(conn):
    u = plant_regime_edge(make_universe(seed=8, n_days=500, n_features=3),
                          "noise_0", episode_len=10, cancel=True)
    cfg = _expressive_cfg(u, "ledgertest-exp")
    summary = runner.run_discovery(conn, cfg, _market_from(u))
    run = ledger.get_run(conn, summary["run_id"])
    assert run["reserve_consumed"] is True
    assert run["segregation"]["reserve"]["start"] == str(u.dates[300])
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert all(c["tier"] == "expressive" for c in cands)
    assert len(cands) == 2  # freeform + detector
    # the planted freeform expression is admitted on the reserve block
    admitted = [c for c in cands if c["verdict"] == "admitted"]
    assert admitted, "planted conditional edge should be admitted on the reserve"
    # detector candidates carry fitted params in provenance (T3 accounting)
    det = next(c for c in cands if c["provenance"].get("detector"))
    assert "fitted_params" in det["provenance"]
    for row in summary["admitted"]:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM regime_definitions WHERE name = %s",
                        (row["definition"],))


def test_consumed_reserve_refused_without_justification(conn):
    u = plant_regime_edge(make_universe(seed=8, n_days=500, n_features=3),
                          "noise_0", episode_len=10, cancel=True)
    from gefion.regimes.discovery.freshhold import ReserveError
    summary = runner.run_discovery(conn, _expressive_cfg(u, "ledgertest-exp-a"),
                                   _market_from(u))
    with pytest.raises(ReserveError):
        runner.run_discovery(conn, _expressive_cfg(u, "ledgertest-exp-b"),
                             _market_from(u))
    # …but an explicit, recorded justification re-opens it
    summary2 = runner.run_discovery(
        conn, _expressive_cfg(u, "ledgertest-exp-c",
                              justification="synthetic test rerun"),
        _market_from(u))
    run2 = ledger.get_run(conn, summary2["run_id"])
    assert run2["segregation"]["reserve"]["justification"] == "synthetic test rerun"
    for s in (summary, summary2):
        for row in s["admitted"]:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM regime_definitions WHERE name = %s",
                            (row["definition"],))


# --- US5 (T040): byte-reproducibility ----------------------------------------


def test_rerun_with_identical_inputs_is_identical(conn):
    """SC-105: same seed + same inputs -> identical candidate hashes, results,
    and verdicts, across both tiers and the whole ledger."""
    def snapshot(name):
        u = plant_regime_edge(make_universe(seed=17, n_days=500, n_features=4),
                              "noise_0", episode_len=10, cancel=True)
        cfg = _config(name=name, seed=17,
                      atoms=[{"feature": "planted_cond", "cmp": ">", "value": 0.0},
                             {"feature": "noise_1", "cmp": ">", "value": 0.0},
                             {"feature": "noise_2", "form": "tercile"}],
                      depth=2, tiers=("interaction", "grammar"),
                      signals=["noise_0", "noise_3"],
                      holdout_weeks=26, min_effective_n=5)
        summary = runner.run_discovery(conn, cfg, _market_from(u))
        run = ledger.get_run(conn, summary["run_id"])
        cands = ledger.list_candidates(conn, summary["run_id"])
        diags = ledger.list_diagnostics(conn, summary["run_id"])
        for row in summary["admitted"]:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM regime_definitions WHERE name = %s",
                            (row["definition"],))
        return {
            "family_size": run["family_size"],
            "search_space": run["search_space"],
            "candidates": [(c["candidate_hash"], c["tier"], c["verdict"],
                            c["counted_in_family"], c["results"]) for c in cands],
            "diagnostics": [(d["kind"], d["sample_dependent"], d["detail"])
                            for d in diags],
        }

    a = snapshot("ledgertest-repro-a")
    b = snapshot("ledgertest-repro-b")
    # the search space differs only by nothing — names are outside it
    assert a == b


def test_runner_refuses_all_holdout_data(conn):
    """US1 acceptance 3: a run that cannot prove segregation produces no
    verdicts — it is recorded and marked invalid."""
    u = make_universe(seed=11, n_days=20, n_features=2)
    with pytest.raises(segregation.SegregationError):
        runner.run_discovery(conn, _config(name="ledgertest-invalid"),
                             _market_from(u))
    run = ledger.get_run(conn, "ledgertest-invalid")
    assert run["status"] == "invalid"
    assert ledger.list_candidates(conn, run["id"]) == []
