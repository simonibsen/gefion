"""Tier evaluators (008, T005 — Foundational). Pure functions.

Only tiers 1–2 (definitional bounds, cross-field contradiction) can return a
trash verdict. Tiers 3–4 (temporal spike, cross-sectional outlier) are
structurally capped at suspect (FR-304): outlierness cannot distinguish trash
from distress, so it corroborates — it never convicts.

Every evaluator returns None to abstain (in-bounds, tolerable drift, missing
comparator, degenerate universe): no verdict from absence of evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from gefion.quality.catalog import Metric

# magnitudes below this are noise around zero — sign flips there are not
# contradictions, and spike ratios use it as the division floor
_MAGNITUDE_FLOOR = 0.5
_EPS = 1e-12


@dataclass
class RuleResult:
    rule: str
    verdict: str  # 'trash' | 'suspect'
    observed: float
    expected: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def check_bounds(metric: Metric, value: float) -> Optional[RuleResult]:
    """Tier 1: definitional envelope from the catalog. Trash outside it."""
    if metric.bounds is None:
        return None
    lo, hi = metric.bounds
    if lo <= value <= hi:
        return None
    return RuleResult(
        rule="definitional_bound", verdict="trash", observed=value,
        expected=lo if value < lo else hi,
        detail={"bounds": [lo, hi], "why": metric.why},
    )


def check_cross_field(metric: Metric, observed: float,
                      recomputed: Optional[float],
                      tolerance_factor: float) -> Optional[RuleResult]:
    """Tier 2: contradiction against a trusted recompute. Trash by
    construction on order-of-magnitude disagreement or a material sign flip.
    Abstains without a comparator."""
    if recomputed is None:
        return None
    obs_mag, rec_mag = abs(observed), abs(recomputed)
    ratio = max(obs_mag, rec_mag) / max(min(obs_mag, rec_mag), _EPS)
    sign_flip = (observed * recomputed < 0
                 and obs_mag > _MAGNITUDE_FLOOR and rec_mag > _MAGNITUDE_FLOOR)
    if ratio <= tolerance_factor and not sign_flip:
        return None
    return RuleResult(
        rule="cross_field", verdict="trash", observed=observed,
        expected=recomputed,
        detail={"ratio": ratio, "sign_flip": sign_flip,
                "tolerance_factor": tolerance_factor,
                "derivation": (metric.derivation or {}).get("expression")},
    )


def check_temporal_spike(prev: Optional[float], value: float,
                         nxt: Optional[float],
                         spike_factor: float) -> Optional[RuleResult]:
    """Tier 3 (suspect only): episodic spike — the value dwarfs BOTH
    neighbors and the series reverts. Level shifts (splits, re-listings) have
    no reversion and pass; persistent degenerate reality has no spike."""
    if prev is None or nxt is None:
        return None
    neighbor_mag = max(abs(prev), abs(nxt), _MAGNITUDE_FLOOR)
    if abs(value) <= spike_factor * neighbor_mag:
        return None
    # reversion: the next observation returns to the prior regime rather than
    # following the value to its new level
    if abs(nxt) > abs(value) / spike_factor:
        return None
    return RuleResult(
        rule="temporal_spike", verdict="suspect", observed=value,
        expected=prev,
        detail={"prev": prev, "next": nxt, "spike_factor": spike_factor},
    )


def check_series_range(max_value: Optional[float], min_value: Optional[float],
                       max_ratio: float) -> Optional[RuleResult]:
    """Series dynamic range (suspect only, issue #136): the max over the
    smallest POSITIVE value of one entity's whole series. Serial reverse-split
    restatements are internally consistent — a 5e11 'price' decaying to single
    digits is real provider semantics, not trash — so outlierness corroborates,
    it never convicts. Abstains without a positive floor: no ratio from
    silence."""
    if max_value is None or min_value is None or min_value <= 0 or max_value <= 0:
        return None
    ratio = max_value / min_value
    if ratio <= max_ratio:
        return None
    return RuleResult(
        rule="series_dynamic_range", verdict="suspect", observed=ratio,
        expected=max_ratio,
        detail={"max": max_value, "min": min_value, "max_ratio": max_ratio},
    )


def check_cross_sectional(value: float, universe: List[float],
                          threshold: float) -> Optional[RuleResult]:
    """Tier 4 (suspect only): robust z against the same-date universe —
    |v − median| / (1.4826 × MAD). Abstains on degenerate universes (tiny
    cross-section or zero MAD): don't divide by silence."""
    if len(universe) < 3:
        return None
    arr = np.asarray(universe, dtype=float)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    if mad <= _EPS:
        return None
    z = abs(value - median) / (1.4826 * mad)
    if z <= threshold:
        return None
    return RuleResult(
        rule="cross_sectional_outlier", verdict="suspect", observed=value,
        expected=median,
        detail={"z": z, "median": median, "mad": mad, "threshold": threshold,
                "n": len(universe)},
    )
