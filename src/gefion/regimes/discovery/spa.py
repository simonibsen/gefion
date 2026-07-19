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


# ---------------------------------------------------------------------------
# Reconstruction + verification (010 T007/T009) — the honesty core
# ---------------------------------------------------------------------------

# Verification tolerance (research R3): floating-point/library noise only.
_ABS_TOL = 1e-9
_REL_TOL = 1e-6

# Minimum outer-window observations for a meaningful block bootstrap (FR-1006)
MIN_OUTER_OBSERVATIONS = 20


class SpaRefusal(RuntimeError):
    """Honest refusal: no verdict can be produced, with the reason named."""


def _get_run(conn, run):
    from gefion.regimes.discovery import ledger
    return ledger.get_run(conn, run)


def _rebuild_market(conn, run):
    """The run's market view, via the SAME loader the run used (research R1)."""
    import datetime as _dt

    from gefion.regimes.discovery import universe as duniverse
    from gefion.regimes.discovery.signals import load_market_data

    ss = run["search_space"]
    atom_features = sorted({a.get("feature") for a in ss.get("atoms", [])
                            if a.get("feature")})
    signals_list = list(ss.get("signals", []))
    chain_spec = ",".join(ss.get("universe_filter", ["passthrough"]))
    chain = duniverse.parse_filter_chain(chain_spec)
    # Runs stamped with a modeling universe (spec 015) rebuild from that
    # universe's CURRENT membership; unstamped (pre-015) runs keep their
    # legacy population so old re-verdicts reproduce.
    uni_stamp = ss.get("universe")
    symbols = None
    if uni_stamp:
        from gefion.universe import universe_members
        symbols = universe_members(conn, uni_stamp.get("universe_name"))
    if any(f.kind != "passthrough" for f in chain):
        if symbols is None:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM stocks ORDER BY symbol")
                symbols = [r[0] for r in cur.fetchall()]
        symbols = duniverse.apply_chain(chain, symbols, conn=conn)
    max_date = ss.get("max_date")
    return load_market_data(
        conn, sorted(set(signals_list) | set(atom_features)),
        horizon_days=int(ss.get("horizon_days", 1)),
        dataset_version=run["dataset_version"],
        symbols=symbols, optional_features=atom_features,
        max_date=_dt.date.fromisoformat(max_date) if max_date else None)


def _outer_window(run):
    import datetime as _dt
    seg = run["segregation"]
    if run.get("reserve_consumed") and seg.get("reserve"):
        block = seg["reserve"]
        return (_dt.date.fromisoformat(block["start"]),
                _dt.date.fromisoformat(block["end"]))
    return (_dt.date.fromisoformat(seg["holdout_start"]),
            _dt.date.fromisoformat(seg["holdout_end"]))


def _recompute_candidate_tests(cand, ss, src, market, start, end):
    """Recompute one counted candidate's outer tests via the run's own code
    paths (edges.*) — a parallel reimplementation could never certify drift
    vs divergence."""
    from gefion.regimes.discovery import edges, grammar

    tier = cand["tier"]
    if tier == "interaction":
        return [edges.tier1_interaction_test(
                    src, signal=s,
                    conditioning_feature=cand["provenance"]["atom_features"][0],
                    start=start, end=end)
                for s in ss["signals"]]
    if tier == "grammar":
        prov = cand["provenance"]
        spec = grammar.Candidate(
            expression=cand["expression"], bucketing=prov["bucketing"],
            depth=int(prov.get("depth", 1)),
            atom_features=tuple(prov.get("atom_features", ())))
        labels = edges.causal_labels(spec, market,
                                     window=int(ss.get("label_window", 60)))
        tests = []
        for s in ss["signals"]:
            tests.extend(edges.tier2_bucket_tests(
                src, signal=s, labels_by_date=labels, start=start, end=end,
                min_effective_n=int(ss.get("min_effective_n", 20))))
        return tests
    raise SpaRefusal(
        f"candidate {cand['candidate_hash']}: expressive-tier reconstruction "
        "is not supported in v1 (fitted detector state) — see research R2a")


