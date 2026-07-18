"""Tier-evaluator tests (008, T004 — Foundational). Pure functions, no DB.

TDD: written FIRST. The proving cases are the live prod garbage from issue
#79 (convict) and the degenerate-but-real population (never convict — SC-301's
zero-false-convictions guarantee). Tiers 3–4 are structurally incapable of a
trash verdict (FR-304).
"""
import pytest

from gefion.quality import rules
from gefion.quality.catalog import Metric


def _metric(name="beta", bounds=(-50.0, 50.0), **kw):
    return Metric(name=name, entity_table="stocks", table="stocks_fundamentals",
                  column=name, why="test", bounds=bounds, **kw)


# --- tier 1: definitional bounds ---------------------------------------------------

def test_bounds_convict_the_issue79_quartet():
    beta = _metric("beta", bounds=(-50.0, 50.0))
    for garbage in (-503341.44, -165013.73):  # MDXH, ELOX — observed live
        r = rules.check_bounds(beta, garbage)
        assert r is not None
        assert r.verdict == "trash"
        assert r.rule == "definitional_bound"
        assert r.observed == garbage
    dy = _metric("dividend_yield", bounds=(0.0, 2.0))
    r = rules.check_bounds(dy, 1000000.0)  # CTAA — observed live
    assert r is not None and r.verdict == "trash"


def test_bounds_never_convict_degenerate_but_real():
    """SC-301: internally consistent extremes stay unflagged. AV reports
    ratios as fractions — ROE -6.15 is -615%."""
    roe = _metric("return_on_equity", bounds=(-100000.0, 100000.0))
    assert rules.check_bounds(roe, -6.15) is None
    margin = _metric("operating_margin", bounds=(-100000.0, 1000.0))
    assert rules.check_bounds(margin, -1724.0) is None
    beta = _metric("beta", bounds=(-50.0, 50.0))
    assert rules.check_bounds(beta, -0.692) is None  # KIDZ's real beta


def test_bounds_edges_are_inside():
    m = _metric("beta", bounds=(-50.0, 50.0))
    assert rules.check_bounds(m, -50.0) is None
    assert rules.check_bounds(m, 50.0) is None
    assert rules.check_bounds(m, 50.0001) is not None


# --- tier 2: cross-field contradiction ---------------------------------------------

def test_cross_field_convicts_order_of_magnitude_disagreement():
    r = rules.check_cross_field(_metric("dividend_yield"), observed=1000000.0,
                                recomputed=0.02, tolerance_factor=10.0)
    assert r is not None
    assert r.verdict == "trash"
    assert r.rule == "cross_field"
    assert r.expected == 0.02


def test_cross_field_tolerates_reporting_drift():
    assert rules.check_cross_field(_metric("pe_ratio"), observed=21.0,
                                   recomputed=18.5, tolerance_factor=10.0) is None


def test_cross_field_abstains_without_a_comparator():
    """No verdict from absence of evidence (spec edge case)."""
    assert rules.check_cross_field(_metric("pe_ratio"), observed=1e9,
                                   recomputed=None, tolerance_factor=10.0) is None


def test_cross_field_convicts_sign_flips_on_material_magnitudes():
    r = rules.check_cross_field(_metric("eps"), observed=-8.0, recomputed=8.0,
                                tolerance_factor=10.0)
    assert r is not None and r.verdict == "trash"
    # near-zero sign wobble is not a contradiction
    assert rules.check_cross_field(_metric("eps"), observed=-0.01,
                                   recomputed=0.01, tolerance_factor=10.0) is None


# --- tier 3: temporal discontinuity (suspect only) ----------------------------------

def test_temporal_spike_requires_magnitude_and_reversion():
    # the live shape: episodic garbage between sane neighbors
    r = rules.check_temporal_spike(prev=1.2, value=-503341.44, nxt=1.2,
                                   spike_factor=100.0)
    assert r is not None
    assert r.verdict == "suspect"
    assert r.rule == "temporal_spike"
    # a level shift is not a spike (no reversion) — splits/re-listings are real
    assert rules.check_temporal_spike(prev=1.2, value=120.0, nxt=118.0,
                                      spike_factor=100.0) is None
    # persistent degenerate reality is not a spike
    assert rules.check_temporal_spike(prev=-6.1, value=-6.2, nxt=-6.0,
                                      spike_factor=100.0) is None


# --- tier 4: cross-sectional outlierness (suspect only) -----------------------------

def test_cross_sectional_outlier_is_suspect_with_z_detail():
    universe = [1.0, 1.1, 0.9, 1.05, 0.95, 1.2, 0.8, 1.0]
    r = rules.check_cross_sectional(value=5000.0, universe=universe,
                                    threshold=10.0)
    assert r is not None
    assert r.verdict == "suspect"
    assert r.rule == "cross_sectional_outlier"
    assert r.detail["z"] > 10
    assert rules.check_cross_sectional(value=1.15, universe=universe,
                                       threshold=10.0) is None


def test_cross_sectional_abstains_on_degenerate_universe():
    # MAD 0 (all identical) or a tiny cross-section: abstain, don't divide
    assert rules.check_cross_sectional(value=99.0, universe=[1.0, 1.0, 1.0],
                                       threshold=10.0) is None
    assert rules.check_cross_sectional(value=99.0, universe=[1.0],
                                       threshold=10.0) is None


# --- the structural cap (FR-304) ----------------------------------------------------

def test_corroboration_tiers_can_never_convict():
    """Tiers 3–4 are structurally incapable of a trash verdict, no matter how
    extreme the input."""
    spike = rules.check_temporal_spike(prev=0.0001, value=1e15, nxt=0.0001,
                                       spike_factor=100.0)
    outlier = rules.check_cross_sectional(value=1e15,
                                          universe=[1.0, 2.0, 1.5, 1.2, 0.8],
                                          threshold=10.0)
    for r in (spike, outlier):
        assert r is not None
        assert r.verdict == "suspect"


# --- series dynamic range (issue #136) — suspect only ------------------------------

def test_series_range_flags_magnitude_cliff_as_suspect():
    """ASTI observed live: restated adjusted_close 5.35e11 down to single
    digits — internally consistent restatement, so suspect, never trash."""
    r = rules.check_series_range(max_value=5.35e11, min_value=7.0,
                                 max_ratio=1.0e6)
    assert r is not None
    assert r.verdict == "suspect"
    assert r.rule == "series_dynamic_range"
    assert r.observed == pytest.approx(5.35e11 / 7.0)
    assert r.expected == 1.0e6
    assert r.detail["max"] == 5.35e11 and r.detail["min"] == 7.0


def test_series_range_tolerates_real_price_histories():
    # BRK.A-class appreciation (~2.5e3) and a 99.99% collapse (~1e4) both pass
    assert rules.check_series_range(700000.0, 275.0, 1.0e6) is None
    assert rules.check_series_range(500.0, 0.05, 1.0e6) is None


def test_series_range_abstains_without_positive_floor():
    """No division by silence: a series whose smallest positive value is
    missing or nonpositive yields no ratio and no verdict."""
    assert rules.check_series_range(100.0, 0.0, 1.0e6) is None
    assert rules.check_series_range(100.0, -5.0, 1.0e6) is None
    assert rules.check_series_range(100.0, None, 1.0e6) is None
