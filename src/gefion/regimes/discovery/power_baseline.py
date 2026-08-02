"""NASDAQ discovery-power baseline harness (issue #180, phase 1 of #179).

Measures how the discovery gate's *admission power* scales with universe size —
a power(effective-N) curve over a sector-stratified N-sweep — so a future
exchange's contribution can be judged *before* committing to a days-long ingest.

The methodology is decided (discovery admission):

- **X-axis, effective-N.** Per subsample, the cross-section's effective
  independent-N, correlation-discounted (``effective_cross_section_n``), NOT the
  raw symbol count. This is the spec-005 independence-adjustment PRINCIPLE
  ("effective N, not raw count") carried from the time axis (005's episode count)
  to the symbol cross-section: correlated names add little, so we discount by the
  average pairwise return correlation. It is also exactly the effective sample
  size that governs the noise of the cross-sectional aggregate the discovery gate
  actually consumes (market-median features + market-mean forward returns), which
  is *why* power scales with it.

- **Y-axis, admitted-edge power.** For each subsample the existing discovery +
  SPA gate (``run_discovery``) is run over a **fixed candidate battery** — the
  same atoms/depth/budget/tiers, so the enumerated candidate set is byte-identical
  and only the symbol set (hence the market aggregate) varies. We record the
  admitted-edge count at the standard gate, the outer BH p-values, and the in-run
  SPA family p-value.

- **Sweep.** Sector-stratified subsamples at several fractions, multiple seeded
  draws per fraction, with across-draw dispersion reported (one draw is noisy).

No new DDL: each draw is a real discovery run in the ``regime_discovery_runs``
ledger (auditable); the harness returns a JSON-serialisable report and never
creates a result table of its own.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from gefion.observability import create_span, set_attributes
from gefion.regimes.discovery import ledger
from gefion.regimes.discovery.runner import DiscoveryConfig, run_discovery
from gefion.regimes.discovery.segregation import MarketData

Series = Sequence[Tuple[datetime.date, float]]

DEFAULT_FRACTIONS: Tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
DEFAULT_DRAWS = 3
# Correlation-estimation window for the real (DB) path: a couple of trading
# years is stationary enough for a stable effective-N and keeps the query bounded.
DEFAULT_CORRELATION_WINDOW_DAYS = 504


class PowerBaselineError(ValueError):
    """Raised on an invalid power-baseline configuration or a contaminated run
    (e.g. the candidate battery drifted across subsamples)."""


# --- sector-stratified subsampling -------------------------------------------

def sector_stratified_subsample(
    members: Sequence[str], sector_of: Dict[str, str], fraction: float, seed: int,
) -> List[str]:
    """A sector-stratified subsample at ``fraction`` of ``members``.

    Preserves the sector mix (per sector, round(fraction x count) symbols) so the
    measured variable is N, not composition drift. Deterministic under ``seed``.
    ``fraction`` is clamped to [0, 1]; a symbol with no sector falls in a
    reserved 'UNKNOWN' stratum so it is still represented.
    """
    fraction = max(0.0, min(1.0, float(fraction)))
    by_sector: Dict[str, List[str]] = {}
    for sym in members:
        by_sector.setdefault(sector_of.get(sym) or "UNKNOWN", []).append(sym)
    rng = random.Random(seed)
    chosen: List[str] = []
    for sector in sorted(by_sector):
        syms = sorted(by_sector[sector])
        k = int(round(fraction * len(syms)))
        if k <= 0:
            continue
        picked = syms[:]
        rng.shuffle(picked)
        chosen.extend(picked[:k])
    return sorted(chosen)


# --- cross-sectional effective-N ---------------------------------------------

def _aligned_matrix(returns_by_symbol: Dict[str, Series]) -> np.ndarray:
    """Symbols x common-dates return matrix (complete cases only)."""
    per_symbol = {s: dict(v) for s, v in returns_by_symbol.items() if v}
    if not per_symbol:
        return np.empty((0, 0))
    common = set.intersection(*(set(d) for d in per_symbol.values()))
    dates = sorted(common)
    if not dates:
        return np.empty((len(per_symbol), 0))
    return np.array([[per_symbol[s][d] for d in dates]
                     for s in sorted(per_symbol)], dtype=float)


def mean_pairwise_correlation(returns_by_symbol: Dict[str, Series]) -> float:
    """Average off-diagonal Pearson correlation across the symbol cross-section.

    Vectorised via the standardised-row identity (no N x N matrix, so it scales
    to thousands of symbols): standardise each symbol's return vector, then
    mean_{i!=j} corr_ij = (||sum_i z_i||^2 / T - N) / (N(N-1)). Symbols with zero
    return variance over the window are dropped (undefined correlation).
    """
    mat = _aligned_matrix(returns_by_symbol)
    if mat.shape[0] < 2 or mat.shape[1] < 2:
        return 0.0
    means = mat.mean(axis=1, keepdims=True)
    stds = mat.std(axis=1, keepdims=True)
    keep = stds[:, 0] > 0
    mat, means, stds = mat[keep], means[keep], stds[keep]
    n, t = mat.shape
    if n < 2:
        return 0.0
    z = (mat - means) / stds                     # rows: mean 0, ||z_i||^2 = t
    colsum = z.sum(axis=0)
    off_diag_sum = (float(colsum @ colsum) / t - n) / (n * (n - 1))
    return off_diag_sum


def effective_cross_section_n(returns_by_symbol: Dict[str, Series]) -> float:
    """Correlation-discounted effective independent-N of the symbol cross-section.

    ``N_eff = N / (1 + (N-1) * rho_bar)`` with ``rho_bar`` the average pairwise
    return correlation (clamped to [0, 1)). This is Kish's effective sample size —
    equivalently the effective N that sets the variance of the cross-sectional
    mean the discovery gate consumes — so it is the right X-axis for admission
    power. Clamped to [1, N]. A single symbol has effective-N 1.
    """
    n = sum(1 for v in returns_by_symbol.values() if v)
    if n <= 1:
        return float(n)
    rho = mean_pairwise_correlation(returns_by_symbol)
    rho = max(0.0, min(0.999999, rho))
    eff = n / (1.0 + (n - 1) * rho)
    return float(max(1.0, min(float(n), eff)))


# --- fixed candidate battery -------------------------------------------------

def battery_fingerprint(candidate_hashes: Sequence[str]) -> str:
    """Order-independent fingerprint of a candidate set — identical across two
    subsamples iff they enumerated exactly the same battery."""
    joined = "\n".join(sorted(set(candidate_hashes)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --- summary statistics, curve, marginal power -------------------------------

def summarize(values: Sequence[float]) -> Dict[str, Any]:
    """Mean/std/min/max plus the raw values (across-draw dispersion)."""
    vals = [float(v) for v in values]
    if not vals:
        return {"mean": None, "std": None, "min": None, "max": None, "values": []}
    arr = np.array(vals, dtype=float)
    return {"mean": float(arr.mean()), "std": float(arr.std(ddof=0)),
            "min": float(arr.min()), "max": float(arr.max()), "values": vals}


def assemble_curve(per_fraction: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One point per fraction, sorted by mean effective-N, carrying the
    across-draw dispersion of the admitted count."""
    points = []
    for fr in per_fraction:
        points.append({
            "fraction": fr["fraction"],
            "effective_n": fr["effective_n"]["mean"],
            "effective_n_std": fr["effective_n"]["std"],
            "admitted": fr["admitted"]["mean"],
            "admitted_std": fr["admitted"]["std"],
            "n_symbols": fr["n_symbols"]["mean"],
        })
    return sorted(points, key=lambda p: (p["effective_n"] if p["effective_n"]
                                         is not None else 0.0))