def _unit_series(cand, ss, src, market, start, end, test):
    """Per-observation relative-performance series for one unit (research
    R2a): bucket units use the within-bucket differential records; interaction
    units use the demeaned interaction moment, sign-aligned with the stored
    coefficient."""
    from gefion.regimes.discovery import edges, grammar

    signal = test["signal"]
    if cand["tier"] == "interaction":
        cond_name = cand["provenance"]["atom_features"][0]
        cond = dict(src.series(cond_name))
        fwd = dict(src.market.forward_returns)
        sig = dict(src.series(signal))
        dates = sorted(d for d in sig
                       if d in cond and d in fwd and start <= d <= end)
        if not dates:
            return [], []
        s = np.array([sig[d] for d in dates])
        c = np.array([cond[d] for d in dates])
        r = np.array([fwd[d] for d in dates])
        z = (s - s.mean()) * (c - c.mean()) * r
        sign = 1.0 if float(test.get("coef") or 0.0) >= 0 else -1.0
        return dates, list(sign * z)

    # grammar bucket unit: within-bucket differential records
    prov = cand["provenance"]
    spec = grammar.Candidate(
        expression=cand["expression"], bucketing=prov["bucketing"],
        depth=int(prov.get("depth", 1)),
        atom_features=tuple(prov.get("atom_features", ())))
    labels = edges.causal_labels(spec, market,
                                 window=int(ss.get("label_window", 60)))
    bucket = test.get("bucket")
    records = src.records(signal, start=start, end=end)
    dates, values = [], []
    for rec in records:
        if labels.get(rec["date"]) == bucket:
            dates.append(rec["date"])
            values.append(rec["experimental_score"] - rec["baseline_score"])
    return dates, values


def _match(stored, recomputed, tier):
    """Pair stored outer tests with recomputed ones by (signal[, bucket])."""
    def key(t):
        return (t.get("signal"), t.get("bucket")) if tier != "interaction" \
            else (t.get("signal"),)
    by_key = {key(t): t for t in recomputed}
    return [(t, by_key.get(key(t))) for t in stored]


def reconstruct_family(conn, run, market=None) -> Dict[str, Any]:
    """Rebuild a completed run's counted family and VERIFY it against the
    ledger before any verdict (FR-1004/1005). Read-only. Refuses honestly on
    drift, an empty family, or an unsupported tier.

    `market` (in-run gate, #87): the run's own live MarketData — skips the
    DB rebuild; verification against the just-stored tests then proves
    same-world by construction rather than by luck."""
    from gefion.regimes.discovery import ledger
    from gefion.regimes.discovery.signals import FeatureSignalSource

    run_row = _get_run(conn, run)
    with create_span("discovery.spa.reconstruct", run_id=run_row["id"]) as span:
        if not run_row.get("family_size"):
            raise SpaRefusal(
                f"run {run_row['id']} has family_size "
                f"{run_row.get('family_size')} — nothing to test")

        ss = run_row["search_space"]
        if market is None:
            market = _rebuild_market(conn, run_row)
        src = FeatureSignalSource(market, ss["signals"],
                                  align_window=int(ss.get("align_window", 60)))
        start, end = _outer_window(run_row)

        units, divergent = [], []
        for cand in ledger.list_candidates(conn, run_row["id"]):
            results = cand.get("results") or {}
            if not cand.get("counted_in_family") or not results.get("selected"):
                continue
            stored_tests = [t for t in results.get("tests", [])
                            if t.get("pvalue") is not None]
            if not stored_tests:
                continue
            recomputed = _recompute_candidate_tests(cand, ss, src, market,
                                                    start, end)
            for stored, recomp in _match(stored_tests, recomputed, cand["tier"]):
                if recomp is None or recomp.get("pvalue") is None:
                    divergent.append((cand["candidate_hash"], stored.get("signal"),
                                      stored["pvalue"], None))
                    continue
                diff = abs(recomp["pvalue"] - stored["pvalue"])
                if diff > max(_ABS_TOL, _REL_TOL * abs(stored["pvalue"])):
                    divergent.append((cand["candidate_hash"], stored.get("signal"),
                                      stored["pvalue"], recomp["pvalue"]))
                    continue
                dates, values = _unit_series(cand, ss, src, market, start, end,
                                             {**stored, **recomp})
                units.append({
                    "candidate_hash": cand["candidate_hash"],
                    "tier": cand["tier"],
                    "signal": stored.get("signal"),
                    "bucket": stored.get("bucket"),
                    "stored_pvalue": stored["pvalue"],
                    "recomputed_pvalue": recomp["pvalue"],
                    "dates": dates,
                    "values": values,
                })

        if divergent:
            details = "; ".join(
                f"{h} [{s}] stored p={sp:.6g} recomputed "
                f"p={'MISSING' if rp is None else format(rp, '.6g')}"
                for h, s, sp, rp in divergent[:5])
            raise SpaRefusal(
                f"reconstruction mismatch — {len(divergent)} unit(s) diverge "
                f"beyond tolerance: {details}. The world has drifted since the "
                "run (price backfill or environment change); no verdict can "
                "honestly be produced.")
        if not units:
            raise SpaRefusal(f"run {run_row['id']}: no counted units survived "
                             "reconstruction — nothing to test")

        max_div = max(abs(u["recomputed_pvalue"] - u["stored_pvalue"])
                      for u in units)
        set_attributes(span, n_units=len(units), max_divergence=max_div)
        return {"run": run_row, "units": units, "family_size": len(units),
                "outer_window": (str(start), str(end)),
                "verification": {"units": len(units),
                                 "max_abs_divergence": max_div,
                                 "all_match": True}}


