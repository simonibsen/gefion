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

import collections
import dataclasses
import datetime
import hashlib
import math
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
# Ragged-alignment thresholds (issue #183). Real symbol histories are ragged
# (staggered IPO/delisting, interior gaps), so the complete-case intersection of
# EVERY symbol's dates collapses to empty; these bound how the survivor sub-panel
# is carved out. A symbol must cover >= COVERAGE of the recent window to survive;
# a date must be shared by >= COVERAGE of the survivors to enter the panel; the
# dense complete-case panel must have >= MIN_ALIGNED_DATES columns to be trusted.
DEFAULT_ALIGN_WINDOW_DATES = 504
DEFAULT_MIN_SYMBOL_COVERAGE = 0.8
DEFAULT_MIN_DATE_COVERAGE = 0.8
DEFAULT_MIN_ALIGNED_DATES = 20


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

@dataclasses.dataclass
class CrossSectionAlignment:
    """A ragged symbol cross-section aligned to a dense complete-case panel.

    ``matrix`` is the ``(n_symbols_kept, n_dates)`` complete return panel the
    vectorised correlation identity requires. The diagnostics record what the
    ragged-tail pruning kept, so a panel that is genuinely too thin to estimate a
    correlation is a *measured* outcome (``thin=True``, NaN correlation), never a
    silent zero that would masquerade as 'uncorrelated' (issue #183).
    """

    matrix: np.ndarray
    n_symbols_in: int          # symbols with any returns (the raw cross-section)
    n_symbols_kept: int        # survivors in the dense complete-case panel
    n_dates: int               # dense complete-case dates
    thin: bool                 # too thin to estimate a cross-sectional correlation
    mean_correlation: float    # average off-diagonal Pearson rho; NaN when thin
    effective_n: float         # Kish N_eff over n_symbols_in; NaN when thin


def _correlation_from_dense_matrix(mat: np.ndarray) -> float:
    """Average off-diagonal Pearson correlation of a dense complete ``(N, T)``
    panel, vectorised via the standardised-row identity (no N x N matrix, so it
    scales to thousands of symbols): standardise each row, then
    ``mean_{i!=j} corr_ij = (||sum_i z_i||^2 / T - N) / (N(N-1))``. Symbols with
    zero return variance are dropped (undefined correlation). Returns NaN when
    fewer than two varying rows or two dates remain."""
    if mat.shape[0] < 2 or mat.shape[1] < 2:
        return float("nan")
    means = mat.mean(axis=1, keepdims=True)
    stds = mat.std(axis=1, keepdims=True)
    keep = stds[:, 0] > 0
    mat, means, stds = mat[keep], means[keep], stds[keep]
    n, t = mat.shape
    if n < 2:
        return float("nan")
    z = (mat - means) / stds                     # rows: mean 0, ||z_i||^2 = t
    colsum = z.sum(axis=0)
    return (float(colsum @ colsum) / t - n) / (n * (n - 1))


def _complete_case_panel(
    survivors: Dict[str, Dict[datetime.date, float]],
    min_date_coverage: float,
    min_dates: int,
) -> Tuple[List[str], List[datetime.date]]:
    """Carve a dense complete-case ``(symbols, dates)`` panel out of the ragged
    survivors. Keep the dates shared by >= ``min_date_coverage`` of the survivors,
    then take the dates every survivor covers. Scattered interior gaps (trading
    halts) would otherwise thin that strict intersection, so the worst-covered
    survivor is dropped one at a time until the common panel is dense enough
    (>= ``min_dates``). On clean data (no interior gaps) the first pass already
    succeeds, so this is a single O(N*T) sweep. Deterministic — ties break on the
    symbol name."""
    syms = sorted(survivors)
    if len(syms) < 2:
        return syms, []
    date_counts: "collections.Counter[datetime.date]" = collections.Counter()
    for s in syms:
        date_counts.update(survivors[s].keys())
    threshold = min_date_coverage * len(syms)
    candidate = sorted(d for d, c in date_counts.items() if c >= threshold)
    cand_set = set(candidate)
    presence = {s: set(survivors[s].keys()) & cand_set for s in syms}
    cur = list(syms)
    while len(cur) >= 2:
        common = [d for d in candidate if all(d in presence[s] for s in cur)]
        if len(common) >= min_dates:
            return cur, common
        worst = max(cur, key=lambda s: (len(cand_set) - len(presence[s]), s))
        cur.remove(worst)
    return cur, []


