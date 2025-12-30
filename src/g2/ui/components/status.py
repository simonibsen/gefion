"""Status display components."""

import streamlit as st


def render_quick_status():
    """Render quick system status in sidebar."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Count symbols
                cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                symbol_count = cur.fetchone()[0]

                # Get latest data date
                cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                latest_date = cur.fetchone()[0]

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Symbols", symbol_count)
        with col2:
            if latest_date:
                st.metric("Latest", str(latest_date)[-5:])
            else:
                st.metric("Latest", "N/A")

        st.success("✓ Connected", icon="🟢")

    except Exception as e:
        st.error("Disconnected", icon="🔴")


def render_system_status():
    """Render detailed system status."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Database stats
                cur.execute("SELECT COUNT(*) FROM stocks")
                total_stocks = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                active_stocks = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM stock_ohlcv")
                ohlcv_rows = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM computed_features")
                feature_rows = cur.fetchone()[0]

                cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                date_range = cur.fetchone()

                # ML stats
                cur.execute("SELECT COUNT(*) FROM ml_models")
                model_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM quantile_predictions")
                prediction_count = cur.fetchone()[0]

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Stocks", total_stocks, help="All stocks in database")
            st.metric("Active Stocks", active_stocks, help="Stocks with Active status")

        with col2:
            st.metric("Price Records", f"{ohlcv_rows:,}", help="Total OHLCV rows")
            st.metric("Feature Records", f"{feature_rows:,}", help="Computed features")

        with col3:
            st.metric("ML Models", model_count, help="Trained models")
            st.metric("Predictions", f"{prediction_count:,}", help="Stored predictions")

        with col4:
            if date_range[0]:
                st.metric("Data Start", str(date_range[0]))
                st.metric("Data End", str(date_range[1]))
            else:
                st.metric("Data Start", "N/A")
                st.metric("Data End", "N/A")

        return True

    except Exception as e:
        st.error(f"Failed to get system status: {e}")
        return False