def marginal_admission_power(curve: List[Dict[str, Any]]) -> Optional[float]:
    """Admitted-edges gained per added effective-N at the frontier — the finite
    difference between the two largest-effective-N points (the predictor for
    what another exchange would buy). None when it cannot be formed."""
    pts = [p for p in curve if p["effective_n"] is not None
           and p["admitted"] is not None]
    if len(pts) < 2:
        return None
    a, b = pts[-2], pts[-1]
    d_eff = b["effective_n"] - a["effective_n"]
    if d_eff <= 0:
        return None
    return float((b["admitted"] - a["admitted"]) / d_eff)


# --- configuration -----------------------------------------------------------

@dataclasses.dataclass
class PowerBaselineConfig:
    """The sweep + the fixed candidate battery. The battery fields (atoms,
    signals, depth, budget, tiers, signal_source, horizon, holdout,
    min_effective_n) are held constant across every subsample."""

    name: str
    atoms: List[Dict[str, Any]]
    signals: List[str]
    fractions: Tuple[float, ...] = DEFAULT_FRACTIONS
    draws_per_fraction: int = DEFAULT_DRAWS
    seed: int = 42
    depth: int = 2
    budget: int = 100
    tiers: Tuple[str, ...] = ("grammar",)
    signal_source: str = "features"
    grading_scheme: str = "walk_forward"
    horizon_days: int = 1
    holdout_weeks: int = 6
    min_effective_n: int = 20
    universe: Optional[str] = None
    dataset_version: str = "dev"
    correlation_window_days: int = DEFAULT_CORRELATION_WINDOW_DAYS

    def validate(self) -> None:
        if not self.name:
            raise PowerBaselineError("a run name is required")
        if not self.atoms:
            raise PowerBaselineError(
                "a non-empty atom battery is required (the battery is the fixed "
                "variable of the experiment)")
        if not self.signals:
            raise PowerBaselineError("a non-empty signal list is required")
        if not self.fractions:
            raise PowerBaselineError("at least one sweep fraction is required")
        if any(not (0.0 < f <= 1.0) for f in self.fractions):
            raise PowerBaselineError(
                f"fractions must each lie in (0, 1]: {self.fractions}")
        if self.draws_per_fraction < 1:
            raise PowerBaselineError("draws_per_fraction must be >= 1")


