"""Pluggable symbol-universe filters for regime discovery (006, T008).

The discovery universe is selected through a declared, chainable filter
interface (FR-121a). v1 built-ins: `test_tickers` (drops the NASDAQ ZVZZT
test-ticker family), `asset_type:<type>` (keeps matching `stocks.asset_type`
rows), and an explicit `passthrough` identity for deliberately unfiltered
runs. The chain is recorded verbatim in the run's pre-registration: the
universe can never be a hidden researcher degree of freedom, and an
unfiltered run must be declared, never a silent fallback.

Richer filters (liquidity tiers, market-cap floors, listing age) plug in
later by extending _FILTER_TYPES.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import List, Optional

from gefion.observability import create_span, set_attributes

# NASDAQ test tickers: ZVZZT, ZWZZT, ZXZZT, ZJZZT, ZAZZT, ...
_TEST_TICKER_RE = re.compile(r"^Z[A-Z]ZZT$")

# Both real-world vocabularies: AlphaVantage OVERVIEW says "Common Stock",
# LISTING_STATUS (the backfill source) says "Stock".
_ASSET_TYPE_ALIASES = {"common": ("Common Stock", "Stock")}


class UniverseError(ValueError):
    """Raised on an invalid or undeclared universe-filter chain."""


@dataclasses.dataclass(frozen=True)
class FilterSpec:
    """One declared filter: a registered kind plus its optional argument."""

    kind: str
    arg: Optional[str] = None

    def describe(self) -> str:
        return f"{self.kind}:{self.arg}" if self.arg is not None else self.kind


DEFAULT_CHAIN = "test_tickers,asset_type:common"

_FILTER_TYPES = ("test_tickers", "asset_type", "passthrough", "half")

# Split-half robustness (issue #68): `half:a`/`half:b` deterministically
# partition the universe by symbol content hash. At market scope this is a
# ROBUSTNESS dimension (was the edge driven by a few names?), NOT independent
# validation — both halves share the same market history. See docs/REGIMES.md.
_HALVES = ("a", "b")


def _half_of(symbol: str) -> str:
    return _HALVES[hashlib.sha256(symbol.encode("utf-8")).digest()[0] % 2]


def parse_filter_chain(spec: Optional[str]) -> List[FilterSpec]:
    """Parse a declared chain string; None means the default quality chain.

    An empty string is refused: deliberately unfiltered runs must declare
    `passthrough` explicitly, and passthrough must stand alone.
    """
    if spec is None:
        spec = DEFAULT_CHAIN
    if not spec.strip():
        raise UniverseError(
            "empty universe filter — declare 'passthrough' explicitly for an unfiltered run")

    out: List[FilterSpec] = []
    for part in spec.split(","):
        part = part.strip()
        kind, _, arg = part.partition(":")
        if kind not in _FILTER_TYPES:
            raise UniverseError(f"unknown universe filter: {part!r}")
        if kind == "asset_type":
            if arg not in _ASSET_TYPE_ALIASES:
                raise UniverseError(f"unknown asset_type selector: {arg!r}")
            out.append(FilterSpec(kind, arg))
        elif kind == "half":
            if arg not in _HALVES:
                raise UniverseError(f"unknown half selector: {arg!r} (expected a|b)")
            out.append(FilterSpec(kind, arg))
        else:
            if arg:
                raise UniverseError(f"filter {kind!r} takes no argument")
            out.append(FilterSpec(kind))

    if any(f.kind == "passthrough" for f in out) and len(out) > 1:
        raise UniverseError("passthrough must be declared alone")
    return out


def describe_chain(chain: List[FilterSpec]) -> List[str]:
    """The chain as recorded in the run's search-space pre-registration."""
    return [f.describe() for f in chain]


def apply_chain(chain: List[FilterSpec], symbols: List[str], conn=None) -> List[str]:
    """Apply every filter in order; preserves input order of survivors."""
    with create_span("discovery.universe.apply_chain",
                     chain=",".join(describe_chain(chain)),
                     n_symbols=len(symbols)) as span:
        out = list(symbols)
        for spec in chain:
            if spec.kind == "passthrough":
                continue
            if spec.kind == "test_tickers":
                out = [s for s in out if not _TEST_TICKER_RE.match(s)]
            elif spec.kind == "half":
                out = [s for s in out if _half_of(s) == spec.arg]
            elif spec.kind == "asset_type":
                if conn is None:
                    raise UniverseError("asset_type filter requires a database connection")
                wanted = list(_ASSET_TYPE_ALIASES[spec.arg])
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT symbol FROM stocks "
                        "WHERE symbol = ANY(%s) AND asset_type = ANY(%s)",
                        (out, wanted),
                    )
                    keep = {r[0] for r in cur.fetchall()}
                out = [s for s in out if s in keep]
        set_attributes(span, n_kept=len(out))
        return out
