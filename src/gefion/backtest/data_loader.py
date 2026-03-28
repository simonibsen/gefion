"""
Data loading utilities for backtesting.

Loads historical price data from the database for use in backtesting.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql

from gefion.observability import create_span, set_attributes


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
    with create_span("backtest.data_loader.load_price_data_for_backtest", symbol_count=len(symbols) if symbols else 0) as span:
        with psycopg.connect(db_url) as conn:
            # Build query
            symbol_where_clauses = []  # For filtering stocks
            ohlcv_where_clauses = []   # For filtering price data
            params = []

            # Filter by symbols
            # Note: exchange filtering is not currently supported as stocks table
            # doesn't have an exchange column. When exchange is specified, we fall
            # back to just using the limit parameter.
            if symbols:
                symbol_where_clauses.append("s.symbol = ANY(%s)")
                params.append(symbols)

            # Apply symbol limit if specified
            if limit and not symbols:
                # Create subquery to get limited set of stock IDs
                # Note: When exchange is passed, we can't filter by it (no column),
                # so we just use the limit to get top N active stocks with data
                symbol_id_subquery = f"""
                    (SELECT s.id FROM stocks s
                     JOIN stock_ohlcv o ON s.id = o.data_id
                     WHERE s.status = 'Active'
                     GROUP BY s.id
                     ORDER BY COUNT(*) DESC
                     LIMIT {limit})
                """
                # Replace symbol filters with IN clause
                symbol_where_clauses = [f"s.id IN {symbol_id_subquery}"]

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

                set_attributes(span, row_count=len(price_data), table="stock_ohlcv")
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
        exchange: Filter by exchange (optional, currently ignored - no exchange column)
        limit: Maximum number of symbols to return (optional)

    Returns:
        List of symbol strings
    """
    with create_span("backtest.data_loader.get_available_symbols") as span:
        with psycopg.connect(db_url) as conn:
            # Note: exchange filtering not supported (no exchange column in stocks table)
            # We just return active stocks with sufficient data
            where_clause = "WHERE s.status = 'Active'"
            params: List[Any] = []

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
                result = [row[0] for row in cur.fetchall()]
                set_attributes(span, result_count=len(result))
                return result
