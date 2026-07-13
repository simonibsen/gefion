"""Walk-forward trust-grading tests (006, T035 — US6).

TDD: written FIRST. Trust ACCRUES from forward-in-time confirmations only:
the probation window is fold 1, each re-test appends a regime_trust_grades
row, backward era-slices are stored descriptive-only and structurally cannot
enter the grade, and an edge that fails early folds is flagged regime-limited
(captured, but never trusted as durable).
"""
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import grading, ledger, runner, segregation
from discovery_synth import make_universe, plant_regime_edge, truncate_universe


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
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'gradetest-%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-gradetest-%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'gradetest-%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-gradetest-%'")
    c.close()


def _market_from(u):
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-test")


def _admitted_run(conn, name, effect_through=None, seed=8, fold_length_days=150):
    """Discovery on the first 500 days of an 800-day universe; the later 300
    days are the forward folds' future."""
    full = plant_regime_edge(
        make_universe(seed=seed, n_days=800, n_features=3), "noise_0",
        episode_len=10, cancel=True, effect_through=effect_through)
    cfg = runner.DiscoveryConfig(
        name=name, seed=seed,
        atoms=[{"feature": "planted_cond", "cmp": ">", "value": 0.0},
               {"feature": "noise_1", "cmp": ">", "value": 0.0}],
        signals=["noise_0"], depth=1, budget=20, tiers=("grammar",),
        holdout_weeks=26, min_effective_n=3, universe_filter="passthrough",
        # a fold must hold enough 10-day regime episodes to clear the
        # effective-N floor — fold width is declared, not guessed
        fold_length_days=fold_length_days)
    summary = runner.run_discovery(conn, cfg, _market_from(truncate_universe(full, 500)))
    # align the run's execution stamp with its simulated world: these tests
    # manufacture a past holdout, which the vintage guard would otherwise
    # (correctly) treat as operator-seen data and demote folds to descriptive
    with conn.cursor() as cur:
        cur.execute("""UPDATE regime_discovery_runs
                       SET created_at = (segregation->>'holdout_end')::date
                       WHERE id = %s""", (summary["run_id"],))
    admitted = ledger.list_candidates(conn, summary["run_id"], verdict="admitted")
    assert len(admitted) == 1, "grading tests need one admitted edge"
    return full, summary, admitted[0]


# --- the interface is structurally forward-only -------------------------------

def test_interface_has_no_backward_confirmation_api():
    scheme = grading.get_scheme("walk_forward")
    public = {m for m in dir(scheme) if not m.startswith("_")}
    assert public == {"register", "record_forward_result", "record_descriptive",
                      "evaluate_fold", "grade"}, (
        "the GradingScheme surface is a whitelist — nothing may add a backward "
        f"confirmation; found {public}")


def test_unknown_scheme_refused():
    with pytest.raises(grading.GradingError):
        grading.get_scheme("vibes")


# --- registration (probation window = fold 1) ---------------------------------

def test_admitted_edges_are_auto_registered(conn):
    _, _, cand = _admitted_run(conn, "gradetest-reg")
    assert cand["provenance"]["grading"]["scheme"] == "walk_forward"
    assert cand["provenance"]["grading"]["fold_length_days"] > 0


def test_register_refuses_unadmitted_candidates(conn):
    _, summary, _ = _admitted_run(conn, "gradetest-unadm")
    scheme = grading.get_scheme("walk_forward")
    loser = next(c for c in ledger.list_candidates(conn, summary["run_id"])
                 if c["verdict"] != "admitted")
    with pytest.raises(grading.GradingError):
        scheme.register(conn, loser["id"])


# --- grade accrual: forward rows only ------------------------------------------

def test_forward_results_accrue_and_descriptive_never_counts(conn):
    _, _, cand = _admitted_run(conn, "gradetest-accrue")
    scheme = grading.get_scheme("walk_forward")
    scheme.record_forward_result(conn, cand["id"], fold=1, confirmed=True,
                                 detail={"p": 0.01})
    scheme.record_forward_result(conn, cand["id"], fold=2, confirmed=True,
                                 detail={"p": 0.02})
    # a glowing backward era-slice — display context only
    scheme.record_descriptive(conn, cand["id"], fold=1, outcome=True,
                              detail={"era": "2020-crash", "p": 1e-9})
    g = scheme.grade(conn, cand["id"])
    assert g["folds"] == 2                # descriptive row NOT among them
    assert g["confirmed"] == 2
    assert g["grade"] == 1.0
    assert g["regime_limited"] is False
    assert g["descriptive_slices"] == 1   # visible, separate, never graded


def test_early_fold_failure_flags_regime_limited(conn):
    _, _, cand = _admitted_run(conn, "gradetest-limited")
    scheme = grading.get_scheme("walk_forward")
    scheme.record_forward_result(conn, cand["id"], fold=1, confirmed=False,
                                 detail={"reason": "edge gone"})
    g = scheme.grade(conn, cand["id"])
    assert g["regime_limited"] is True
    assert g["grade"] == 0.0


