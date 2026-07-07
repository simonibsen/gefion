"""Nested-segregation tests for regime discovery (006, T011 — US1).

TDD: written FIRST. The DiscoveryDataContext is the ONLY data path during
discovery: it exposes inner-window rows only and raises on any outer-holdout
access — enforcement by construction, not convention (FR-101/102). Includes
the leaked-vs-nested negative demonstration: fitting a boundary WITH holdout
data manufactures significance from noise; the nested path does not.
"""
import datetime

import numpy as np
import pytest

from gefion.experiments.holdout import HoldoutManager
from gefion.experiments.statistical import compute_holdout_pvalue
from gefion.regimes.discovery import segregation
from tests.discovery_synth import make_universe


def _market(seed=3, n_days=400):
    u = make_universe(seed=seed, n_days=n_days, n_features=2)
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-test"), u


def _context(seed=3, n_days=400, holdout_weeks=6):
    market, u = _market(seed, n_days)
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=holdout_weeks)
    return segregation.DiscoveryDataContext(market, holdout), u, holdout


# --- inner-only access -------------------------------------------------------

def test_inner_rows_end_at_the_boundary():
    ctx, u, holdout = _context()
    inner = ctx.inner_feature("noise_0")
    assert inner, "inner window must be non-empty"
    assert max(d for d, _ in inner) <= holdout.get_max_training_date()
    assert max(d for d, _ in ctx.inner_returns()) <= holdout.get_max_training_date()


def test_outer_holdout_rows_are_not_exposed():
    ctx, u, holdout = _context()
    outer_dates = {d for d, _ in u.forward_returns if d >= holdout.holdout_start_date}
    assert outer_dates, "test needs a non-empty holdout"
    inner_dates = {d for d, _ in ctx.inner_feature("noise_0")}
    assert not (inner_dates & outer_dates)


def test_unknown_feature_raises():
    ctx, _, _ = _context()
    with pytest.raises(LookupError):
        ctx.inner_feature("not_a_feature")


def test_check_dates_raises_on_holdout_touch():
    ctx, u, holdout = _context()
    with pytest.raises(segregation.SegregationError):
        ctx.check_dates([holdout.holdout_end_date])
    # inner dates pass
    ctx.check_dates([holdout.get_max_training_date()])


def test_boundaries_recorded_for_preregistration():
    ctx, u, holdout = _context()
    b = ctx.boundaries()
    assert b["inner_start"] == str(u.dates[0])
    assert b["inner_end"] == str(holdout.get_max_training_date())
    assert b["holdout_start"] == str(holdout.holdout_start_date)
    assert b["holdout_end"] == str(holdout.holdout_end_date)
    assert datetime.date.fromisoformat(b["inner_end"]) < datetime.date.fromisoformat(
        b["holdout_start"])


def test_context_requires_data_before_the_holdout():
    """A dataset that is ALL holdout cannot prove segregation — refuse."""
    market, u = _market(n_days=20)
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=6)
    with pytest.raises(segregation.SegregationError):
        segregation.DiscoveryDataContext(market, holdout)


# --- the leaked-vs-nested negative demonstration (T1/T3) ---------------------
#
# "Fit" a regime by picking the signal-alignment threshold that looks best —
# once peeking at the holdout (leaked), once on inner data only (nested) —
# then test the chosen regime's edge ON the holdout. The leaked path
# manufactures significance from pure noise; the nested path does not.
# This is a demonstration of the trap, not product behavior: the framework
# forbids the leaked path by construction.

def _best_threshold_pvalue(fit_sig, fit_fwd, eval_sig, eval_fwd):
    """Pick the threshold that maximizes edge on the FIT slice, return its
    p-value on the EVAL slice."""
    best_p, best_thr = 1.0, None
    for thr in np.quantile(fit_sig, np.linspace(0.1, 0.9, 17)):
        mask = fit_sig > thr
        if mask.sum() < 10:
            continue
        scores = list(np.sign(fit_sig[mask]) * fit_fwd[mask])
        p = compute_holdout_pvalue([0.0] * len(scores), scores, alternative="greater")
        if p < best_p:
            best_p, best_thr = p, thr
    mask = eval_sig > best_thr
    if mask.sum() < 5:
        return 1.0
    scores = list(np.sign(eval_sig[mask]) * eval_fwd[mask])
    return compute_holdout_pvalue([0.0] * len(scores), scores, alternative="greater")


def test_leaked_fit_fabricates_significance_nested_does_not():
    ctx, u, holdout = _context(seed=20260706, n_days=600, holdout_weeks=13)
    sig = np.array([v for _, v in u.features["noise_0"]])
    fwd = np.array([v for _, v in u.forward_returns])
    dates = np.array(u.dates)
    outer = dates >= np.datetime64(holdout.holdout_start_date)

    # leaked: threshold chosen on the holdout itself, judged on the holdout
    leaked_p = _best_threshold_pvalue(sig[outer], fwd[outer], sig[outer], fwd[outer])
    # nested: threshold chosen on inner data only (what the context exposes)
    inner_sig = np.array([v for _, v in ctx.inner_feature("noise_0")])
    inner_fwd = np.array([v for _, v in ctx.inner_returns()])
    nested_p = _best_threshold_pvalue(inner_sig, inner_fwd, sig[outer], fwd[outer])

    assert leaked_p < 0.05, "peeking should look (spuriously) significant"
    assert nested_p > 0.05, "the nested path must not inherit the fabricated edge"
