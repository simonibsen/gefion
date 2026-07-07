"""Expressive-tier detector runtime (006, T030 — US3).

Detector candidates (HMM/clustering-style code) run through the SAME
whitelisted-import sandbox as AI-generated feature functions (R5) — no new
security surface. Contract: `fit(series, seed) -> params` executed strictly
on the DiscoveryDataContext's inner window; `label(series, params) ->
[(date, label)]` causal.

Three cheap, effective screens run before any fresh-holdout data is spent,
all fail-closed and all recorded (FR-113 / SC-106):

- degeneracy — a bucket holding more (or less) than the declared share bounds
  is uninformative, not evaluable;
- instability — seeded refits whose labels disagree mean the boundary is an
  artifact of the fit, not the data (T3);
- non-causality — labels that change when the future is truncated away are
  lookahead, full stop.

Fitted parameters are always returned for the ledger's provenance: T3's
degrees of freedom are recorded, never hidden.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from gefion.features.dispatcher import SandboxExecutionError, exec_sandboxed
from gefion.observability import create_span, set_attributes
from gefion.regimes.discovery.segregation import DiscoveryDataContext, MarketData

DEGENERACY_MAX_SHARE = 0.90
DEGENERACY_MIN_SHARE = 0.02
STABILITY_FLOOR = 0.80
N_REFITS = 3
CAUSALITY_PREFIXES = (0.6, 0.8)


class DetectorError(RuntimeError):
    """Raised when detector code cannot be executed or violates its contract."""


def _load(code: str):
    try:
        fns = exec_sandboxed(code, "fit", "label")
    except SandboxExecutionError as exc:
        raise DetectorError(f"detector code rejected by sandbox: {exc}") from exc
    return fns["fit"], fns["label"]


def _labels(fn_label, series, params) -> List[Tuple[Any, str]]:
    out = fn_label(series, params)
    try:
        return [(d, str(lab)) for d, lab in out]
    except (TypeError, ValueError) as exc:
        raise DetectorError(f"label() must return [(date, label)]: {exc}") from exc


def run_detector_candidate(ctx: DiscoveryDataContext, code: str, feature: str,
                           seed: int) -> Dict[str, Any]:
    """Fit and screen one detector candidate on the inner window only.

    Returns {"params", "labels", "refusal", "detail"} — refusal is None when
    every screen passes, else "degenerate" | "unstable" | "noncausal" with a
    quantitative detail dict (recorded as a diagnostic by the runner).
    """
    with create_span("discovery.detectors.run_candidate", feature=feature) as span:
        fn_fit, fn_label = _load(code)
        inner = ctx.inner_feature(feature)
        try:
            params = fn_fit(inner, seed=seed)
        except Exception as exc:
            raise DetectorError(f"fit() failed: {exc}") from exc
        labels = _labels(fn_label, inner, params)
        result: Dict[str, Any] = {"params": params, "labels": labels,
                                  "refusal": None, "detail": None}

        # -- degeneracy screen ----------------------------------------------
        counts: Dict[str, int] = {}
        for _, lab in labels:
            counts[lab] = counts.get(lab, 0) + 1
        shares = {lab: n / len(labels) for lab, n in counts.items()}
        if any(s > DEGENERACY_MAX_SHARE or s < DEGENERACY_MIN_SHARE
               for s in shares.values()):
            result["refusal"] = "degenerate"
            result["detail"] = {"share": shares,
                                "bounds": [DEGENERACY_MIN_SHARE, DEGENERACY_MAX_SHARE]}
            set_attributes(span, refusal="degenerate")
            return result

        # -- stability screen (seeded refits must agree) ----------------------
        agreements = []
        base = [lab for _, lab in labels]
        for refit_seed in range(seed + 1, seed + N_REFITS):
            try:
                refit_params = fn_fit(inner, seed=refit_seed)
            except Exception as exc:
                raise DetectorError(f"refit failed: {exc}") from exc
            refit = [lab for _, lab in _labels(fn_label, inner, refit_params)]
            agreements.append(
                sum(1 for a, b in zip(base, refit) if a == b) / len(base))
        if min(agreements) < STABILITY_FLOOR:
            result["refusal"] = "unstable"
            result["detail"] = {"agreement": min(agreements), "refits": N_REFITS,
                                "floor": STABILITY_FLOOR}
            set_attributes(span, refusal="unstable")
            return result

        # -- causality screen (prefix invariance) -----------------------------
        by_date = dict(labels)
        for frac in CAUSALITY_PREFIXES:
            prefix = inner[: int(len(inner) * frac)]
            for d, lab in _labels(fn_label, prefix, params):
                if by_date.get(d) != lab:
                    result["refusal"] = "noncausal"
                    result["detail"] = {"prefix_fraction": frac, "date": str(d),
                                        "full": by_date.get(d), "prefix": lab}
                    set_attributes(span, refusal="noncausal")
                    return result

        set_attributes(span, refusal="none", n_labels=len(labels))
        return result


# --- principle-seeded detector templates (T031) --------------------------------
#
# v1 ships one deliberately simple template per detector-flavored principle:
# a two-state boundary fit on inner data. A real HMM (sklearn mixture) can
# slot in later through the same seam; the guards don't care how the boundary
# was fit, only that it is stable, non-degenerate, and causal.

_TWO_STATE_TEMPLATE = '''
import numpy as np

def fit(series, seed=0):
    values = [v for _, v in series]
    return {"cut": float(np.median(values))}

def label(series, params):
    return [(d, "high" if v > params["cut"] else "low") for d, v in series]
'''

DETECTOR_TEMPLATES = {
    "regime-detection-hmm": _TWO_STATE_TEMPLATE,
}


def seed_detectors_from_principles(principles, available_features) -> List[Dict[str, Any]]:
    """Detector candidates from catalog principles that have a template.

    Feature choice follows the same conservative matching as atom seeding;
    when nothing matches, the first available feature (sorted) is used —
    a detector conditions on *some* market series, and which one is part of
    the recorded provenance either way.
    """
    from gefion.regimes.discovery.grammar import match_features

    out: List[Dict[str, Any]] = []
    for principle in principles:
        code = DETECTOR_TEMPLATES.get(principle.get("id"))
        if code is None:
            continue
        matched: List[str] = []
        for requirement in principle.get("data_requirements", []):
            if str(requirement).startswith("features."):
                matched.extend(match_features(
                    str(requirement).split(".", 1)[1], available_features))
        feature = (sorted(matched) or sorted(available_features))[0]
        out.append({
            "name": f"{principle['id']}-detector",
            "code": code,
            "feature": feature,
            "provenance": {"principle_id": principle["id"], "template": "two_state"},
        })
    return out


def apply_detector(market: MarketData, code: str, feature: str,
                   params: Dict[str, Any]) -> Dict[Any, str]:
    """Label the full timeline with FROZEN fitted params (evaluation phase).

    The params were fit on inner data only; applying the label function
    forward is causal by the prefix-invariance screen above.
    """
    with create_span("discovery.detectors.apply", feature=feature) as span:
        _, fn_label = _load(code)
        if feature not in market.features:
            raise LookupError(f"feature {feature!r} not in market data")
        labels = _labels(fn_label, market.features[feature], params)
        set_attributes(span, n_labels=len(labels))
        return dict(labels)
