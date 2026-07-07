"""Tests for regime-conditional experiment verdicts (005 T032).

Per-observation holdout scores + regime labels -> per-regime p-values -> flat BH
family, fail-closed on low-power buckets. Pure computation over synthetic scores.
"""
import datetime as dt

import numpy as np

from gefion.regimes.conditional import conditional_pvalues, assemble_fdr_family


def _dates(n):
    d0 = dt.date(2024, 1, 1)
    return [d0 + dt.timedelta(days=i) for i in range(n)]


def _observations(seed=0):
    """20 days: first 10 'calm' (genuine improvement), next 10 'stressed' (none)."""
    np.random.seed(seed)
    d = _dates(20)
    labels = {d[i]: ("calm" if i < 10 else "stressed") for i in range(20)}
    obs = []
    for i in range(20):
        if i < 10:  # calm — experimental clearly better (lower loss)
            base = 1.0 + 0.05 * np.random.randn()
            exp = 0.5 + 0.05 * np.random.randn()
        else:       # stressed — experimental slightly worse (no improvement)
            base = 1.0 + 0.05 * np.random.randn()
            exp = 1.02 + 0.05 * np.random.randn()
        obs.append({"date": d[i], "baseline_score": base, "experimental_score": exp})
    return obs, labels


def test_improvement_bucket_significant_noise_bucket_not():
    obs, labels = _observations()
    verdicts = conditional_pvalues(obs, labels, alternative="less", min_effective_n=1)
    by_bucket = {v["bucket"]: v for v in verdicts}
    assert by_bucket["calm"]["pvalue"] < 0.05
    assert by_bucket["stressed"]["pvalue"] > 0.05


def test_fail_closed_low_power_bucket_gets_no_pvalue():
    obs, labels = _observations()
    # each bucket is one contiguous episode -> effective_n 1; require 20 -> low power
    verdicts = conditional_pvalues(obs, labels, alternative="less", min_effective_n=20)
    assert all(v["pvalue"] is None and v["low_power"] for v in verdicts)


def test_undefined_days_excluded():
    d = _dates(5)
    obs = [{"date": d[i], "baseline_score": 1.0, "experimental_score": 0.5} for i in range(5)]
    labels = {d[0]: "calm", d[1]: "calm"}  # d[2..4] undefined
    verdicts = conditional_pvalues(obs, labels, alternative="less", min_effective_n=1)
    assert {v["bucket"] for v in verdicts} == {"calm"}


def test_assemble_fdr_family_marks_survivors_and_counts_all():
    # two experiments' worth of verdicts; only strong ones survive
    verdicts = [
        {"bucket": "calm", "pvalue": 0.001, "low_power": False},
        {"bucket": "stressed", "pvalue": 0.9, "low_power": False},
        {"bucket": "calm", "pvalue": 0.004, "low_power": False},
    ]
    out = assemble_fdr_family(verdicts, fdr_rate=0.10)
    assert all("survived" in v for v in out)
    assert sum(v["survived"] for v in out) >= 1
    # the p=0.9 verdict must not survive
    assert not [v for v in out if v["pvalue"] == 0.9][0]["survived"]


def test_no_pvalue_never_survives_fail_closed():
    verdicts = [
        {"bucket": "calm", "pvalue": None, "low_power": True},
        {"bucket": "stressed", "pvalue": None, "low_power": True},
    ]
    out = assemble_fdr_family(verdicts)
    assert not any(v["survived"] for v in out)


def test_empty_family_survives_nothing():
    assert assemble_fdr_family([]) == []
