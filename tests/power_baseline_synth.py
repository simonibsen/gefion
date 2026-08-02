"""Per-symbol synthetic universe for the power-baseline harness (issue #180).

Pure test infrastructure — no DB, no real data. Unlike ``discovery_synth`` (which
generates market-level series directly, because v1 discovery only tests
market-level signals), this generator produces a PER-SYMBOL cross-section so the
harness's two concerns can be exercised honestly:

1. sector-stratified subsampling + cross-sectional effective-N (both symbol-level);
2. the discovery gate's admission power as a function of how many symbols are
   averaged into the market aggregate.

The mechanism that makes power scale with N is the real one: the market forward
return the discovery gate consumes is the cross-sectional MEAN of per-symbol
forward returns. A per-symbol return is

    r[s, t] = common_t + inject_t + eps[s, t]

where ``common_t`` is a shared factor (so symbols correlate — effective-N < N),
``inject_t`` is a planted conditional edge shared across symbols, and ``eps`` is
idiosyncratic noise. Averaging k symbols shrinks the idiosyncratic term as
~1/sqrt(k), so the edge's signal-to-noise — and thus discovery power — RISES with
the size (effective-N) of the subsample. Everything is seeded and deterministic.
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from discovery_synth import business_days
from gefion.regimes.discovery.segregation import MarketData

Series = List[Tuple[datetime.date, float]]


@dataclasses.dataclass
class PerSymbolUniverse:
    """A seeded per-symbol cross-section with shared market-level features."""

    dates: List[datetime.date]
    symbols: List[str]
    sector_of: Dict[str, str]
    features: Dict[str, Series]                 # market-level shared feature series
    per_symbol_returns: Dict[str, np.ndarray]   # symbol -> length-n_days fwd returns
    dataset_version: str = "synth-pbase"
    planted: Optional[Dict] = None

    def returns_for_subsample(self, subsample: Sequence[str]) -> Dict[str, Series]:
        """Per-symbol forward-return series for the subsample (effective-N input)."""
        return {s: list(zip(self.dates, self.per_symbol_returns[s]))
                for s in subsample}

    def market_for_subsample(self, subsample: Sequence[str]) -> MarketData:
        """Aggregate the subsample to a market-level MarketData the way the real
        loader does: market forward return = cross-sectional MEAN over the
        subsample; features are the shared market-level series (so the candidate
        battery and its causal labels are identical across every subsample)."""
        if not subsample:
            raise ValueError("empty subsample")
        stacked = np.vstack([self.per_symbol_returns[s] for s in subsample])
        market_fwd = stacked.mean(axis=0)
        return MarketData(
            features={k: list(v) for k, v in self.features.items()},
            forward_returns=list(zip(self.dates, market_fwd)),
            dataset_version=self.dataset_version,
        )


def _sectored_symbols(n_symbols: int, n_sectors: int) -> Tuple[List[str], Dict[str, str]]:
    symbols, sector_of = [], {}
    for i in range(n_symbols):
        sym = f"SYN{i:04d}"
        sec = f"sector_{i % n_sectors}"
        symbols.append(sym)
        sector_of[sym] = sec
    return symbols, sector_of


def make_per_symbol_universe(
    seed: int,
    n_days: int = 500,
    n_symbols: int = 60,
    n_sectors: int = 3,
    n_noise_features: int = 2,
    planted: bool = False,
    effect: float = 0.05,
    common_scale: float = 0.003,
    idio_scale: float = 0.04,
    episode_len: int = 10,
) -> PerSymbolUniverse:
    """A seeded per-symbol universe.

    With ``planted=False`` the signal is independent of every return, so nothing
    should ever survive discovery at any N (the false-positive floor). With
    ``planted=True`` a conditional edge is injected inside the regime where the
    conditioning feature is high — recoverable only once enough symbols are
    averaged to beat the idiosyncratic noise (power rises with N).
    """
    rng = np.random.default_rng(seed)
    dates = business_days(n_days)
    symbols, sector_of = _sectored_symbols(n_symbols, n_sectors)

    # Market-level shared features: a signal, a slow square-wave conditioner, and
    # decoy noise features. These are identical for every subsample.
    features: Dict[str, Series] = {}
    sig = rng.normal(size=n_days)
    features["sig_0"] = list(zip(dates, sig))
    for j in range(n_noise_features):
        features[f"noise_{j}"] = list(zip(dates, rng.normal(size=n_days)))

    cond = np.zeros(n_days)
    in_regime = np.zeros(n_days, dtype=bool)
    high = True
    for start in range(0, n_days, episode_len):
        end = min(start + episode_len, n_days)
        cond[start:end] = (1.0 if high else -1.0) + rng.normal(scale=0.05, size=end - start)
        in_regime[start:end] = high
        high = not high
    features["cond_0"] = list(zip(dates, cond))

    inject = np.where(in_regime, effect * np.sign(sig), 0.0) if planted \
        else np.zeros(n_days)
    common = rng.normal(scale=common_scale, size=n_days)

    per_symbol_returns: Dict[str, np.ndarray] = {}
    for s in symbols:
        eps = rng.normal(scale=idio_scale, size=n_days)
        per_symbol_returns[s] = common + inject + eps

    return PerSymbolUniverse(
        dates=dates,
        symbols=symbols,
        sector_of=sector_of,
        features=features,
        per_symbol_returns=per_symbol_returns,
        planted=({"signal": "sig_0", "conditioning": "cond_0",
                  "effect": effect,
                  "in_regime_dates": [d for d, k in zip(dates, in_regime) if k]}
                 if planted else None),
    )


def correlated_returns(
    seed: int, n_symbols: int, n_days: int = 250, rho_scale: float = 0.08,
    idio_scale: float = 0.02,
) -> Dict[str, Series]:
    """A pure per-symbol return panel with a shared factor (positive average
    correlation) — for the effective-N unit tests. Larger ``rho_scale`` relative
    to ``idio_scale`` means tighter correlation and a smaller effective-N."""
    rng = np.random.default_rng(seed)
    dates = business_days(n_days)
    common = rng.normal(scale=rho_scale, size=n_days)
    out: Dict[str, Series] = {}
    for i in range(n_symbols):
        eps = rng.normal(scale=idio_scale, size=n_days)
        out[f"SYN{i:04d}"] = list(zip(dates, common + eps))
    return out


def independent_returns(seed: int, n_symbols: int, n_days: int = 250) -> Dict[str, Series]:
    """A per-symbol panel with no shared factor — effective-N should approach N."""
    rng = np.random.default_rng(seed)
    dates = business_days(n_days)
    return {f"SYN{i:04d}": list(zip(dates, rng.normal(size=n_days)))
            for i in range(n_symbols)}
