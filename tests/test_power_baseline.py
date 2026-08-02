"""Unit tests for the NASDAQ discovery-power baseline harness (issue #180).

Pure, DB-free tests of the harness's building blocks — written FIRST (TDD):

1. sector-stratified subsampling preserves sector proportions and is
   deterministic under a seed;
2. cross-sectional effective-N (correlation-discounted, the spec-005
   independence-adjustment principle applied to the symbol cross-section)
   shrinks as the subsample shrinks and discounts correlated names;
3. the fixed candidate battery is fingerprinted identically across draws;
4. the power(effective-N) curve + across-draw dispersion + marginal
   admission-power assemble correctly from per-fraction results.

The literature-pinned DIRECTION guarantees (power rises with N on a planted
edge; noise stays at the false-positive floor) run the real gate and live in
``test_power_baseline_direction.py`` (DB).
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from power_baseline_synth import (  # noqa: E402
    all_ragged_returns,
    correlated_returns,
    independent_returns,
    make_per_symbol_universe,
    ragged_correlated_returns,
)

from gefion.regimes.discovery import power_baseline as pb  # noqa: E402


# --- sector-stratified subsampling -------------------------------------------

def _sectored(counts):
    """Build members + sector_of from {sector: count}."""
    members, sector_of = [], {}
    i = 0
    for sec, c in counts.items():
        for _ in range(c):
            sym = f"S{i:04d}"
            members.append(sym)
            sector_of[sym] = sec
            i += 1
    return members, sector_of


def test_subsample_preserves_sector_proportions():
    members, sector_of = _sectored({"tech": 40, "energy": 20, "health": 20})
    sub = pb.sector_stratified_subsample(members, sector_of, 0.5, seed=1)
    from collections import Counter
    got = Counter(sector_of[s] for s in sub)
    assert got["tech"] == 20 and got["energy"] == 10 and got["health"] == 10


def test_subsample_deterministic_under_seed():
    members, sector_of = _sectored({"a": 30, "b": 30})
    assert (pb.sector_stratified_subsample(members, sector_of, 0.3, seed=7)
            == pb.sector_stratified_subsample(members, sector_of, 0.3, seed=7))


def test_subsample_seeds_differ():
    members, sector_of = _sectored({"a": 40, "b": 40})
    a = pb.sector_stratified_subsample(members, sector_of, 0.5, seed=1)
    b = pb.sector_stratified_subsample(members, sector_of, 0.5, seed=2)
    assert a != b


def test_subsample_full_fraction_returns_all():
    members, sector_of = _sectored({"a": 13, "b": 7})
    sub = pb.sector_stratified_subsample(members, sector_of, 1.0, seed=1)
    assert sorted(sub) == sorted(members)


def test_subsample_size_grows_with_fraction():
    members, sector_of = _sectored({"a": 40, "b": 40, "c": 40})
    sizes = [len(pb.sector_stratified_subsample(members, sector_of, f, seed=3))
             for f in (0.25, 0.5, 0.75, 1.0)]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]


# --- cross-sectional effective-N ---------------------------------------------

def test_effective_n_below_raw_when_correlated():
    rets = correlated_returns(seed=1, n_symbols=40, rho_scale=0.1, idio_scale=0.01)
    eff = pb.effective_cross_section_n(rets)
    assert 1.0 <= eff < 40  # correlated names add little
    assert eff < 20         # tight correlation discounts hard


def test_effective_n_near_raw_when_independent():
    rets = independent_returns(seed=2, n_symbols=30)
    eff = pb.effective_cross_section_n(rets)
    assert eff > 24  # little to discount


def test_effective_n_shrinks_with_subsample():
    rets = correlated_returns(seed=3, n_symbols=60, rho_scale=0.05, idio_scale=0.02)
    full = pb.effective_cross_section_n(rets)
    subset = pb.effective_cross_section_n(
        {k: rets[k] for k in list(rets)[:15]})
    assert subset < full


def test_effective_n_deterministic():
    rets = correlated_returns(seed=4, n_symbols=20)
    assert pb.effective_cross_section_n(rets) == pb.effective_cross_section_n(rets)


def test_mean_pairwise_correlation_positive_for_shared_factor():
    rets = correlated_returns(seed=5, n_symbols=25, rho_scale=0.1, idio_scale=0.01)
    assert pb.mean_pairwise_correlation(rets) > 0.5


def test_effective_n_single_symbol_is_one():
    rets = independent_returns(seed=6, n_symbols=1)
    assert pb.effective_cross_section_n(rets) == 1.0


# --- ragged real cross-section (issue #183 regression) -----------------------

def test_naive_intersection_collapses_on_ragged_histories():
    """Pins the root cause: the complete-case intersection of EVERY symbol's
    dates is empty on a ragged cross-section (staggered IPO/delist), which is why
    the pre-fix effective-N degenerated to the raw count."""
    rets = ragged_correlated_returns(seed=11)
    common = set.intersection(*(set(dict(v)) for v in rets.values()))
    assert len(common) == 0


def test_effective_n_robust_to_ragged_histories():
    """The regression for #183: on a genuinely correlated but ragged panel the
    robust alignment must still discount — rho > 0 and effective-N meaningfully
    below the raw count. Pre-fix (naive intersection) this returns rho == 0 and
    effective_n == raw N, so this test FAILS against the old code."""
    rets = ragged_correlated_returns(seed=11)
    n_raw = sum(1 for v in rets.values() if v)
    rho = pb.mean_pairwise_correlation(rets)
    eff = pb.effective_cross_section_n(rets)
    assert rho > 0.15                 # the shared factor is recovered
    assert 1.0 <= eff < 0.8 * n_raw   # a meaningful correlation discount
    # and the alignment reports what survived the ragged-tail pruning
    al = pb.align_cross_section(rets)
    assert not al.thin
    assert al.n_symbols_in == n_raw
    assert 2 <= al.n_symbols_kept < n_raw   # the IPO/delisted tail was dropped
    assert al.n_dates >= 20                 # a dense complete-case panel


def test_align_reports_thin_panel_instead_of_silent_zero():
    """Coverage-threshold edge: when nothing clears the coverage bar the panel is
    genuinely too thin to estimate a cross-sectional correlation. That must be
    surfaced (thin=True, NaN correlation) — never a silent 0 that would masquerade
    as 'uncorrelated' and inflate effective-N back to raw N."""
    rets = all_ragged_returns(seed=12)
    al = pb.align_cross_section(rets)
    assert al.thin
    assert al.n_symbols_kept < 2
    assert math.isnan(al.mean_correlation)
    assert math.isnan(pb.mean_pairwise_correlation(rets))
    assert math.isnan(pb.effective_cross_section_n(rets))


# --- fixed candidate battery fingerprint -------------------------------------

def test_battery_fingerprint_order_independent():
    a = pb.battery_fingerprint(["grammar:x", "interaction:y", "grammar:z"])
    b = pb.battery_fingerprint(["grammar:z", "grammar:x", "interaction:y"])
    assert a == b


def test_battery_fingerprint_detects_change():
    a = pb.battery_fingerprint(["grammar:x", "interaction:y"])
    b = pb.battery_fingerprint(["grammar:x", "interaction:y", "grammar:z"])
    assert a != b


# --- curve assembly, dispersion, marginal power ------------------------------

def _fraction_result(fraction, effective_ns, admitted):
    return {
        "fraction": fraction,
        "effective_n": pb.summarize(effective_ns),
        "admitted": pb.summarize(admitted),
        "spa_p": pb.summarize([0.5] * len(admitted)),
        "n_symbols": pb.summarize([len(admitted)] * len(admitted)),
    }


def test_summarize_reports_dispersion():
    s = pb.summarize([1.0, 3.0, 2.0])
    assert s["mean"] == 2.0 and s["min"] == 1.0 and s["max"] == 3.0
    assert s["std"] > 0 and s["values"] == [1.0, 3.0, 2.0]


def test_curve_sorted_by_effective_n():
    per_fraction = [
        _fraction_result(1.0, [40, 41, 39], [3, 2, 3]),
        _fraction_result(0.25, [12, 11, 13], [0, 0, 1]),
        _fraction_result(0.5, [22, 23, 21], [1, 1, 2]),
    ]
    curve = pb.assemble_curve(per_fraction)
    effs = [pt["effective_n"] for pt in curve]
    assert effs == sorted(effs)
    # dispersion carried onto each point
    assert all("admitted_std" in pt for pt in curve)


def test_marginal_admission_power_positive_when_power_rises():
    per_fraction = [
        _fraction_result(0.25, [12], [0]),
        _fraction_result(0.5, [22], [1]),
        _fraction_result(1.0, [40], [3]),
    ]
    curve = pb.assemble_curve(per_fraction)
    slope = pb.marginal_admission_power(curve)
    assert slope is not None and slope > 0


def test_marginal_admission_power_none_for_single_point():
    curve = pb.assemble_curve([_fraction_result(1.0, [40], [3])])
    assert pb.marginal_admission_power(curve) is None


# --- config -------------------------------------------------------------------

def test_config_defaults_and_validation():
    cfg = pb.PowerBaselineConfig(
        name="pb", atoms=[{"feature": "cond_0", "cmp": ">", "value": 0.0}],
        signals=["sig_0"])
    assert cfg.fractions == (0.25, 0.5, 0.75, 1.0)
    assert cfg.draws_per_fraction >= 2  # one draw is too noisy
    cfg.validate()


def test_config_rejects_empty_battery():
    cfg = pb.PowerBaselineConfig(name="pb", atoms=[], signals=["sig_0"])
    with pytest.raises(pb.PowerBaselineError):
        cfg.validate()


def test_config_rejects_bad_fraction():
    cfg = pb.PowerBaselineConfig(
        name="pb", atoms=[{"feature": "cond_0", "cmp": ">", "value": 0.0}],
        signals=["sig_0"], fractions=(0.0, 1.5))
    with pytest.raises(pb.PowerBaselineError):
        cfg.validate()


def test_make_per_symbol_universe_is_market_aggregation():
    """Sanity check on the synthetic generator the DB direction tests rely on:
    averaging more symbols shrinks the market forward-return noise."""
    u = make_per_symbol_universe(seed=1, n_days=300, n_symbols=60, planted=False)
    small = u.market_for_subsample(u.symbols[:6])
    big = u.market_for_subsample(u.symbols)
    small_std = np.std([v for _, v in small.forward_returns])
    big_std = np.std([v for _, v in big.forward_returns])
    assert big_std < small_std  # 1/sqrt(N) noise reduction
