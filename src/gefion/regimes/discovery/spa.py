"""SPA re-verdict statistical core (010, T003/T005 — Foundational).

The selection-aware question BH does not ask: is the BEST of everything the
search tried distinguishable from the best that searching pure noise would
produce? Hansen's SPA (2005) answers it with a studentized max statistic and
a stationary-bootstrap null; the Politis–White rule picks the expected block
length automatically. All three routines are deterministic under a seed —
identical inputs + seed produce byte-identical p-values.

Implemented directly in numpy (no new dependency): the algorithms are short
and exactly specified; the recipes live in specs/010-spa-reverdict/research.md.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from gefion.observability import create_span, set_attributes


# ---------------------------------------------------------------------------
# Stationary bootstrap (Politis–Romano 1994)
# ---------------------------------------------------------------------------

def stationary_bootstrap_indices(n: int, expected_block: float,
                                 iterations: int,
                                 rng: np.random.Generator) -> np.ndarray:
    """Joint resampling index paths, shape (iterations, n).

    Each path: start uniform; at every step continue the block
    (idx+1 mod n, wrap-around) with probability 1 − 1/L, else restart
    uniformly. ONE path is applied to every unit — that joint application is
    what preserves cross-candidate dependence.
    """
    L = max(1.0, float(expected_block))
    p_restart = 1.0 / L
    idx = np.empty((iterations, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, size=iterations)
    restarts = rng.random((iterations, n)) < p_restart
    fresh = rng.integers(0, n, size=(iterations, n))
    for t in range(1, n):
        cont = (idx[:, t - 1] + 1) % n
        idx[:, t] = np.where(restarts[:, t], fresh[:, t], cont)
    return idx


# ---------------------------------------------------------------------------
# Politis–White (2004; Patton correction 2009) automatic block length
# ---------------------------------------------------------------------------

def _flat_top(x: np.ndarray) -> np.ndarray:
    """Trapezoidal (flat-top) lag window: 1 on [0, .5], linear to 0 at 1."""
    w = np.zeros_like(x)
    w[np.abs(x) <= 0.5] = 1.0
    mid = (np.abs(x) > 0.5) & (np.abs(x) <= 1.0)
    w[mid] = 2.0 * (1.0 - np.abs(x[mid]))
    return w


def politis_white_block_length(series: np.ndarray) -> float:
    """Automatic expected block length for the stationary bootstrap.

    Floored at 1 and capped at n/3 (short-window guard). Computed on one
    series — for the joint family resampling, the cross-unit mean series
    (research R4).
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    if n < 10:
        return 1.0
    x = x - x.mean()

    # Significant-lag rule for the truncation point m_hat
    max_lag = max(5, int(math.ceil(math.sqrt(math.log10(n)))))
    check_lags = min(n - 2, int(math.ceil(math.sqrt(n))) + max_lag)
    denom = float(np.dot(x, x))
    if denom <= 0:
        return 1.0
    rho = np.array([np.dot(x[:-k], x[k:]) / denom
                    for k in range(1, check_lags + 1)])
    threshold = 2.0 * math.sqrt(math.log10(n) / n)
    m_hat = 0
    for k in range(len(rho)):
        window = rho[k:k + max_lag]
        if np.all(np.abs(window) < threshold):
            m_hat = k
            break
    else:
        m_hat = len(rho)
    M = min(2 * m_hat, check_lags)

    # Flat-top-weighted spectral quantities
    gamma = np.array([np.dot(x[:-k], x[k:]) / n if k > 0 else np.dot(x, x) / n
                      for k in range(0, M + 1)])
    if M == 0:
        return 1.0
    lags = np.arange(0, M + 1)
    w = _flat_top(lags / max(M, 1))
    g0 = gamma[0] + 2.0 * float(np.sum(w[1:] * gamma[1:]))       # long-run var
    G = 2.0 * float(np.sum(w[1:] * lags[1:] * gamma[1:]))
    D_sb = 2.0 * g0 ** 2
    if D_sb <= 0 or abs(G) < 1e-12:
        return 1.0
    b = ((2.0 * G ** 2) / D_sb) ** (1.0 / 3.0) * n ** (1.0 / 3.0)
    return float(min(max(1.0, b), n / 3.0))


# ---------------------------------------------------------------------------
# Hansen SPA (2005)
# ---------------------------------------------------------------------------

