"""Detector-candidate tests (006, T029 — US3 expressive tier).

TDD: written FIRST. Detector candidates (HMM/clustering-style code) execute
through the SAME whitelisted-import sandbox as AI-generated features (R5):
`fit(series, seed)` strictly on the DiscoveryDataContext's inner window,
`label(series, params)` causal. Guards, all fail-closed and all recorded:
degeneracy (a bucket holding >90% or <2%), instability (seeded refits that
disagree), and non-causality (labels that change when the future is removed).
Fitted parameters are recorded — T3's degrees of freedom are ledgered, not
hidden.
"""
import pytest

from gefion.experiments.holdout import HoldoutManager
from gefion.regimes.discovery import detectors, segregation
from tests.discovery_synth import make_universe, plant_regime_edge

# A well-behaved detector: threshold on the trailing-median-adjusted level.
GOOD_DETECTOR = '''
import numpy as np

def fit(series, seed=0):
    values = [v for _, v in series]
    return {"threshold": float(np.median(values))}

def label(series, params):
    out = []
    history = []
    for d, v in series:
        history.append(v)
        out.append((d, "high" if v > params["threshold"] else "low"))
    return out
'''

DEGENERATE_DETECTOR = '''
def fit(series, seed=0):
    return {}

def label(series, params):
    return [(d, "always") for d, _ in series]
'''

UNSTABLE_DETECTOR = '''
import numpy as np

def fit(series, seed=0):
    rng = np.random.default_rng(seed)
    return {"cut": float(rng.normal(scale=2.0))}   # seed-dependent boundary

def label(series, params):
    return [(d, "high" if v > params["cut"] else "low") for d, v in series]
'''

NONCAUSAL_DETECTOR = '''
import numpy as np

def fit(series, seed=0):
    return {}

def label(series, params):
    values = [v for _, v in series]
    cut = float(np.median(values))  # uses the FULL series at every date
    return [(d, "high" if v > cut else "low") for d, v in series]
'''

FORBIDDEN_IMPORT_DETECTOR = '''
import os

def fit(series, seed=0):
    return {}

def label(series, params):
    return [(d, "x") for d, _ in series]
'''


def _ctx(seed=61, n_days=400):
    u = make_universe(seed=seed, n_days=n_days, n_features=2)
    market = segregation.MarketData(features=u.features,
                                    forward_returns=u.forward_returns,
                                    dataset_version="synth-test")
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=13)
    return segregation.DiscoveryDataContext(market, holdout), u


def test_good_detector_fits_and_labels():
    ctx, u = _ctx()
    result = detectors.run_detector_candidate(ctx, GOOD_DETECTOR, feature="noise_0",
                                              seed=7)
    assert result["refusal"] is None
    assert "threshold" in result["params"]          # T3: fitted params recorded
    labels = dict(result["labels"])
    assert set(labels.values()) == {"high", "low"}
    # discovery-phase labels cover exactly the inner window (outer labeling
    # happens at evaluation time, after the freeze)
    assert len(labels) == len(ctx.inner_feature("noise_0"))


def test_detector_fits_on_inner_window_only():
    """The fit sees exactly the DiscoveryDataContext's inner rows."""
    ctx, u = _ctx()
    result = detectors.run_detector_candidate(ctx, GOOD_DETECTOR, feature="noise_0",
                                              seed=7)
    import numpy as np
    inner_values = [v for _, v in ctx.inner_feature("noise_0")]
    assert result["params"]["threshold"] == pytest.approx(float(np.median(inner_values)))


def test_degenerate_detector_is_refused():
    ctx, _ = _ctx()
    result = detectors.run_detector_candidate(ctx, DEGENERATE_DETECTOR,
                                              feature="noise_0", seed=7)
    assert result["refusal"] == "degenerate"
    assert "share" in result["detail"]


def test_unstable_detector_is_refused():
    """Seeded refits that disagree -> refused_unstable, with the agreement
    fraction recorded."""
    ctx, _ = _ctx()
    result = detectors.run_detector_candidate(ctx, UNSTABLE_DETECTOR,
                                              feature="noise_0", seed=7)
    assert result["refusal"] == "unstable"
    assert result["detail"]["agreement"] < detectors.STABILITY_FLOOR


def test_noncausal_detector_is_refused():
    """Labels must not change when the future is truncated away."""
    ctx, _ = _ctx()
    result = detectors.run_detector_candidate(ctx, NONCAUSAL_DETECTOR,
                                              feature="noise_0", seed=7)
    assert result["refusal"] == "noncausal"


def test_forbidden_import_is_blocked_by_the_sandbox():
    ctx, _ = _ctx()
    with pytest.raises(detectors.DetectorError):
        detectors.run_detector_candidate(ctx, FORBIDDEN_IMPORT_DETECTOR,
                                         feature="noise_0", seed=7)


def test_detector_missing_contract_functions_raises():
    ctx, _ = _ctx()
    with pytest.raises(detectors.DetectorError):
        detectors.run_detector_candidate(ctx, "def fit(series, seed=0):\n    return {}",
                                         feature="noise_0", seed=7)


# --- principle-seeded detector candidates (T031, US3) --------------------------

def test_seed_detectors_from_principles():
    principles = [
        {"id": "regime-detection-hmm",
         "data_requirements": ["ohlcv.close", "features.volatility_realized"]},
        {"id": "kelly-criterion-sizing", "data_requirements": ["ohlcv.close"]},
    ]
    cands = detectors.seed_detectors_from_principles(principles, ["noise_0", "noise_1"])
    assert len(cands) == 1  # only principles with a detector template seed one
    cand = cands[0]
    assert cand["provenance"]["principle_id"] == "regime-detection-hmm"
    assert cand["feature"] in ("noise_0", "noise_1")
    assert "def fit" in cand["code"] and "def label" in cand["code"]


def test_seeded_detector_template_passes_the_screens():
    """The built-in template must survive its own guards on ordinary data."""
    ctx, _ = _ctx()
    cands = detectors.seed_detectors_from_principles(
        [{"id": "regime-detection-hmm", "data_requirements": []}], ["noise_0"])
    result = detectors.run_detector_candidate(ctx, cands[0]["code"],
                                              feature="noise_0", seed=3)
    assert result["refusal"] is None, result["detail"]