def align_cross_section(
    returns_by_symbol: Dict[str, Series],
    *,
    window: int = DEFAULT_ALIGN_WINDOW_DATES,
    min_symbol_coverage: float = DEFAULT_MIN_SYMBOL_COVERAGE,
    min_date_coverage: float = DEFAULT_MIN_DATE_COVERAGE,
    min_dates: int = DEFAULT_MIN_ALIGNED_DATES,
) -> CrossSectionAlignment:
    """Align a ragged real cross-section to a dense complete-case return panel.

    Real symbol histories are ragged — recent IPOs, delistings and interior gaps —
    so the complete-case intersection of EVERY symbol's dates collapses to empty
    and the effective-N degenerates to the raw count (issue #183). This instead:

    1. restricts to a recent dense window (the most recent ``window`` dates);
    2. drops symbols whose coverage of that window is below ``min_symbol_coverage``
       (the ragged tail: recent IPOs, early delistings, heavily-gapped names);
    3. keeps the dates that at least ``min_date_coverage`` of the survivors share;
    4. takes the complete-case panel on the survivors — non-empty now that the
       ragged tail is gone — and computes the correlation-discounted effective-N.

    ``N_eff = N / (1 + (N-1) * rho_bar)`` (Kish's effective sample size), with
    ``rho_bar`` the average pairwise return correlation clamped to ``[0, 1)`` and
    ``N`` the raw cross-section size, clamped to ``[1, N]``. If the survivor panel
    is still too thin (< 2 symbols or < ``min_dates`` dates), that is surfaced
    honestly (``thin=True``, NaN correlation and effective-N) rather than being
    reported as an uncorrelated cross-section.
    """
    with create_span("discovery.power_baseline.align_cross_section") as span:
        per_symbol = {s: dict(v) for s, v in returns_by_symbol.items() if v}
        n_in = len(per_symbol)

        def _thin(matrix: np.ndarray, kept: int, n_dates: int) -> CrossSectionAlignment:
            set_attributes(span, n_symbols_in=n_in, n_symbols_kept=kept,
                           n_dates=n_dates, thin=True)
            return CrossSectionAlignment(
                matrix, n_in, kept, n_dates, True, float("nan"), float("nan"))

        if n_in < 2:
            return _thin(np.empty((n_in, 0)), n_in, 0)

        # 1. recent dense window: the most recent `window` observed dates.
        all_dates = sorted(set().union(*(set(d) for d in per_symbol.values())))
        if len(all_dates) > window:
            all_dates = all_dates[-window:]
        win = set(all_dates)
        n_win = len(all_dates)
        per_symbol = {s: {d: dv[d] for d in dv.keys() & win}
                      for s, dv in per_symbol.items()}

        # 2. drop the ragged tail: symbols below the window-coverage threshold.
        min_cov_dates = min_symbol_coverage * n_win
        survivors = {s: dv for s, dv in per_symbol.items()
                     if len(dv) >= min_cov_dates}

        # 3 + 4. dense complete-case panel on the survivors.
        kept_syms, kept_dates = _complete_case_panel(
            survivors, min_date_coverage, min_dates)
        n_dates = len(kept_dates)
        if len(kept_syms) < 2 or n_dates < min_dates:
            matrix = (np.array([[survivors[s][d] for d in kept_dates]
                                for s in kept_syms], dtype=float)
                      if kept_syms and kept_dates
                      else np.empty((len(kept_syms), n_dates)))
            return _thin(matrix, len(kept_syms), n_dates)

        matrix = np.array([[survivors[s][d] for d in kept_dates]
                           for s in kept_syms], dtype=float)
        rho = _correlation_from_dense_matrix(matrix)
        if math.isnan(rho):                       # all survivors constant (rare)
            return _thin(matrix, len(kept_syms), n_dates)

        rho_c = max(0.0, min(0.999999, rho))
        eff = n_in / (1.0 + (n_in - 1) * rho_c)
        eff = float(max(1.0, min(float(n_in), eff)))
        set_attributes(span, n_symbols_in=n_in, n_symbols_kept=len(kept_syms),
                       n_dates=n_dates, thin=False)
        return CrossSectionAlignment(
            matrix, n_in, len(kept_syms), n_dates, False, float(rho), eff)


