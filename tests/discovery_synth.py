"""Synthetic-data generators for regime-discovery tests (006, T002).

Pure test infrastructure — no DB, no real data. Everything is seeded and
deterministic so discovery runs built on it are byte-reproducible (FR-111)
and the negative-control suite (SC-101/102) is a standing guarantee rather
than a flaky sample.

The toy universe is market-level by construction: discovery's v1 signal
source tests market-level feature series against market-level forward
returns, so we generate those directly (a per-symbol layer would only be
averaged away again).
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

Series = List[Tuple[datetime.date, float]]

_START = datetime.date(2020, 1, 6)  # a Monday; fixed so runs are reproducible


def business_days(n_days: int, start: datetime.date = _START) -> List[datetime.date]:
    """n_days consecutive weekdays from a fixed start date."""
    out: List[datetime.date] = []
    d = start
    while len(out) < n_days:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


@dataclasses.dataclass
class SynthUniverse:
    """A seeded toy universe: market-level features, prices, and forward returns."""

    seed: int
    dates: List[datetime.date]
    prices: Series                      # market index level (GBM)
    features: Dict[str, Series]         # market-level feature series (noise unless planted)
    forward_returns: Series             # 1-day forward market return at each date
    planted: Optional[Dict] = None      # description of any injected structure

    def feature_names(self) -> List[str]:
        return sorted(self.features)


def make_universe(
    seed: int,
    n_days: int = 500,
    n_features: int = 6,
    feature_prefix: str = "noise",
) -> SynthUniverse:
    """Pure-noise universe: GBM market prices, iid-noise features, no structure.

    Forward returns are the GBM's own next-day returns — independent of every
    feature, so nothing should ever survive discovery on this data (SC-101).
    """
    rng = np.random.default_rng(seed)
    dates = business_days(n_days + 1)  # +1 so every kept date has a forward return

    log_rets = rng.normal(loc=0.0002, scale=0.01, size=n_days + 1)
    levels = 100.0 * np.exp(np.cumsum(log_rets))

    features: Dict[str, Series] = {}
    for i in range(n_features):
        vals = rng.normal(size=n_days + 1)
        features[f"{feature_prefix}_{i}"] = list(zip(dates[:n_days], vals[:n_days]))

    fwd = levels[1:] / levels[:-1] - 1.0
    return SynthUniverse(
        seed=seed,
        dates=dates[:n_days],
        prices=list(zip(dates[:n_days], levels[:n_days])),
        features=features,
        forward_returns=list(zip(dates[:n_days], fwd[:n_days])),
    )


def plant_regime_edge(
    universe: SynthUniverse,
    signal_feature: str,
    conditioning_feature: str = "planted_cond",
    effect: float = 0.02,
    regime_fraction: float = 0.5,
) -> SynthUniverse:
    """Inject a conditional edge: `signal_feature` predicts forward returns, but
    ONLY inside the regime where `conditioning_feature` is high (SC-102).

    The conditioning series is a slow square wave (long episodes, so causal
    tercile/threshold labels recover it and the episode-based effective-N floor
    is clearable). Inside the regime, forward returns gain
    `effect * sign(signal)`; outside, returns stay pure noise.
    """
    rng = np.random.default_rng(universe.seed + 104729)  # distinct stream, still seeded
    n = len(universe.dates)

    # Slow alternation: ~10 episodes across the sample, half of them "in regime".
    episode_len = max(20, n // 10)
    cond_vals = np.zeros(n)
    in_regime = np.zeros(n, dtype=bool)
    high = True
    for start in range(0, n, episode_len):
        end = min(start + episode_len, n)
        level = 1.0 if high else -1.0
        cond_vals[start:end] = level + rng.normal(scale=0.05, size=end - start)
        in_regime[start:end] = high
        high = not high
    if regime_fraction >= 0.999:
        in_regime[:] = True
        cond_vals = np.abs(cond_vals)

    if signal_feature not in universe.features:
        raise KeyError(f"unknown signal feature {signal_feature!r}")
    sig = np.array([v for _, v in universe.features[signal_feature]])

    fwd = np.array([v for _, v in universe.forward_returns])
    fwd = fwd + np.where(in_regime, effect * np.sign(sig), 0.0)

    features = dict(universe.features)
    features[conditioning_feature] = list(zip(universe.dates, cond_vals))

    return SynthUniverse(
        seed=universe.seed,
        dates=universe.dates,
        prices=universe.prices,
        features=features,
        forward_returns=list(zip(universe.dates, fwd)),
        planted={
            "signal": signal_feature,
            "conditioning": conditioning_feature,
            "effect": effect,
            "in_regime_dates": [d for d, keep in zip(universe.dates, in_regime) if keep],
        },
    )