def _hac_omega(x: np.ndarray, expected_block: float) -> float:
    """Hansen's kernel variance of the mean, consistent with the stationary
    bootstrap: ω² = γ₀ + 2 Σ κ(i) γᵢ with
    κ(i) = ((n−i)/n)(1−1/L)^i + (i/n)(1−1/L)^{n−i}."""
    n = x.size
    if n < 2:
        return float(np.var(x)) if n else 1.0
    xc = x - x.mean()
    q = 1.0 - 1.0 / max(1.0, expected_block)
    omega = float(np.dot(xc, xc)) / n
    max_lag = n - 1
    for i in range(1, max_lag + 1):
        kappa = ((n - i) / n) * q ** i + (i / n) * q ** (n - i)
        if kappa < 1e-12:
            break
        gamma_i = float(np.dot(xc[:-i], xc[i:])) / n
        omega += 2.0 * kappa * gamma_i
    return max(omega, 1e-12)


def spa_test(d: np.ndarray, iterations: int = 1000, seed: int = 0,
             mask: Optional[np.ndarray] = None,
             expected_block: Optional[float] = None) -> Dict[str, Any]:
    """Hansen SPA over a unit × time relative-performance matrix.

    H0: no unit beats the benchmark (max_k E[d_k] ≤ 0). Statistic: the
    studentized max, floored at 0. Null: joint stationary-bootstrap resamples
    with Hansen's three recenterings —
      p_lower      μ̂ = max(d̄, 0)      (most aggressive null)
      p_consistent μ̂ = d̄ · 1{d̄ ≥ −√(ω̂²/n · 2 log log n)}   (the verdict)
      p_upper      μ̂ = d̄               (all units centered; RC-like, most conservative)
    so p_lower ≤ p_consistent ≤ p_upper by construction.

    `mask` (same shape, True = valid) supports units with missing dates.
    Deterministic under `seed` (PCG64).
    """
    d = np.asarray(d, dtype=float)
    if d.ndim != 2:
        raise ValueError("d must be units × time")
    units, n = d.shape
    if mask is None:
        mask = np.ones_like(d, dtype=bool)
    mask = np.asarray(mask, dtype=bool)

    with create_span("discovery.spa.test", units=units, n=n,
                     iterations=iterations) as span:
        counts = mask.sum(axis=1).astype(float)
        if np.any(counts < 2):
            raise ValueError("every unit needs at least 2 observations")
        sums = np.where(mask, d, 0.0).sum(axis=1)
        means = sums / counts

        L = expected_block if expected_block is not None else \
            politis_white_block_length(_cross_unit_mean(d, mask))
        L = max(1.0, float(L))

        omegas = np.array([_hac_omega(d[k][mask[k]], L) for k in range(units)])
        scales = np.sqrt(omegas / counts)                  # sd of each mean

        t_stat = float(max(0.0, np.max(means / scales)))

        # Hansen recenterings
        loglog = math.sqrt(2.0 * math.log(math.log(max(n, 3))))
        a_k = scales * loglog
        mu_lower = np.maximum(means, 0.0)
        mu_consistent = np.where(means >= -a_k, means, 0.0)
        mu_upper = means

        rng = np.random.default_rng(seed)
        idx = stationary_bootstrap_indices(n, L, iterations, rng)

        # Resampled means per unit per iteration (mask-aware)
        d_filled = np.where(mask, d, 0.0)
        m_float = mask.astype(float)
        boot_means = np.empty((iterations, units))
        boot_counts = np.empty((iterations, units))
        for b in range(iterations):
            take = idx[b]
            boot_means[b] = d_filled[:, take].sum(axis=1)
            boot_counts[b] = m_float[:, take].sum(axis=1)
        boot_counts = np.maximum(boot_counts, 1.0)
        boot_means = boot_means / boot_counts

        def p_value(mu: np.ndarray) -> float:
            z = (boot_means - mu[None, :]) / scales[None, :]
            t_star = np.maximum(z.max(axis=1), 0.0)
            return float((t_star >= t_stat).mean())

        result = {
            "p_lower": p_value(mu_lower),
            "p_consistent": p_value(mu_consistent),
            "p_upper": p_value(mu_upper),
            "statistic": t_stat,
            "family_size": units,
            "iterations": iterations,
            "seed": seed,
            "block_length": L,
        }
        set_attributes(span, **{k: v for k, v in result.items()
                                if isinstance(v, (int, float))})
        return result


def _cross_unit_mean(d: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Per-date mean across units (mask-aware) — the series the automatic
    block length is computed on (research R4)."""
    counts = np.maximum(mask.sum(axis=0), 1)
    return np.where(mask, d, 0.0).sum(axis=0) / counts