# --- fold evaluation: genuinely-after data only ---------------------------------

def test_evaluate_fold_confirms_a_durable_edge(conn):
    full, _, cand = _admitted_run(conn, "gradetest-durable")
    scheme = grading.get_scheme("walk_forward")
    outcome = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert outcome["confirmed"] is True
    g = scheme.grade(conn, cand["id"])
    assert g["folds"] == 1 and g["confirmed"] == 1


def test_evaluate_fold_fails_a_single_era_edge(conn):
    """SC-109: an edge that existed only in the discovery era passes the hard
    gate but fails its forward folds -> regime-limited."""
    full, _, cand = _admitted_run(conn, "gradetest-oneera", effect_through=500)
    scheme = grading.get_scheme("walk_forward")
    outcome = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert outcome["confirmed"] is False
    assert scheme.grade(conn, cand["id"])["regime_limited"] is True


def test_evaluate_fold_refuses_windows_without_future_data(conn):
    """Only data genuinely after the discovery window can confirm; a fold with
    no such data yet is an error, not a free pass."""
    full, _, cand = _admitted_run(conn, "gradetest-nofuture")
    scheme = grading.get_scheme("walk_forward")
    truncated = _market_from(truncate_universe(full, 500))  # nothing after discovery
    with pytest.raises(grading.GradingError):
        scheme.evaluate_fold(conn, truncated, cand["id"], fold=1)


# --- issue #67: no evidence is not failure ------------------------------------

def test_power_refused_fold_records_no_evidence_not_failure(conn):
    """A 30-day fold holds ~1 episode of a 10-day-cycle regime — the re-test
    refuses at the floor. That is absent evidence, not contradicting evidence:
    it must not count as a fold, must not fail the grade, and must not flag
    regime-limited (issue #67)."""
    full, _, cand = _admitted_run(conn, "gradetest-refused", fold_length_days=30)
    scheme = grading.get_scheme("walk_forward")
    outcome = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert outcome["refused"] is True
    assert outcome["confirmed"] is None
    g = scheme.grade(conn, cand["id"])
    assert g["folds"] == 0                 # no evidence entered the denominator
    assert g["no_evidence"] == 1           # …but the refusal is visible
    assert g["regime_limited"] is False    # absent evidence is not weakness
    assert g["grade"] is None


def test_genuine_fold_failure_still_fails(conn):
    """The fix must not soften real contradictions: an evaluable fold with no
    edge still records confirmed=False and flags regime-limited."""
    full, _, cand = _admitted_run(conn, "gradetest-realfail", effect_through=500)
    scheme = grading.get_scheme("walk_forward")
    outcome = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert outcome["refused"] is False
    assert outcome["confirmed"] is False
    g = scheme.grade(conn, cand["id"])
    assert g["folds"] == 1 and g["regime_limited"] is True


def test_regrading_widens_the_grid_and_replaces_no_evidence_rows(conn):
    """Grading config is a forward-looking declaration: it may be re-declared
    for an admitted edge UNTIL real evidence exists. Re-registration with a
    wider fold replaces a no-evidence row; the durable edge then confirms."""
    full, _, cand = _admitted_run(conn, "gradetest-regrid", fold_length_days=30)
    scheme = grading.get_scheme("walk_forward")
    refused = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert refused["refused"] is True
    # widen the grid — the prod scenario (run 2's regimes froze 30-day folds)
    scheme.register(conn, cand["id"], fold_length_days=150)
    outcome = scheme.evaluate_fold(conn, _market_from(full), cand["id"], fold=1)
    assert outcome["refused"] is False and outcome["confirmed"] is True
    g = scheme.grade(conn, cand["id"])
    assert g["folds"] == 1 and g["confirmed"] == 1
    assert g["no_evidence"] == 0           # the placeholder was replaced


def test_regrading_locked_once_evidence_exists(conn):
    """Once a non-refused forward row exists the grid is immutable — moving
    the fold boundaries after seeing outcomes would be selection."""
    _, _, cand = _admitted_run(conn, "gradetest-lock")
    scheme = grading.get_scheme("walk_forward")
    scheme.record_forward_result(conn, cand["id"], fold=1, confirmed=True)
    with pytest.raises(grading.GradingError):
        scheme.register(conn, cand["id"], fold_length_days=60)


def test_evidence_rows_are_immutable(conn):
    """A confirmed/failed fold row can never be overwritten — only
    no-evidence placeholders are replaceable."""
    _, _, cand = _admitted_run(conn, "gradetest-immutable")
    scheme = grading.get_scheme("walk_forward")
    scheme.record_forward_result(conn, cand["id"], fold=1, confirmed=True)
    with pytest.raises(grading.GradingError):
        scheme.record_forward_result(conn, cand["id"], fold=1, confirmed=False)
