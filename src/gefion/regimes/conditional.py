"""Regime-conditional experiment verdicts (spec 005, T033).

Turns per-observation holdout scores into per-regime holdout p-values and enters
them all into one flat Benjamini-Hochberg family. Fail-closed: a low-power or
undefined bucket produces no p-value and cannot survive. Reuses the experiment
framework's own primitives — compute_holdout_pvalue and apply_fdr — so the
conditional gate is the same test, sliced.
"""
from __future__ import annotations

from typing import Any, Dict, List

from gefion.experiments.statistical import apply_fdr, compute_holdout_pvalue
from gefion.observability import create_span, set_attributes
from gefion.regimes.labels import effective_n


def conditional_pvalues(
    observations: List[Dict[str, Any]],
    labels_by_date: Dict[Any, str],
    alternative: str = "less",
    min_effective_n: int = 20,
) -> List[Dict[str, Any]]:
    """Per-regime holdout p-values from per-observation baseline/experimental scores.

    Each observation is {date, baseline_score, experimental_score}. Observations are
    bucketed by regime label (by date); within each bucket, if the effective (episode)
    sample clears the floor, a one-sided holdout p-value is computed — otherwise the
    bucket is low-power and gets no p-value (fail-closed).
    """
    with create_span("regimes.conditional.pvalues") as span:
        dates = sorted({o["date"] for o in observations})
        label_series = [(d, labels_by_date.get(d, "undefined")) for d in dates]

        by_bucket: Dict[str, Dict[str, List[float]]] = {}
        for o in observations:
            label = labels_by_date.get(o["date"], "undefined")
            if label == "undefined":
                continue
            b = by_bucket.setdefault(label, {"base": [], "exp": []})
            b["base"].append(float(o["baseline_score"]))
            b["exp"].append(float(o["experimental_score"]))

        verdicts: List[Dict[str, Any]] = []
        for bucket, scores in by_bucket.items():
            eff_n = effective_n(label_series, bucket)
            n = len(scores["base"])
            if eff_n < min_effective_n or n < 2:
                verdicts.append({"bucket": bucket, "pvalue": None, "effective_n": eff_n,
                                 "n": n, "low_power": True})
            else:
                p = compute_holdout_pvalue(scores["base"], scores["exp"], alternative)
                verdicts.append({"bucket": bucket, "pvalue": float(p), "effective_n": eff_n,
                                 "n": n, "low_power": False})
        set_attributes(span, n_buckets=len(verdicts))
        return verdicts


def assemble_fdr_family(
    verdicts: List[Dict[str, Any]], fdr_rate: float = 0.10
) -> List[Dict[str, Any]]:
    """Enter every valid-p-value verdict into ONE flat BH family and mark survivors.

    Verdicts without a p-value (low-power/undefined) can never survive (fail-closed).
    Mutates and returns the verdicts with a `survived` flag.
    """
    with create_span("regimes.conditional.fdr_family") as span:
        valid = [v for v in verdicts if v.get("pvalue") is not None]
        pvals = [v["pvalue"] for v in valid]
        mask = apply_fdr(pvals, fdr_rate) if pvals else []
        for v, survived in zip(valid, mask):
            v["survived"] = bool(survived)
        for v in verdicts:
            if v.get("pvalue") is None:
                v["survived"] = False  # fail-closed
        set_attributes(span, family_size=len(pvals),
                       survivors=sum(1 for v in verdicts if v.get("survived")))
        return verdicts
