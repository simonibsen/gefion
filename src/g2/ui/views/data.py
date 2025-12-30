"""Data Management page - Update and manage market data."""

import streamlit as st
import subprocess
import sys
import json
import os
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
        # Build command
        cmd = [sys.executable, "-m", "g2.cli", "data-update", "--json"]
        cmd.extend(["--exchange", exchange])
        if limit:
            cmd.extend(["--limit", str(limit)])
        cmd.extend(["--timeframe", timeframe])
        if refresh:
            cmd.append("--refresh")

        # Set environment
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        with st.status("Updating data...", expanded=True) as status:
            # Create metrics display
            col1, col2, col3, col4 = st.columns(4)
            progress_metric = col1.empty()
            inserted_metric = col2.empty()
            errors_metric = col3.empty()
            rate_metric = col4.empty()

            # Progress bar
            progress_bar = st.progress(0)
            status_text = st.empty()

            try:
                # Start process with streaming output
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                last_data = {}

                # Stream output line by line
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        last_data = data

                        # Update progress bar
                        pct = data.get("percent", 0)
                        progress_bar.progress(min(1.0, pct / 100.0))

                        # Update metrics
                        done = data.get("done", 0)
                        total = data.get("total", 0)
                        progress_metric.metric("Progress", f"{done}/{total}")
                        inserted_metric.metric("Inserted", f"{data.get('inserted_total', 0):,}")
                        errors_metric.metric("Errors", f"{data.get('errors', 0)}")

                        rate = data.get("rate_per_sec", 0)
                        eta = data.get("eta_seconds")
                        if eta and eta > 0:
                            rate_metric.metric("ETA", f"{eta:.0f}s")
                        else:
                            rate_metric.metric("Rate", f"{rate:.1f}/s")

                        # Update status text
                        label = data.get("label", "")
                        last_ok = data.get("last_ok", "")
                        if label:
                            status_text.write(f"Processing: **{label}**")
                        elif last_ok:
                            status_text.write(f"Last completed: **{last_ok}**")

                    except json.JSONDecodeError:
                        # Non-JSON output, show as-is
                        status_text.write(line)

                # Wait for process to complete
                returncode = process.wait()

                if returncode == 0:
                    progress_bar.progress(1.0)
                    status.update(label="✅ Update completed!", state="complete")

                    # Show final summary
                    if last_data:
                        st.success(
                            f"Completed: {last_data.get('successes', 0)} symbols, "
                            f"{last_data.get('inserted_total', 0):,} records inserted, "
                            f"{last_data.get('errors', 0)} errors"
                        )
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Update failed", state="error")
                    st.error("Update failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
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
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        with st.status(f"Updating {symbol.upper()}...", expanded=True) as status:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "g2.cli", "prices-ingest",
                     "--symbol", symbol.upper(), "--timeframe", "full", "--json"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                status_text = st.empty()
                last_data = {}

                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        last_data = data
                        inserted = data.get("inserted_total", data.get("inserted", 0))
                        status_text.write(f"Fetched {inserted:,} records...")
                    except json.JSONDecodeError:
                        status_text.write(line)

                returncode = process.wait()

                if returncode == 0:
                    status.update(label=f"✅ {symbol.upper()} updated!", state="complete")
                    inserted = last_data.get("inserted_total", last_data.get("inserted", 0))
                    if inserted:
                        st.success(f"Inserted {inserted:,} records for {symbol.upper()}")
                else:
                    stderr = process.stderr.read()
                    status.update(label=f"❌ Failed", state="error")
                    st.error(f"Failed: {stderr}")

            except Exception as e:
                status.update(label="❌ Error", state="error")
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
