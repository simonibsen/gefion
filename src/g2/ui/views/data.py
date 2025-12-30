"""Data Management page - Update and manage market data."""

import streamlit as st
import subprocess
import sys
from datetime import datetime


def render_data():
    """Render the data management page."""
    st.title("📁 Data Management")
    st.markdown("Manage market data, features, and database operations.")

    tab1, tab2, tab3 = st.tabs(["📥 Update Data", "📊 Data Status", "🔧 Maintenance"])

    with tab1:
        render_update_section()

    with tab2:
        render_status_section()

    with tab3:
        render_maintenance_section()


def render_update_section():
    """Render the data update section."""
    st.subheader("Update Market Data")

    st.info("""
    💡 **Data Update** fetches the latest prices from AlphaVantage and computes
    all active technical indicators (RSI, MACD, Bollinger Bands, etc.)
    """)

    col1, col2 = st.columns(2)

    with col1:
        exchange = st.selectbox(
            "Exchange",
            ["NASDAQ", "NYSE"],
            help="Select exchange to update",
        )

        symbol_count = st.selectbox(
            "Symbols to Update",
            ["All", "10", "20", "50", "100", "Custom"],
            help="Number of symbols to update",
        )

        if symbol_count == "All":
            limit = None
        elif symbol_count == "Custom":
            limit = st.number_input(
                "Custom Limit",
                min_value=1,
                max_value=500,
                value=50,
            )
        else:
            limit = int(symbol_count)

    with col2:
        timeframe = st.selectbox(
            "Timeframe",
            ["auto", "compact", "full"],
            help="auto=smart update, compact=100 days, full=20+ years",
        )

        refresh = st.checkbox(
            "Refresh existing data",
            help="Re-fetch and overwrite existing data points",
        )

    if st.button("🚀 Start Update", type="primary", use_container_width=True):
        with st.spinner("Updating data... This may take a while."):
            try:
                # Build command
                cmd = [sys.executable, "-m", "g2.cli", "data-update", "--json"]

                cmd.extend(["--exchange", exchange])

                if limit:
                    cmd.extend(["--limit", str(limit)])

                cmd.extend(["--timeframe", timeframe])

                if refresh:
                    cmd.append("--refresh")

                # Set environment
                import os
                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                # Run command
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,  # 10 minute timeout
                )

                if result.returncode == 0:
                    st.success("✅ Data update completed!")
                    with st.expander("Output"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Update failed")
                    st.code(result.stderr)

            except subprocess.TimeoutExpired:
                st.error("Update timed out after 10 minutes")
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("---")

    # Single symbol update
    st.subheader("Update Single Symbol")

    symbol = st.text_input(
        "Symbol",
        placeholder="AAPL",
        help="Enter a symbol to fetch/update",
    )

    if st.button("Update Symbol", use_container_width=True) and symbol:
        with st.spinner(f"Updating {symbol}..."):
            try:
                import os
                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                result = subprocess.run(
                    [sys.executable, "-m", "g2.cli", "prices-ingest",
                     "--symbol", symbol.upper(), "--timeframe", "full", "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=120,
                )

                if result.returncode == 0:
                    st.success(f"✅ {symbol.upper()} updated!")
                else:
                    st.error(f"Failed: {result.stderr}")

            except Exception as e:
                st.error(f"Error: {e}")


def render_status_section():
    """Render data status section."""
    st.subheader("Data Status")

    if st.button("🔄 Refresh Status", use_container_width=True):
        st.rerun()

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Overall stats
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM stocks) as total_stocks,
                        (SELECT COUNT(*) FROM stocks WHERE status = 'Active') as active_stocks,
                        (SELECT COUNT(*) FROM stock_ohlcv) as ohlcv_rows,
                        (SELECT COUNT(*) FROM computed_features) as feature_rows,
                        (SELECT MIN(date) FROM stock_ohlcv) as min_date,
                        (SELECT MAX(date) FROM stock_ohlcv) as max_date
                """)
                stats = cur.fetchone()

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric("Total Stocks", stats[0])
                    st.metric("Active Stocks", stats[1])

                with col2:
                    st.metric("Price Records", f"{stats[2]:,}")
                    st.metric("Feature Records", f"{stats[3]:,}")

                with col3:
                    st.metric("Data Start", str(stats[4]) if stats[4] else "N/A")
                    st.metric("Data End", str(stats[5]) if stats[5] else "N/A")

                st.markdown("---")

                # Per-symbol stats
                st.subheader("Coverage by Symbol")

                cur.execute("""
                    SELECT
                        s.symbol,
                        s.sector,
                        COUNT(o.date) as days,
                        MIN(o.date) as first_date,
                        MAX(o.date) as last_date
                    FROM stocks s
                    LEFT JOIN stock_ohlcv o ON s.id = o.data_id
                    WHERE s.status = 'Active'
                    GROUP BY s.id, s.symbol, s.sector
                    ORDER BY days DESC
                    LIMIT 50
                """)
                coverage = cur.fetchall()

                if coverage:
                    import pandas as pd
                    df = pd.DataFrame(
                        coverage,
                        columns=["Symbol", "Sector", "Days", "First Date", "Last Date"]
                    )
                    st.dataframe(df, use_container_width=True)

    except Exception as e:
        st.error(f"Error loading status: {e}")


def render_maintenance_section():
    """Render database maintenance section."""
    st.subheader("Database Maintenance")

    st.warning("⚠️ These operations modify the database. Use with caution.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Trim Old Data")
        st.markdown("Remove price data older than specified date.")

        trim_date = st.date_input(
            "Keep data after",
            help="Data before this date will be deleted",
        )

        if st.button("🗑️ Trim Data", type="secondary"):
            st.warning("Not implemented in UI for safety. Use CLI: `g2 prices-trim`")

    with col2:
        st.markdown("### Vacuum Database")
        st.markdown("Reclaim disk space and optimize performance.")

        if st.button("🧹 Vacuum", type="secondary"):
            with st.spinner("Vacuuming..."):
                try:
                    from g2.ui.components.database import get_connection

                    with get_connection() as conn:
                        conn.autocommit = True
                        with conn.cursor() as cur:
                            cur.execute("VACUUM ANALYZE")
                    st.success("✅ Vacuum complete")
                except Exception as e:
                    st.error(f"Error: {e}")

    st.markdown("---")

    # Feature definitions
    st.subheader("Feature Definitions")

    try:
        from g2.ui.components.database import get_feature_definitions

        features = get_feature_definitions()

        if features:
            import pandas as pd
            df = pd.DataFrame(features)
            st.dataframe(df, use_container_width=True)

            st.caption(f"Total: {len(features)} features ({sum(1 for f in features if f['active'])} active)")
    except Exception as e:
        st.error(f"Error loading features: {e}")
