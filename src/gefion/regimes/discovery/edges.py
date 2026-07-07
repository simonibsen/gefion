"""Conditional edge tests per candidate regime (006, T014 — US1 tier 1).

Zero new statistics (R3): the tier-1 gradient question is the 005
continuous-interaction test (one HAC coefficient per signal x candidate);
candidate bucket labels are computed causally through the 005 label
primitives. Small samples refuse rather than emit a fragile p-value
(fail-closed, FR-107).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import numpy as np

from gefion.observability import create_span, set_attributes
from gefion.regimes.definitions import RegimeDefinition, iter_leaves
from gefion.regimes.discovery.grammar import Candidate
from gefion.regimes.discovery.segregation import MarketData
from gefion.regimes.discovery.signals import FeatureSignalSource
from gefion.regimes.interaction import continuous_interaction
from gefion.regimes.labels import compute_labels

MIN_INTERACTION_N = 5  # continuous_interaction's own floor


def tier1_interaction_test(
    src: FeatureSignalSource,
    signal: str,
    conditioning_feature: str,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Does `signal`'s edge scale with `conditioning_feature` in [start, end]?

    One interaction coefficient + p-value (005 HAC test). Too few aligned
    observations → an explicit refusal record (pvalue None), never a guess.
    """
    with create_span("discovery.edges.tier1",
                     signal=signal, conditioning=conditioning_feature) as span:
        sig = dict(src.series(signal))
        cond = dict(src.series(conditioning_feature))
        fwd = dict(src.market.forward_returns)
        dates = [d for d in sorted(set(sig) & set(cond) & set(fwd))
                 if (start is None or d >= start) and (end is None or d <= end)]
        n = len(dates)
        base = {"signal": signal, "conditioning": conditioning_feature, "n": n}
        if n < MIN_INTERACTION_N:
            set_attributes(span, refused=True, n=n)
            return {**base, "pvalue": None, "reason": "min_sample"}
        result = continuous_interaction(
            np.array([sig[d] for d in dates]),
            np.array([cond[d] for d in dates]),
            np.array([fwd[d] for d in dates]),
        )
        set_attributes(span, n=n, pvalue=result["interaction_pvalue"])
        return {**base, "n": result["n"],
                "pvalue": result["interaction_pvalue"],
                "coef": result["interaction_coef"]}


def causal_labels(candidate: Candidate, market: MarketData,
                  window: int = 60) -> Dict[datetime.date, str]:
    """Causal bucket labels for a candidate over the full timeline.

    Every label at t uses only data <= t (005 FR-004, inherited via
    compute_labels), so labels may be computed once and then restricted to
    the outer holdout for evaluation. Referenced feature series are aligned
    to their common dates first (boolean AST evaluation zips children by
    position).
    """
    with create_span("discovery.edges.causal_labels") as span:
        refs = {leaf["feature"] for leaf in iter_leaves(candidate.expression)}
        missing = refs - set(market.features)
        if missing:
            raise LookupError(f"candidate references unavailable feature(s): {sorted(missing)}")
        common = set.intersection(*({d for d, _ in market.features[r]} for r in refs))
        features = {r: [(d, v) for d, v in market.features[r] if d in common]
                    for r in refs}
        defn = RegimeDefinition(name="discovery-candidate", scope="market",
                                expression=candidate.expression,
                                bucketing=candidate.bucketing)
        rows = compute_labels(defn, features, window=window,
                              dataset_version=market.dataset_version)
        set_attributes(span, n_labels=len(rows))
        return {d: label for d, _, label in rows}