def reverdict(conn, run, iterations: int = 1000,
              seed: Optional[int] = None, level: Optional[float] = None,
              block_length: Optional[float] = None,
              market=None) -> Dict[str, Any]:
    """The post-run SPA re-verdict (FR-1001..1006): reconstruct → verify →
    joint stationary bootstrap → Hansen SPA. Read-only over ledger and market
    rows; recording is the caller's step. With `market`, runs against the
    caller's live MarketData (the in-run gate)."""
    with create_span("discovery.spa.reverdict", run=str(run),
                     iterations=iterations) as span:
        fam = reconstruct_family(conn, run, market=market)
        run_row = fam["run"]
        set_attributes(span, run_id=run_row["id"])
        all_dates = sorted({d for u in fam["units"] for d in u["dates"]})
        n = len(all_dates)
        if n < MIN_OUTER_OBSERVATIONS:
            raise SpaRefusal(
                f"outer window has {n} observation(s) — below the "
                f"{MIN_OUTER_OBSERVATIONS}-observation floor for a block "
                "bootstrap")
        date_index = {d: i for i, d in enumerate(all_dates)}
        units = fam["units"]
        d_matrix = np.zeros((len(units), n))
        mask = np.zeros((len(units), n), dtype=bool)
        for k, u in enumerate(units):
            for d, v in zip(u["dates"], u["values"]):
                j = date_index[d]
                d_matrix[k, j] = v
                mask[k, j] = True

        use_seed = int(seed if seed is not None else run_row["seed"])
        result = spa_test(d_matrix, iterations=iterations, seed=use_seed,
                          mask=mask, expected_block=block_length)
        lvl = float(level if level is not None
                    else run_row["search_space"].get("fdr_rate", 0.01))
        # passed means SUPPORTED (research R9): a small p rejects SPA's null
        # ("the best-looking candidate is explainable by search luck"), so
        # the family's best survives the search-aware test.
        out = {
            **result,
            "run_id": run_row["id"],
            "run_name": run_row["name"],
            "level": lvl,
            "passed": result["p_consistent"] <= lvl,
            "outer_window": fam["outer_window"],
            "verification": fam["verification"],
        }
        set_attributes(span, p_consistent=out["p_consistent"],
                       passed=out["passed"], family_size=out["family_size"])
        return out