def _draw_seed(base_seed: int, fraction: float, draw: int) -> int:
    """A distinct, deterministic seed per (fraction, draw)."""
    return (int(base_seed) * 1_000_003 + int(round(fraction * 1000)) * 101
            + draw) % (2 ** 31 - 1)


# --- the harness core --------------------------------------------------------

def run_power_baseline(
    conn,
    config: PowerBaselineConfig,
    *,
    members: Sequence[str],
    sector_of: Dict[str, str],
    market_for_subsample: Callable[[Sequence[str]], MarketData],
    returns_for_subsample: Callable[[Sequence[str]], Dict[str, Series]],
    max_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Run the sector-stratified N-sweep and assemble the power(effective-N)
    report. Data access is injected (``market_for_subsample`` /
    ``returns_for_subsample``) so the real DB path and the synthetic tests share
    one honest core; each draw is evaluated by the real ``run_discovery`` gate.
    """
    config.validate()
    atom_features = sorted({a.get("feature") for a in config.atoms
                            if a.get("feature")})
    with create_span("discovery.power_baseline.run", run_name=config.name,
                     fractions=len(config.fractions),
                     draws=config.draws_per_fraction) as span:
        per_fraction: List[Dict[str, Any]] = []
        battery_fp: Optional[str] = None
        n_candidates = 0

        for fraction in config.fractions:
            with create_span("discovery.power_baseline.fraction",
                             fraction=fraction) as fspan:
                draws: List[Dict[str, Any]] = []
                for draw in range(config.draws_per_fraction):
                    seed = _draw_seed(config.seed, fraction, draw)
                    subsample = sector_stratified_subsample(
                        members, sector_of, fraction, seed)
                    if not subsample:
                        raise PowerBaselineError(
                            f"fraction {fraction} drew an empty subsample from "
                            f"{len(members)} members — raise the fraction")
                    returns = returns_for_subsample(subsample)
                    eff_n = effective_cross_section_n(returns)
                    rho = mean_pairwise_correlation(returns)
                    market = market_for_subsample(subsample)

                    pct = int(round(fraction * 100))
                    run_name = f"{config.name}-f{pct:03d}-d{draw}"
                    dcfg = DiscoveryConfig(
                        name=run_name, seed=seed, atoms=config.atoms,
                        signals=config.signals, depth=config.depth,
                        budget=config.budget, tiers=tuple(config.tiers),
                        signal_source=config.signal_source,
                        grading_scheme=config.grading_scheme,
                        universe_filter="passthrough",
                        horizon_days=config.horizon_days,
                        holdout_weeks=config.holdout_weeks,
                        min_effective_n=config.min_effective_n,
                        dataset_version=config.dataset_version,
                        max_date=max_date)
                    summary = run_discovery(conn, dcfg, market)

                    cands = ledger.list_candidates(conn, summary["run_id"])
                    hashes = [c["candidate_hash"] for c in cands]
                    fp = battery_fingerprint(hashes)
                    if battery_fp is None:
                        battery_fp, n_candidates = fp, len(hashes)
                    elif fp != battery_fp:
                        raise PowerBaselineError(
                            f"candidate battery drifted at fraction {fraction} "
                            f"draw {draw} (run {summary['run_id']}): the measured "
                            "power would not be attributable to N alone — every "
                            "subsample must enumerate an identical battery")
                    outer_pvalues = [
                        t["pvalue"] for c in cands
                        for t in (c.get("results") or {}).get("tests", [])
                        if t.get("pvalue") is not None]
                    spa_p = (summary["spa"]["p_consistent"]
                             if summary.get("spa") else None)

                    draws.append({
                        "draw": draw, "seed": seed, "run_id": summary["run_id"],
                        "n_symbols": len(subsample), "effective_n": eff_n,
                        "mean_correlation": rho,
                        "n_admitted": summary["n_admitted"],
                        "family_size": summary["family_size"],
                        "spa_p_consistent": spa_p,
                        "outer_pvalues": outer_pvalues,
                        "battery_fingerprint": fp,
                    })

                spa_vals = [d["spa_p_consistent"] for d in draws
                            if d["spa_p_consistent"] is not None]
                fr_result = {
                    "fraction": fraction,
                    "effective_n": summarize([d["effective_n"] for d in draws]),
                    "admitted": summarize([d["n_admitted"] for d in draws]),
                    "n_symbols": summarize([d["n_symbols"] for d in draws]),
                    "mean_correlation": summarize(
                        [d["mean_correlation"] for d in draws]),
                    "spa_p": summarize(spa_vals),
                    "draws": draws,
                }
                per_fraction.append(fr_result)
                set_attributes(fspan,
                               effective_n_mean=fr_result["effective_n"]["mean"],
                               admitted_mean=fr_result["admitted"]["mean"])

        curve = assemble_curve(per_fraction)
        marginal = marginal_admission_power(curve)
        report = {
            "name": config.name,
            "created_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "dataset_version": config.dataset_version,
            "universe": config.universe,
            "max_date": str(max_date) if max_date else None,
            "config": {
                "fractions": list(config.fractions),
                "draws_per_fraction": config.draws_per_fraction,
                "seed": config.seed,
                "correlation_window_days": config.correlation_window_days,
            },
            "battery": {
                "atoms": config.atoms,
                "signals": config.signals,
                "depth": config.depth,
                "budget": config.budget,
                "tiers": list(config.tiers),
                "signal_source": config.signal_source,
                "atom_features": atom_features,
                "fingerprint": battery_fp,
                "n_candidates": n_candidates,
                "fixed": True,
            },
            "fractions": per_fraction,
            "curve": curve,
            "marginal_admission_power": marginal,
        }
        set_attributes(span, n_candidates=n_candidates,
                       marginal_admission_power=marginal if marginal is not None
                       else -1.0)
        return report


# --- real (DB) data access ---------------------------------------------------

def _db_members_and_sectors(conn, universe: Optional[str]) -> Tuple[
        List[str], Dict[str, str]]:
    from gefion.universe import universe_members
    members = universe_members(conn, universe)
    sector_of: Dict[str, str] = {}
    if members:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, sector FROM stocks WHERE symbol = ANY(%s)",
                (members,))
            sector_of = {row[0]: (row[1] or "UNKNOWN") for row in cur.fetchall()}
    return members, sector_of


def _db_returns_for_subsample(conn, subsample: Sequence[str],
                              window_days: int,
                              max_date: Optional[datetime.date]) -> Dict[str, Series]:
    """Per-symbol close-to-close forward returns over a bounded trailing window —
    the correlation panel behind the cross-sectional effective-N."""
    end = max_date or datetime.date.today()
    start = end - datetime.timedelta(days=int(window_days * 1.6) + 10)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.symbol, o.date, o.close
               FROM stock_ohlcv o JOIN stocks s ON s.id = o.data_id
               WHERE s.symbol = ANY(%s) AND o.date > %s AND o.date <= %s
                 AND o.close IS NOT NULL
               ORDER BY s.symbol, o.date""",
            (list(subsample), start, end))
        rows = cur.fetchall()
    closes: Dict[str, List[Tuple[datetime.date, float]]] = {}
    for symbol, d, close in rows:
        closes.setdefault(symbol, []).append((d, float(close)))
    out: Dict[str, Series] = {}
    for symbol, series in closes.items():
        rets = [(series[i][0], series[i][1] / series[i - 1][1] - 1.0)
                for i in range(1, len(series)) if series[i - 1][1]]
        if rets:
            out[symbol] = rets
    return out


