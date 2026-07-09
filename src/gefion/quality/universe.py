"""Research-universe hardening (008, T016 — US3).

Junk instruments never enter a research universe: NASDAQ test tickers are
excluded unconditionally, and asset_type/exchange are declared, fail-closed
selectors. The test-ticker list is the catalog's single source of truth
(shared vocabulary with 006's discovery chain, which carries its own equivalent
regex implementation over the audited filter chain).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from gefion.observability import create_span, set_attributes
from gefion.quality import catalog as _catalog

# The NASDAQ test-ticker family also matches this shape (ZVZZT, ZWZZT, …); the
# catalog list is the authoritative enumeration and this is the belt-and-braces
# pattern for members not yet enumerated.
_TEST_TICKER_RE = re.compile(r"^Z[A-Z]ZZT$")

# AlphaVantage OVERVIEW says "Common Stock"; LISTING_STATUS says "Stock".
_ASSET_TYPE_ALIASES = {"common": ("Common Stock", "Stock")}


def _catalog_test_tickers(cat=None) -> set:
    cat = cat or _catalog.load_default()
    return set(cat.universe.get("test_tickers", []))


def is_test_ticker(symbol: str, cat=None) -> bool:
    return symbol in _catalog_test_tickers(cat) or bool(_TEST_TICKER_RE.match(symbol))


def exclude_test_tickers(symbols: List[str], cat=None) -> List[str]:
    tickers = _catalog_test_tickers(cat)
    return [s for s in symbols
            if s not in tickers and not _TEST_TICKER_RE.match(s)]


def research_universe(conn, symbols: List[str],
                      require_asset_type: Optional[str] = None,
                      cat=None) -> Tuple[List[str], Dict[str, int]]:
    """Filter a symbol list to a research-grade universe.

    Test tickers are always dropped. When `require_asset_type` is given, only
    stocks whose `stocks.asset_type` matches are kept — NULL/unknown asset type
    fails closed (excluded, counted), consistent with 006's filter chain.
    """
    with create_span("quality.universe.research", n_in=len(symbols)) as span:
        report = {"test_tickers": 0, "asset_type_excluded": 0}
        without_test = []
        for s in symbols:
            if is_test_ticker(s, cat):
                report["test_tickers"] += 1
            else:
                without_test.append(s)

        kept = without_test
        if require_asset_type:
            aliases = _ASSET_TYPE_ALIASES.get(require_asset_type)
            if aliases is None:
                from gefion.regimes.discovery.universe import UniverseError
                raise UniverseError(
                    f"unknown asset_type selector: {require_asset_type!r}")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol FROM stocks WHERE symbol = ANY(%s) "
                    "AND asset_type = ANY(%s)",
                    (without_test, list(aliases)),
                )
                matched = {row[0] for row in cur.fetchall()}
            kept = [s for s in without_test if s in matched]
            report["asset_type_excluded"] = len(without_test) - len(kept)

        set_attributes(span, n_out=len(kept), **report)
        return kept, report
