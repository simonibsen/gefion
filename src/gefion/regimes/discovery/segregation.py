"""Nested data segregation for regime discovery (006, T012 — US1).

The DiscoveryDataContext is the ONLY data-access path during discovery and
detector fitting: it is constructed from the full market data plus an outer
HoldoutManager (reused from experiments.holdout) and exposes inner-window
rows only. Any request touching the outer holdout raises SegregationError.
Enforcement is by construction (FR-101/102) — the same pattern as 005's
causality-by-construction leaves; evaluation of the outer holdout happens in
a separate step that only runs after the candidate set is frozen (T4 guard,
enforced by the ledger lifecycle).
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Any, Dict, List, Optional, Tuple

from gefion.experiments.holdout import HoldoutManager
from gefion.observability import create_span, set_attributes

Series = List[Tuple[datetime.date, float]]


class SegregationError(RuntimeError):
    """Raised when discovery-phase code touches the outer holdout."""


@dataclasses.dataclass
class MarketData:
    """Market-level series for a discovery run: features and forward returns.

    Synthetic runs build this from the test generators; real runs load it via
    the signal source (market-median feature series + forward market returns).
    """

    features: Dict[str, Series]
    forward_returns: Series
    dataset_version: str = "dev"

    def dates(self) -> List[datetime.date]:
        return [d for d, _ in self.forward_returns]


class DiscoveryDataContext:
    """Inner-window-only view of the market data (discovery/fitting phase).

    When a fresh-holdout `reserve` block is declared (expressive tier), its
    dates are excluded from the inner window too — the reserve is validation
    data, never discovery data (FR-118a).
    """

    def __init__(self, market: MarketData, holdout: HoldoutManager,
                 reserve: Optional[Tuple[datetime.date, datetime.date]] = None):
        with create_span("discovery.segregation.context") as span:
            self._market = market
            self.inner_end = holdout.get_max_training_date()
            self.holdout_start = holdout.holdout_start_date
            self.holdout_end = holdout.holdout_end_date
            self.reserve = reserve

            inner_dates = [d for d in market.dates() if self._is_inner(d)]
            if not inner_dates:
                raise SegregationError(
                    "no data before the outer holdout — segregation cannot be proven "
                    f"(holdout starts {self.holdout_start}, data starts "
                    f"{min(market.dates(), default=None)})")
            self.inner_start = min(inner_dates)
            set_attributes(span, inner_days=len(inner_dates),
                           inner_end=str(self.inner_end))

    def _is_inner(self, d: datetime.date) -> bool:
        if d > self.inner_end:
            return False
        if self.reserve and self.reserve[0] <= d <= self.reserve[1]:
            return False
        return True

    # -- inner-only accessors -------------------------------------------------

    def feature_names(self) -> List[str]:
        return sorted(self._market.features)

    def inner_feature(self, name: str) -> Series:
        if name not in self._market.features:
            raise LookupError(f"feature {name!r} not in market data")
        return [(d, v) for d, v in self._market.features[name] if self._is_inner(d)]

    def inner_returns(self) -> Series:
        return [(d, v) for d, v in self._market.forward_returns if self._is_inner(d)]

    def inner_market(self) -> MarketData:
        """The inner window as a MarketData view — the ONLY market object the
        discovery/screening phase may hold; every series stops at the boundary."""
        return MarketData(
            features={name: self.inner_feature(name) for name in self._market.features},
            forward_returns=self.inner_returns(),
            dataset_version=self._market.dataset_version,
        )

    # -- guards ---------------------------------------------------------------

    def check_dates(self, dates) -> None:
        """Raise if any date lies in the outer holdout or the reserve (FR-101)."""
        touched = [d for d in dates if not self._is_inner(d)]
        if touched:
            raise SegregationError(
                f"discovery touched {len(touched)} non-inner date(s) "
                f"(outer holdout or reserve), first: {min(touched)} "
                f"(inner window ends {self.inner_end})")

    # -- pre-registration record ------------------------------------------------

    def boundaries(self) -> Dict[str, Any]:
        """Segregation boundaries as recorded in the run row (FR-102)."""
        out = {
            "inner_start": str(self.inner_start),
            "inner_end": str(self.inner_end),
            "holdout_start": str(self.holdout_start),
            "holdout_end": str(self.holdout_end),
        }
        if self.reserve:
            out["reserve"] = {"start": str(self.reserve[0]), "end": str(self.reserve[1])}
        return out
