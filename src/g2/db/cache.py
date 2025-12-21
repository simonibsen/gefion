"""
Query result caching to reduce redundant database queries.

Pre-fetches commonly needed data (e.g., stock IDs) and shares across workers
to avoid repeated queries for the same information.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import psycopg


def prefetch_stock_ids(conn: psycopg.Connection, symbols: Sequence[str]) -> Dict[str, int]:
    """
    Pre-fetch stock IDs for a list of symbols in a single query.

    This avoids N individual queries when processing N symbols in parallel.

    Args:
        conn: Database connection
        symbols: List of stock symbols to look up

    Returns:
        Dict mapping symbol -> stock_id for symbols that exist in database
    """
    if not symbols:
        return {}

    with conn.cursor() as cur:
        # Use ANY for efficient IN query with parameter binding
        cur.execute(
            "SELECT symbol, id FROM stocks WHERE symbol = ANY(%s);",
            (list(symbols),)
        )
        rows = cur.fetchall()

    return {row[0]: row[1] for row in rows}


def prefetch_latest_prices(
    conn: psycopg.Connection,
    stock_ids: Sequence[int]
) -> Dict[int, Optional[object]]:
    """
    Pre-fetch latest price dates for multiple stocks in one query.

    Args:
        conn: Database connection
        stock_ids: List of stock IDs to query

    Returns:
        Dict mapping stock_id -> latest_date (or None if no prices)
    """
    if not stock_ids:
        return {}

    with conn.cursor() as cur:
        # Use lateral join for efficient per-stock latest date query
        cur.execute("""
            SELECT s.id, MAX(sp.date) as latest_date
            FROM unnest(%s::int[]) as s(id)
            LEFT JOIN stock_ohlcv sp ON sp.data_id = s.id
            GROUP BY s.id;
        """, (list(stock_ids),))
        rows = cur.fetchall()

    return {row[0]: row[1] for row in rows}


def prefetch_feature_ids(
    conn: psycopg.Connection,
    feature_names: Sequence[str]
) -> Dict[str, int]:
    """
    Pre-fetch feature IDs for a list of feature names.

    Args:
        conn: Database connection
        feature_names: List of feature names to look up

    Returns:
        Dict mapping feature_name -> feature_id
    """
    if not feature_names:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, id FROM feature_definitions WHERE name = ANY(%s);",
            (list(feature_names),)
        )
        rows = cur.fetchall()

    return {row[0]: row[1] for row in rows}


class StockMetadataCache:
    """
    Cache for stock metadata to share across workers.

    Usage:
        # Pre-fetch once before parallel processing
        cache = StockMetadataCache()
        cache.load_stocks(conn, symbols)

        # Use in workers (no DB queries)
        stock_id = cache.get_stock_id("AAPL")
        if stock_id is None:
            # Stock doesn't exist yet
            stock_id = create_new_stock(...)
            cache.add_stock("AAPL", stock_id)
    """

    def __init__(self):
        self._stock_ids: Dict[str, int] = {}
        self._latest_dates: Dict[int, Optional[object]] = {}
        self._feature_ids: Dict[str, int] = {}

    def load_stocks(self, conn: psycopg.Connection, symbols: Sequence[str]) -> None:
        """Pre-load stock IDs for given symbols."""
        self._stock_ids.update(prefetch_stock_ids(conn, symbols))

    def load_latest_prices(self, conn: psycopg.Connection, stock_ids: Sequence[int]) -> None:
        """Pre-load latest price dates for given stock IDs."""
        self._latest_dates.update(prefetch_latest_prices(conn, stock_ids))

    def load_features(self, conn: psycopg.Connection, feature_names: Sequence[str]) -> None:
        """Pre-load feature IDs for given feature names."""
        self._feature_ids.update(prefetch_feature_ids(conn, feature_names))

    def get_stock_id(self, symbol: str) -> Optional[int]:
        """Get cached stock ID, or None if not found."""
        return self._stock_ids.get(symbol)

    def add_stock(self, symbol: str, stock_id: int) -> None:
        """Add a new stock to cache (after creating in DB)."""
        self._stock_ids[symbol] = stock_id

    def get_latest_date(self, stock_id: int) -> Optional[object]:
        """Get cached latest price date, or None if not in cache."""
        return self._latest_dates.get(stock_id)

    def get_feature_id(self, feature_name: str) -> Optional[int]:
        """Get cached feature ID, or None if not found."""
        return self._feature_ids.get(feature_name)

    def clear(self) -> None:
        """Clear all cached data."""
        self._stock_ids.clear()
        self._latest_dates.clear()
        self._feature_ids.clear()
