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
        SELECT p.prediction_date,
               (p.prediction_values->>'q10')::NUMERIC,
               (p.prediction_values->>'q50')::NUMERIC,
               (p.prediction_values->>'q90')::NUMERIC
        FROM predictions p
        JOIN stocks s ON p.data_id = s.id
        JOIN ml_models m ON p.model_id = m.id
        WHERE p.prediction_type = 'quantile'
          AND s.symbol = %s
          AND m.name = %s
          AND p.horizon_days = %s
        ORDER BY p.prediction_date ASC
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


# ---------------------------------------------------------------------------
# Phase 3: New chart category queries
# ---------------------------------------------------------------------------


def fetch_model_calibration(
    conn: psycopg.Connection,
    model_name: str,
) -> List[Dict[str, Any]]:
    """Fetch calibration data: predicted quantile levels vs observed coverage."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                (p.prediction_values->>'q10')::NUMERIC as q10,
                (p.prediction_values->>'q50')::NUMERIC as q50,
                (p.prediction_values->>'q90')::NUMERIC as q90,
                po.actual_return
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            JOIN prediction_outcomes po ON po.data_id = p.data_id
                AND po.prediction_date = p.prediction_date
                AND po.horizon_days = p.horizon_days
            WHERE p.prediction_type = 'quantile'
              AND m.name = %s
        """, (model_name,))
        rows = cur.fetchall()

    if not rows:
        return []

    result = []
    for level, label, idx in [(0.1, "q10", 0), (0.5, "q50", 1), (0.9, "q90", 2)]:
        below = sum(1 for r in rows if r[3] is not None and r[idx] is not None and float(r[3]) <= float(r[idx]))
        total = sum(1 for r in rows if r[3] is not None and r[idx] is not None)
        observed = below / total if total > 0 else 0
        result.append({"predicted": level, "observed": observed, "count": total, "label": label})
    return result


def fetch_predictions_vs_actuals(
    conn: psycopg.Connection,
    model_name: str,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Fetch prediction-actual pairs for scatter plot."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.symbol, p.prediction_date,
                   (p.prediction_values->>'q50')::NUMERIC, po.actual_return
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            JOIN stocks s ON p.data_id = s.id
            JOIN prediction_outcomes po ON po.data_id = p.data_id
                AND po.prediction_date = p.prediction_date
                AND po.horizon_days = p.horizon_days
            WHERE p.prediction_type = 'quantile' AND m.name = %s
                AND po.actual_return IS NOT NULL
            ORDER BY p.prediction_date DESC LIMIT %s
        """, (model_name, limit))
        return [
            {"symbol": r[0], "date": str(r[1]), "predicted": float(r[2]), "actual": float(r[3])}
            for r in cur.fetchall()
        ]


def fetch_pipeline_health(conn: psycopg.Connection) -> Dict[str, Any]:
    """Fetch data freshness, feature coverage, prediction distributions.

    Uses fast queries — avoids full table scans on hypertables.
    """
    from datetime import date as d
    result: Dict[str, Any] = {"freshness": [], "coverage": {}, "predictions": []}
    with conn.cursor() as cur:
        # Single query for all freshness data (fast — MAX on indexed columns)
        cur.execute("""
            SELECT 'OHLCV', MAX(date) FROM stock_ohlcv
            UNION ALL
            SELECT 'Features', MAX(date) FROM computed_features
            UNION ALL
            SELECT 'Predictions', MAX(prediction_date) FROM predictions
        """)
        for name, latest in cur.fetchall():
            if latest:
                result["freshness"].append({"name": name, "days_old": (d.today() - latest).days})

        # Feature coverage — use stocks table count (fast) vs approximate feature coverage
        # Instead of expensive COUNT(DISTINCT data_id) on hypertable, check recent features only
        cur.execute("SELECT COUNT(*) FROM stocks")
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(DISTINCT data_id) FROM computed_features
            WHERE date >= CURRENT_DATE - INTERVAL '30 days'
        """)
        computed = cur.fetchone()[0]
        result["coverage"] = {"computed": computed, "total": total}

        # Prediction counts by type (fast — small result set)
        cur.execute("SELECT prediction_type, COUNT(*) FROM predictions GROUP BY prediction_type")
        result["predictions"] = [{"type": r[0], "count": r[1]} for r in cur.fetchall()]
    return result


def fetch_confusion_matrix(
    conn: psycopg.Connection,
    model_name: str,
) -> Dict[str, Any]:
    """Fetch confusion matrix for trend classifier."""
    labels = ["strong_down", "weak_down", "neutral", "weak_up", "strong_up"]
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.prediction_values->>'predicted_class', po.actual_return
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            JOIN prediction_outcomes po ON po.data_id = p.data_id
                AND po.prediction_date = p.prediction_date
                AND po.horizon_days = p.horizon_days
            WHERE p.prediction_type = 'trend_class' AND m.name = %s
                AND po.actual_return IS NOT NULL
        """, (model_name,))
        rows = cur.fetchall()

    def classify(ret):
        r = float(ret) * 100
        if r < -3: return "strong_down"
        if r < -1: return "weak_down"
        if r < 1: return "neutral"
        if r < 3: return "weak_up"
        return "strong_up"

    matrix = [[0]*5 for _ in range(5)]
    for predicted, actual_ret in rows:
        actual_class = classify(actual_ret)
        if predicted in labels and actual_class in labels:
            matrix[labels.index(actual_class)][labels.index(predicted)] += 1
    return {"labels": labels, "matrix": matrix}
