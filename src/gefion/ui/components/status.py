"""Status display components."""

import streamlit as st
from dataclasses import dataclass
from typing import Optional
from datetime import date
from gefion.observability import create_span, set_attributes


@dataclass
class SystemStats:
    """Cached system statistics."""
    total_stocks: int = 0
    active_stocks: int = 0
    ohlcv_rows: int = 0
    feature_rows: int = 0
    model_count: int = 0
    prediction_count: int = 0
    date_start: Optional[date] = None
    date_end: Optional[date] = None


@st.cache_data(ttl=10)
def get_latest_data_date() -> Optional[date]:
    """Fast query to get latest data date (10-second cache)."""
    try:
        from gefion.ui.components.database import get_connection

        with create_span("ui.status.get_latest_data_date"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                    result = cur.fetchone()
                    return result[0] if result else None
    except Exception:
        return None


def _check_cache_invalidation():
    """Invalidate stats cache if data date has changed."""
    current_date = get_latest_data_date()

    # Track last known date in session state
    last_date = st.session_state.get("_stats_last_date")

    if last_date is not None and current_date != last_date:
        # Data has been updated - clear the stats cache
        get_system_stats.clear()

    st.session_state["_stats_last_date"] = current_date


@st.cache_data(ttl=60)
def get_system_stats() -> Optional[SystemStats]:
    """Get system statistics with 60-second cache."""
    try:
        from gefion.ui.components.database import get_connection

        with create_span("ui.status.get_system_stats"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM stocks")
                    total_stocks = cur.fetchone()[0]

                    cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                    active_stocks = cur.fetchone()[0]

                    # Use pg_stat approximation for large hypertables (instant vs 20s+ scans)
                    cur.execute("""
                        SELECT COALESCE(n_live_tup, 0) FROM pg_stat_user_tables
                        WHERE relname = 'stock_ohlcv'
                    """)
                    row = cur.fetchone()
                    ohlcv_rows = row[0] if row else 0

                    cur.execute("""
                        SELECT COALESCE(n_live_tup, 0) FROM pg_stat_user_tables
                        WHERE relname = 'computed_features'
                    """)
                    row = cur.fetchone()
                    feature_rows = row[0] if row else 0

                    cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                    date_range = cur.fetchone()

                    # ML tables may not exist yet - query gracefully
                    model_count = 0
                    prediction_count = 0
                    try:
                        cur.execute("SELECT COUNT(*) FROM ml_models")
                        model_count = cur.fetchone()[0]
                    except Exception:
                        pass

                    try:
                        cur.execute("""
                            SELECT COALESCE(n_live_tup, 0) FROM pg_stat_user_tables
                            WHERE relname = 'predictions'
                        """)
                        row = cur.fetchone()
                        prediction_count = row[0] if row else 0
                    except Exception:
                        pass

        return SystemStats(
            total_stocks=total_stocks,
            active_stocks=active_stocks,
            ohlcv_rows=ohlcv_rows,
            feature_rows=feature_rows,
            model_count=model_count,
            prediction_count=prediction_count,
            date_start=date_range[0],
            date_end=date_range[1],
        )
    except Exception:
        return None


def render_quick_status():
    """Render quick system status in sidebar using cached data."""
    stats = get_system_stats()

    if stats is None:
        st.error("Disconnected", icon="🔴")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Symbols", stats.active_stocks)
    with col2:
        if stats.date_end:
            st.metric("Latest", str(stats.date_end)[-5:])
        else:
            st.metric("Latest", "N/A")

    st.success("✓ Connected", icon="🟢")


def render_system_status():
    """Render detailed system status using cached data."""
    _check_cache_invalidation()
    stats = get_system_stats()

    if stats is None:
        st.error("Failed to get system status")
        return False

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Stocks", stats.total_stocks, help="All stocks in database")
        st.metric("Active Stocks", stats.active_stocks, help="Stocks with Active status")

    with col2:
        st.metric("Price Records", f"{stats.ohlcv_rows:,}", help="Total OHLCV rows")
        st.metric("Feature Records", f"{stats.feature_rows:,}", help="Computed features")

    with col3:
        st.metric("ML Models", stats.model_count, help="Trained models")
        st.metric("Predictions", f"{stats.prediction_count:,}", help="Stored predictions")

    with col4:
        if stats.date_start:
            st.metric("Data Start", str(stats.date_start))
            st.metric("Data End", str(stats.date_end))
        else:
            st.metric("Data Start", "N/A")
            st.metric("Data End", "N/A")

    st.caption("_Stats cached for 60 seconds_")
    return True