def run_power_baseline_db(conn, config: PowerBaselineConfig,
                          max_date: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Real-data entry point: resolve the modeling universe (015), read sectors
    off ``stocks``, load the market aggregate per subsample via the same loader a
    discovery run uses, and run the sweep through the real gate."""
    from gefion.regimes.discovery.signals import load_market_data

    with create_span("discovery.power_baseline.db", run_name=config.name) as span:
        members, sector_of = _db_members_and_sectors(conn, config.universe)
        if len(members) < 2:
            raise PowerBaselineError(
                f"universe {config.universe or 'default'!r} has {len(members)} "
                "member(s) — nothing to sweep")
        atom_features = sorted({a.get("feature") for a in config.atoms
                                if a.get("feature")})
        load_features = sorted(set(config.signals) | set(atom_features))

        def market_for_subsample(subsample: Sequence[str]) -> MarketData:
            return load_market_data(
                conn, load_features, horizon_days=config.horizon_days,
                dataset_version=config.dataset_version, symbols=list(subsample),
                optional_features=atom_features, max_date=max_date)

        def returns_for_subsample(subsample: Sequence[str]) -> Dict[str, Series]:
            return _db_returns_for_subsample(
                conn, subsample, config.correlation_window_days, max_date)

        set_attributes(span, members=len(members))
        return run_power_baseline(
            conn, config, members=members, sector_of=sector_of,
            market_for_subsample=market_for_subsample,
            returns_for_subsample=returns_for_subsample, max_date=max_date)
