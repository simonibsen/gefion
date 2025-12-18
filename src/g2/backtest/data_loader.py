"""
Data loading utilities for backtesting.

Loads historical price data from the database for use in backtesting.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql


def load_price_data_for_backtest(
    db_url: str,
    symbols: Optional[List[str]] = None,
    exchange: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Load historical price data from database for backtesting.

    Args:
        db_url: Database connection URL
        symbols: List of symbols to load (optional)
        exchange: Exchange name to filter by (optional, alternative to symbols)
        start_date: Start date for price data (optional)
        end_date: End date for price data (optional)
        limit: Maximum number of symbols to load (optional, for testing)

    Returns:
        List of price records with symbol, date, close, open, high, low, volume
    """
    with psycopg.connect(db_url) as conn:
        # Build query
        where_clauses = []
        params = []

        # Filter by symbols or exchange
        if symbols:
            where_clauses.append("s.symbol = ANY(%s)")
            params.append(symbols)
        elif exchange:
            where_clauses.append("LOWER(s.exchange) = LOWER(%s)")
            params.append(exchange)

        # Filter by date range
        if start_date:
            where_clauses.append("o.date >= %s")
            params.append(start_date)
        if end_date:
            where_clauses.append("o.date <= %s")
            params.append(end_date)

        # Build WHERE clause
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Build limit clause for symbols (not rows)
        limit_sql = ""
        if limit and not symbols:  # Only apply limit if not filtering by specific symbols
            # Subquery to limit symbols
            symbol_limit_subquery = f"""
                SELECT id FROM stocks s
                WHERE {where_sql if not exchange else "LOWER(s.exchange) = LOWER(%s)"}
                AND s.status = 'Active'
                LIMIT {limit}
            """
            where_sql = f"s.id IN ({symbol_limit_subquery})"

        query = f"""
            SELECT
                s.symbol,
                o.date,
                o.close,
                o.open,
                o.high,
                o.low,
                o.volume
            FROM stocks s
            JOIN stock_ohlcv o ON s.id = o.data_id
            WHERE {where_sql}
            ORDER BY o.date ASC, s.symbol ASC
        """

        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

            # Convert to list of dicts
            price_data = [
                {
                    "symbol": row[0],
                    "date": row[1],
                    "close": float(row[2]) if row[2] is not None else None,
                    "open": float(row[3]) if row[3] is not None else None,
                    "high": float(row[4]) if row[4] is not None else None,
                    "low": float(row[5]) if row[5] is not None else None,
                    "volume": int(row[6]) if row[6] is not None else None,
                }
                for row in rows
            ]

            return price_data


def get_available_symbols(
    db_url: str,
    exchange: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """
    Get list of available symbols in the database.

    Args:
        db_url: Database connection URL
        exchange: Filter by exchange (optional)
        limit: Maximum number of symbols to return (optional)

    Returns:
        List of symbol strings
    """
    with psycopg.connect(db_url) as conn:
        where_clause = ""
        params = []

        if exchange:
            where_clause = "WHERE LOWER(s.exchange) = LOWER(%s) AND s.status = 'Active'"
            params.append(exchange)
        else:
            where_clause = "WHERE s.status = 'Active'"

        limit_clause = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT s.symbol
            FROM stocks s
            JOIN stock_ohlcv o ON s.id = o.data_id
            {where_clause}
            GROUP BY s.symbol
            HAVING COUNT(*) > 50
            ORDER BY COUNT(*) DESC
            {limit_clause}
        """

        with conn.cursor() as cur:
            cur.execute(query, params)
            return [row[0] for row in cur.fetchall()]
