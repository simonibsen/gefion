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
        symbol_where_clauses = []  # For filtering stocks
        ohlcv_where_clauses = []   # For filtering price data
        params = []

        # Filter by symbols or exchange
        if symbols:
            symbol_where_clauses.append("s.symbol = ANY(%s)")
            params.append(symbols)
        elif exchange:
            symbol_where_clauses.append("LOWER(s.exchange) = LOWER(%s)")
            params.append(exchange)

        # Apply symbol limit if specified
        if limit and not symbols:
            # Create subquery to get limited set of stock IDs
            subquery_where = " AND ".join(symbol_where_clauses) if symbol_where_clauses else "1=1"
            symbol_id_subquery = f"""
                (SELECT id FROM stocks s
                 WHERE {subquery_where} AND s.status = 'Active'
                 LIMIT {limit})
            """
            # Replace symbol filters with IN clause
            symbol_where_clauses = [f"s.id IN {symbol_id_subquery}"]
            # params already has the exchange parameter if needed

        # Add active status filter if not using limit subquery
        elif not limit:
            symbol_where_clauses.append("s.status = 'Active'")

        # Filter by date range (applies to OHLCV data)
        if start_date:
            ohlcv_where_clauses.append("o.date >= %s")
            params.append(start_date)
        if end_date:
            ohlcv_where_clauses.append("o.date <= %s")
            params.append(end_date)

        # Combine all WHERE clauses
        all_where_clauses = symbol_where_clauses + ohlcv_where_clauses
        where_sql = " AND ".join(all_where_clauses) if all_where_clauses else "1=1"

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
