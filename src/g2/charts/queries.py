"""
Database query functions for chart data retrieval.
"""

from datetime import date
from typing import Any, Dict, List, Optional

import psycopg


def fetch_ohlcv_for_chart(
    conn: psycopg.Connection,
    symbol: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    adjusted: bool = True,
) -> List[Dict[str, Any]]:
    """
    Fetch OHLCV data for charting.

    Args:
        conn: Database connection
        symbol: Stock symbol (e.g., 'AAPL')
        start_date: Optional start date filter
        end_date: Optional end date filter
        adjusted: Use split-adjusted prices (default True for accurate analysis)

    Returns:
        List of dicts with keys: date, open, high, low, close, volume
    """
    # When adjusted=True, use adjusted_close and compute adjusted OHLC
    # The adjustment ratio is: adjusted_close / close
    # We apply this ratio to open, high, low as well
    if adjusted:
        query = """
            SELECT
                o.date,
                CASE WHEN o.adjusted_close IS NOT NULL AND o.close > 0
                     THEN o.open * (o.adjusted_close / o.close)
                     ELSE o.open END as adj_open,
                CASE WHEN o.adjusted_close IS NOT NULL AND o.close > 0
                     THEN o.high * (o.adjusted_close / o.close)
                     ELSE o.high END as adj_high,
                CASE WHEN o.adjusted_close IS NOT NULL AND o.close > 0
                     THEN o.low * (o.adjusted_close / o.close)
                     ELSE o.low END as adj_low,
                COALESCE(o.adjusted_close, o.close) as adj_close,
                o.volume
            FROM stock_ohlcv o
            JOIN stocks s ON o.data_id = s.id
            WHERE s.symbol = %s
        """
    else:
        query = """
            SELECT o.date, o.open, o.high, o.low, o.close, o.volume
            FROM stock_ohlcv o
            JOIN stocks s ON o.data_id = s.id
            WHERE s.symbol = %s
        """
    params: List[Any] = [symbol]

    if start_date:
        query += " AND o.date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND o.date <= %s"
        params.append(end_date)

    query += " ORDER BY o.date ASC"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [
        {
            "date": row[0],
            "open": float(row[1]) if row[1] is not None else None,
            "high": float(row[2]) if row[2] is not None else None,
            "low": float(row[3]) if row[3] is not None else None,
            "close": float(row[4]) if row[4] is not None else None,
            "volume": int(row[5]) if row[5] is not None else 0,
        }
        for row in rows
    ]


def fetch_predictions_for_chart(
    conn: psycopg.Connection,
    symbol: str,
    model_name: str,
    horizon: int = 7,
) -> List[Dict[str, Any]]:
    """
    Fetch prediction data for charting.

    Args:
        conn: Database connection
        symbol: Stock symbol
        model_name: Model name to filter by
        horizon: Prediction horizon in days

    Returns:
        List of dicts with keys: date, q10, q50, q90
    """
    query = """
        SELECT qp.prediction_date, qp.q10, qp.q50, qp.q90
        FROM quantile_predictions qp
        JOIN stocks s ON qp.data_id = s.id
        JOIN ml_models m ON qp.model_id = m.id
        WHERE s.symbol = %s
          AND m.name = %s
          AND qp.horizon_days = %s
        ORDER BY qp.prediction_date ASC
    """

    with conn.cursor() as cur:
        cur.execute(query, (symbol, model_name, horizon))
        rows = cur.fetchall()

    return [
        {
            "date": row[0],
            "q10": float(row[1]) if row[1] is not None else None,
            "q50": float(row[2]) if row[2] is not None else None,
            "q90": float(row[3]) if row[3] is not None else None,
        }
        for row in rows
    ]


def fetch_features_for_chart(
    conn: psycopg.Connection,
    symbol: str,
    feature_names: List[str],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch feature data for charting.

    Args:
        conn: Database connection
        symbol: Stock symbol
        feature_names: List of feature names to fetch
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        Dict mapping feature_name -> list of {date, value}
    """
    # Initialize result dict with empty lists
    result: Dict[str, List[Dict[str, Any]]] = {name: [] for name in feature_names}

    if not feature_names:
        return result

    query = """
        SELECT cf.date, fd.name, cf.value
        FROM computed_features cf
        JOIN stocks s ON cf.data_id = s.id
        JOIN feature_definitions fd ON cf.feature_id = fd.id
        WHERE s.symbol = %s
          AND fd.name = ANY(%s)
    """
    params: List[Any] = [symbol, feature_names]

    if start_date:
        query += " AND cf.date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND cf.date <= %s"
        params.append(end_date)

    query += " ORDER BY cf.date ASC, fd.name"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    for row in rows:
        feature_name = row[1]
        if feature_name in result:
            result[feature_name].append({
                "date": row[0],
                "value": float(row[2]) if row[2] is not None else None,
            })

    return result


def fetch_backtest_equity_curve(
    conn: psycopg.Connection,
    backtest_id: str,
) -> List[Dict[str, Any]]:
    """
    Fetch equity curve data from a backtest.

    Note: This expects a backtest_equity_curve table which may need to be
    created separately. The backtest module currently returns results in memory
    rather than persisting to database.

    Args:
        conn: Database connection
        backtest_id: Backtest identifier

    Returns:
        List of dicts with keys: date, equity, drawdown
    """
    # Check if table exists first
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'backtest_equity_curve'
            )
        """)
        if not cur.fetchone()[0]:
            return []

        query = """
            SELECT date, equity, drawdown
            FROM backtest_equity_curve
            WHERE backtest_id = %s
            ORDER BY date ASC
        """
        cur.execute(query, (backtest_id,))
        rows = cur.fetchall()

    return [
        {
            "date": row[0],
            "equity": float(row[1]),
            "drawdown": float(row[2]) if row[2] is not None else 0.0,
        }
        for row in rows
    ]