def mean_pairwise_correlation(
    returns_by_symbol: Dict[str, Series], **kwargs: Any) -> float:
    """Average off-diagonal Pearson correlation across the symbol cross-section,
    robust to ragged histories (see :func:`align_cross_section`). NaN when the
    aligned survivor panel is too thin to estimate a correlation."""
    return align_cross_section(returns_by_symbol, **kwargs).mean_correlation


def effective_cross_section_n(
    returns_by_symbol: Dict[str, Series], **kwargs: Any) -> float:
    """Correlation-discounted effective independent-N of the symbol cross-section.

    ``N_eff = N / (1 + (N-1) * rho_bar)`` — Kish's effective sample size, the
    right X-axis for admission power because it sets the variance of the
    cross-sectional mean the discovery gate consumes. A single symbol has
    effective-N 1; a ragged panel too thin to align returns NaN (surfaced, not
    silently collapsed to the raw N). See :func:`align_cross_section`."""
    n = sum(1 for v in returns_by_symbol.values() if v)
    if n <= 1:
        return float(n)
    return align_cross_section(returns_by_symbol, **kwargs).effective_n


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
                    alignment = align_cross_section(
                        returns, window=config.correlation_window_days)
                    eff_n = alignment.effective_n
                    rho = alignment.mean_correlation
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
                        "n_symbols_aligned": alignment.n_symbols_kept,
                        "n_aligned_dates": alignment.n_dates,
                        "alignment_thin": alignment.thin,
                        "n_admitted": summary["n_admitted"],
                        "family_size": summary["family_size"],
                        "spa_p_consistent": spa_p,
                        "outer_pvalues": outer_pvalues,
                        "battery_fingerprint": fp,
                    })

                spa_vals = [d["spa_p_consistent"] for d in draws
                            if d["spa_p_consistent"] is not None]
                n_thin = sum(1 for d in draws if d["alignment_thin"])
                fr_result = {
                    "fraction": fraction,
                    "effective_n": summarize([d["effective_n"] for d in draws]),
                    "admitted": summarize([d["n_admitted"] for d in draws]),
                    "n_symbols": summarize([d["n_symbols"] for d in draws]),
                    "mean_correlation": summarize(
                        [d["mean_correlation"] for d in draws]),
                    "n_symbols_aligned": summarize(
                        [d["n_symbols_aligned"] for d in draws]),
                    "n_aligned_dates": summarize(
                        [d["n_aligned_dates"] for d in draws]),
                    "thin_draws": n_thin,
                    "spa_p": summarize(spa_vals),
                    "draws": draws,
                }
                per_fraction.append(fr_result)
                set_attributes(fspan,
                               effective_n_mean=fr_result["effective_n"]["mean"],
                               admitted_mean=fr_result["admitted"]["mean"],
                               n_symbols_aligned_mean=fr_result[
                                   "n_symbols_aligned"]["mean"],
                               thin_draws=n_thin)

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
