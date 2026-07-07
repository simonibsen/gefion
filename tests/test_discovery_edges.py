"""Edge-evaluation tests for regime discovery (006, T013 — US1 tier 1).

TDD: written FIRST. The v1 signal source builds per-observation records from
active features causally (trailing-median alignment, no future data); the
tier-1 path answers the gradient question per candidate via the 005
continuous-interaction test, restricted to the outer holdout window.
"""
import numpy as np
import pytest

from gefion.experiments.holdout import HoldoutManager
from gefion.regimes.discovery import edges, grammar, segregation, signals
from tests.discovery_synth import make_universe, plant_regime_edge


def _market(u):
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-test")


# --- per-observation records from signal_source='features' -------------------

def test_signal_source_validates_signals():
    u = make_universe(seed=1, n_days=200, n_features=2)
    with pytest.raises(LookupError):
        signals.FeatureSignalSource(_market(u), ["nope"])


def test_records_are_causal_and_aligned():
    u = make_universe(seed=2, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"], align_window=60)
    recs = src.records("noise_0")
    # warmup: no record before the trailing window is full
    assert len(recs) == 300 - 59
    fwd = dict(u.forward_returns)
    for r in recs[:20]:
        assert r["baseline_score"] == 0.0
        assert abs(r["experimental_score"]) == pytest.approx(abs(fwd[r["date"]]))


def _truncate(u, n):
    """Same universe, future removed — the honest way to test causality."""
    import dataclasses
    return dataclasses.replace(
        u,
        dates=u.dates[:n],
        prices=u.prices[:n],
        features={k: v[:n] for k, v in u.features.items()},
        forward_returns=u.forward_returns[:n],
    )


def test_records_prefix_invariant():
    """Alignment at date t must not change when future data is appended —
    the causal guarantee, tested by truncation."""
    u_full = make_universe(seed=4, n_days=300, n_features=1)
    u_short = _truncate(u_full, 200)
    full = signals.FeatureSignalSource(_market(u_full), ["noise_0"]).records("noise_0")
    short = signals.FeatureSignalSource(_market(u_short), ["noise_0"]).records("noise_0")
    by_date = {r["date"]: r for r in full}
    for r in short:
        assert by_date[r["date"]]["experimental_score"] == pytest.approx(
            r["experimental_score"])


def test_records_window_restriction():
    u = make_universe(seed=5, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    start, end = u.dates[250], u.dates[280]
    recs = src.records("noise_0", start=start, end=end)
    assert recs and all(start <= r["date"] <= end for r in recs)


# --- tier-1: continuous-interaction per candidate -----------------------------

def test_tier1_interaction_finds_planted_gradient():
    u = plant_regime_edge(make_universe(seed=6, n_days=400, n_features=2), "noise_0")
    src = signals.FeatureSignalSource(_market(u), ["noise_0", "noise_1"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="planted_cond")
    assert test["pvalue"] is not None and test["pvalue"] < 0.01
    null = edges.tier1_interaction_test(
        src, signal="noise_1", conditioning_feature="planted_cond")
    assert null["pvalue"] is None or null["pvalue"] > 0.01


def test_tier1_refuses_small_samples():
    u = make_universe(seed=7, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="noise_0",
        start=u.dates[-2], end=u.dates[-1])
    assert test["pvalue"] is None and test["reason"] == "min_sample"
    assert test["n"] < 5


def test_tier1_respects_evaluation_window():
    """Restricting to the outer holdout must only use holdout observations."""
    u = plant_regime_edge(make_universe(seed=8, n_days=400, n_features=1), "noise_0")
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=13)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="planted_cond",
        start=holdout.holdout_start_date, end=holdout.holdout_end_date)
    outer_days = sum(1 for d in u.dates if d >= holdout.holdout_start_date)
    assert test["n"] <= outer_days


# --- causal bucket labels from candidate ASTs ---------------------------------

def test_causal_labels_from_tercile_candidate():
    u = make_universe(seed=9, n_days=300, n_features=2)
    cand = grammar.enumerate_candidates(
        [{"feature": "noise_0", "form": "tercile"}], depth=1)[0]
    labels = edges.causal_labels(cand, _market(u), window=60)
    assert set(labels.values()) <= {"low", "mid", "high", "undefined"}
    defined = [lab for lab in labels.values() if lab != "undefined"]
    assert len(defined) == 300 - 59


def test_causal_labels_prefix_invariant():
    """Label at t must not change when future data is appended (FR-103)."""
    u_full = make_universe(seed=10, n_days=300, n_features=1)
    u_short = _truncate(u_full, 220)
    cand = grammar.enumerate_candidates(
        [{"feature": "noise_0", "form": "tercile"}], depth=1)[0]
    full = edges.causal_labels(cand, _market(u_full), window=60)
    short = edges.causal_labels(cand, _market(u_short), window=60)
    for d, lab in short.items():
        assert full[d] == lab


def test_causal_labels_boolean_composite():
    u = make_universe(seed=11, n_days=200, n_features=2)
    cands = grammar.enumerate_candidates(
        [{"feature": "noise_0", "cmp": ">", "value": 0.0},
         {"feature": "noise_1", "cmp": ">", "value": 0.0}], depth=2)
    composite = next(c for c in cands if c.depth == 2)
    labels = edges.causal_labels(composite, _market(u), window=60)
    assert set(labels.values()) <= {"true", "false"}
