"""Data Management page - Update and manage market data."""

import streamlit as st
import subprocess
import sys
import json
import os
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List
from queue import Queue, Empty


@dataclass
class ProcessState:
    """Track state of a background process."""
    process: Optional[subprocess.Popen] = None
    is_running: bool = False
    phase: str = ""
    progress: float = 0.0
    done: int = 0
    total: int = 0
    inserted: int = 0
    errors: int = 0
    last_ok: str = ""
    workers: Optional[int] = None
    writer_workers: Optional[int] = None
    mode: str = ""
    output_lines: List[str] = field(default_factory=list)
    error_message: str = ""
    completed: bool = False
    success: bool = False
    # Performance metrics
    rate_per_sec: float = 0.0
    eta_seconds: float = 0.0
    successes: int = 0
    last_ok_inserted: int = 0


@st.cache_data(ttl=60)
def _get_symbol_coverage() -> list:
    """Get symbol coverage stats with 60-second cache."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
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
                return cur.fetchall()
    except Exception:
        return []


def get_process_state(key: str) -> ProcessState:
    """Get or create process state for a key."""
    state_key = f"process_{key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = ProcessState()
    else:
        # Migrate old ProcessState objects that don't have new fields
        old_state = st.session_state[state_key]
        if not hasattr(old_state, 'rate_per_sec'):
            # Create new state and copy over existing fields
            new_state = ProcessState()
            for field in ['process', 'is_running', 'phase', 'progress', 'done', 'total',
                          'inserted', 'errors', 'last_ok', 'workers', 'writer_workers',
                          'mode', 'error_message', 'completed', 'success']:
                if hasattr(old_state, field):
                    setattr(new_state, field, getattr(old_state, field))
            st.session_state[state_key] = new_state
    return st.session_state[state_key]


def clear_process_state(key: str):
    """Clear process state."""
    state_key = f"process_{key}"
    if state_key in st.session_state:
        st.session_state[state_key] = ProcessState()


def stop_process(key: str):
    """Stop a running process."""
    state = get_process_state(key)
    if state.process and state.is_running:
        try:
            state.process.terminate()
            state.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            state.process.kill()
        state.is_running = False
        state.error_message = "Stopped by user"


def start_background_process(key: str, cmd: list, env: dict):
    """Start a background process with state tracking."""
    state = get_process_state(key)

    # Don't start if already running
    if state.is_running:
        return False

    state.is_running = True
    state.completed = False
    state.success = False
    state.error_message = ""
    state.output_lines = []
    state.progress = 0
    state.done = 0
    state.total = 0
    state.inserted = 0
    state.errors = 0
    state.phase = ""
    state.last_ok = ""

    def run_in_thread():
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            state.process = process

            # Read output line by line
            for line in process.stdout:
                # Check if stop requested
                if not state.is_running:
                    process.terminate()
                    break

                line = line.strip()
                if not line or len(line) < 3:
                    continue

                # Store raw output (keep last 50 lines)
                state.output_lines.append(line)
                if len(state.output_lines) > 50:
                    state.output_lines.pop(0)

                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    if "_meta" in data or "summary" in data:
                        continue
                    # Update state from progress data
                    state.phase = data.get("phase", state.phase)
                    state.progress = data.get("percent", state.progress)
                    state.done = data.get("done", state.done)
                    state.total = data.get("total", state.total)
                    state.inserted = data.get("inserted_total", state.inserted)
                    state.errors = data.get("errors", state.errors)
                    state.last_ok = data.get("last_ok", state.last_ok)
                    state.workers = data.get("workers", state.workers)
                    state.writer_workers = data.get("writer_workers", state.writer_workers)
                    state.mode = data.get("mode", state.mode)
                    # Performance metrics
                    state.rate_per_sec = data.get("rate_per_sec", state.rate_per_sec)
                    state.eta_seconds = data.get("eta_seconds", state.eta_seconds)
                    state.successes = data.get("successes", state.successes)
                    state.last_ok_inserted = data.get("last_ok_inserted", state.last_ok_inserted)
                except json.JSONDecodeError:
                    pass

            returncode = process.wait()
            state.completed = True
            state.success = returncode == 0
            if returncode != 0:
                stderr = process.stderr.read()
                if stderr:
                    state.error_message = stderr
                from g2.ui.errors import log_ui_error
                log_ui_error(
                    source="background_process",
                    message=state.error_message or f"Process exited with code {returncode}",
                    context={"key": key, "returncode": returncode},
                )

        except Exception as e:
            state.error_message = str(e)
            state.completed = True
            state.success = False
            from g2.ui.errors import log_ui_error
            log_ui_error(source="background_process", message=str(e), context={"key": key})
        finally:
            state.is_running = False

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    return True


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


def render_process_status(key: str, title: str):
    """Render status for a running/completed process."""
    state = get_process_state(key)

    if not state.is_running and not state.completed:
        return False  # No active process

    # Show status container
    if state.is_running:
        status_label = f"Running: {title}"
        status_state = "running"
    elif state.completed and state.success:
        status_label = f"Completed: {title}"
        status_state = "complete"
    else:
        status_label = f"Failed: {title}"
        status_state = "error"

    with st.status(status_label, expanded=True, state=status_state):
        # Phase and progress bar
        if state.phase:
            phase_emoji = "📊" if state.phase == "prices" else "🧮" if state.phase == "features" else "⚙️"
            st.write(f"{phase_emoji} Phase: **{state.phase.title()}**")

        if state.progress > 0:
            st.progress(min(1.0, state.progress / 100.0))

        # Get performance metrics (with defaults for old session state objects)
        rate_per_sec = getattr(state, 'rate_per_sec', 0.0)
        eta_seconds = getattr(state, 'eta_seconds', 0.0)
        successes = getattr(state, 'successes', 0)
        last_ok_inserted = getattr(state, 'last_ok_inserted', 0)

        # Main metrics row
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Progress", f"{state.done}/{state.total}", f"{state.progress:.0f}%")
        col2.metric("Inserted", f"{state.inserted:,}", f"+{last_ok_inserted}" if last_ok_inserted else None)
        col3.metric("Errors", str(state.errors))

        # ETA display
        if eta_seconds > 0:
            if eta_seconds < 60:
                eta_str = f"{eta_seconds:.0f}s"
            elif eta_seconds < 3600:
                eta_str = f"{eta_seconds / 60:.1f}m"
            else:
                eta_str = f"{eta_seconds / 3600:.1f}h"
            col4.metric("ETA", eta_str)
        elif state.workers:
            col4.metric("Workers", str(state.workers))

        # Performance row
        if rate_per_sec > 0 or state.workers:
            col1, col2, col3, col4 = st.columns(4)
            if rate_per_sec > 0:
                col1.metric("Rate", f"{rate_per_sec:.1f}/s")
            if successes > 0:
                col2.metric("Successes", str(successes))
            if state.workers:
                col3.metric("Workers", str(state.workers))
            if state.mode:
                col4.metric("Mode", state.mode)

        if state.last_ok:
            st.caption(f"Last processed: **{state.last_ok}**")

        if state.error_message:
            st.error(state.error_message)

        # Show CLI output log
        output_lines = getattr(state, 'output_lines', [])
        if output_lines:
            with st.expander("📜 CLI Output", expanded=False):
                # Parse JSON objects from output (may span multiple lines)
                buffer = ""
                json_objects = []
                for line in output_lines:
                    buffer += line + "\n"
                    try:
                        data = json.loads(buffer)
                        json_objects.append(data)
                        buffer = ""
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue accumulating
                        pass

                # Show last few parsed JSON objects
                for data in json_objects[-3:]:
                    # Skip verbose meta blocks
                    if "_meta" in data:
                        continue
                    # Truncate long symbol lists
                    if "symbols" in data and len(data.get("symbols", [])) > 5:
                        data = data.copy()
                        data["symbols"] = data["symbols"][:5] + [f"... ({len(data['symbols'])} total)"]
                    st.json(data)

        # Control buttons
        col1, col2 = st.columns(2)
        if state.is_running:
            if col1.button("⏹ Stop", key=f"stop_{key}", type="secondary"):
                stop_process(key)
                st.rerun()
        if state.completed:
            if col1.button("Clear", key=f"clear_{key}", type="secondary"):
                clear_process_state(key)
                st.rerun()

    return True  # Has active/completed process


def render_update_section():
    """Render the data update section."""
    st.subheader("Update Market Data")

    state = get_process_state("data_update")

    # If process is running or completed, show status
    if state.is_running or state.completed:
        render_process_status("data_update", "Data Update")

        # Auto-refresh while running
        if state.is_running:
            st.caption("🔄 Auto-refreshing...")
            time.sleep(1.5)
            st.rerun()
        return  # Don't show update form while process is active

    st.info("""
    **Data Update** runs two phases:
    1. **Prices** - Fetches OHLCV data from AlphaVantage
    2. **Features** - Computes all active feature definitions (technical indicators,
       cross-sectional rankings, sector comparisons, etc.)
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

    # Show equivalent CLI command
    cli_parts = ["g2", "data-update", "--exchange", exchange]
    if limit:
        cli_parts.extend(["--limit", str(limit)])
    cli_parts.extend(["--timeframe", timeframe])
    if refresh:
        cli_parts.append("--refresh")
    st.code(" ".join(cli_parts), language="bash")

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

        # Start background process
        start_background_process("data_update", cmd, env)
        st.rerun()  # Refresh to show status

    st.markdown("---")

    # Single symbol update
    st.subheader("Update Single Symbol")

    symbol = st.text_input(
        "Symbol",
        placeholder="AAPL",
        help="Enter a symbol to fetch prices and compute features",
    )

    if st.button("Update Symbol", use_container_width=True) and symbol:
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        # Show equivalent CLI commands
        st.code(f"""# Fetch prices
g2 prices-ingest --symbol {symbol.upper()} --timeframe full

# Compute features
g2 feat-compute --symbols {symbol.upper()} --all-features""", language="bash")

        with st.status(f"Updating {symbol.upper()}...", expanded=True) as status:
            # Phase indicator
            phase_display = st.empty()

            # Metrics
            col1, col2, col3 = st.columns(3)
            progress_metric = col1.empty()
            inserted_metric = col2.empty()
            phase_metric = col3.empty()

            status_text = st.empty()

            try:
                # For single symbol, we need to ensure it's in the database first
                # Use prices-ingest to add/update the symbol, then data-update for features

                # Step 1: Ingest prices for the specific symbol
                phase_display.write("Phase: **Prices**")
                ingest_cmd = [sys.executable, "-m", "g2.cli", "prices-ingest",
                              "--symbol", symbol.upper(), "--timeframe", "full", "--json"]

                process = subprocess.Popen(
                    ingest_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                price_inserted = 0
                for line in process.stdout:
                    line = line.strip()
                    if not line or len(line) < 3:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            continue
                        # Skip meta/summary blocks
                        if "_meta" in data or "summary" in data:
                            continue
                        if "inserted_total" in data or "inserted" in data:
                            price_inserted = data.get("inserted_total", data.get("inserted", 0))
                            status_text.write(f"Fetched {price_inserted:,} price records...")
                    except json.JSONDecodeError:
                        # Skip JSON fragments and partial lines
                        pass

                returncode = process.wait()
                if returncode != 0:
                    stderr = process.stderr.read()
                    status.update(label=f"❌ Price fetch failed", state="error")
                    st.error(f"Failed: {stderr}")
                    raise Exception("Price fetch failed")

                inserted_metric.metric("Price Records", f"{price_inserted:,}")

                # Step 2: Compute features for this symbol
                phase_display.write("Phase: **Features**")
                phase_metric.metric("Feature Values", "0")
                status_text.write(f"Computing features for {symbol.upper()}...")

                feat_cmd = [sys.executable, "-m", "g2.cli", "feat-compute",
                            "--symbols", symbol.upper(), "--all-features", "--json"]

                process = subprocess.Popen(
                    feat_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                feature_inserted = 0
                for line in process.stdout:
                    line = line.strip()
                    if not line or len(line) < 3:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            continue
                        # Skip meta/summary blocks, only process progress updates
                        if "_meta" in data or "summary" in data:
                            continue
                        # Only update if this is a progress message with inserted_total
                        if "inserted_total" in data or "inserted" in data:
                            feature_inserted = data.get("inserted_total", data.get("inserted", 0))
                            phase_metric.metric("Feature Values", f"{feature_inserted:,}")
                            status_text.write(f"Computed {feature_inserted:,} feature values...")
                    except json.JSONDecodeError:
                        # Skip JSON fragments and partial lines
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label=f"✅ {symbol.upper()} updated!", state="complete")
                    st.success(
                        f"Updated {symbol.upper()}: "
                        f"{price_inserted:,} price records, "
                        f"{feature_inserted:,} feature values"
                    )
                else:
                    stderr = process.stderr.read()
                    status.update(label=f"❌ Feature compute failed", state="error")
                    st.error(f"Failed: {stderr}")

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")


def render_status_section():
    """Render data status section."""
    st.subheader("Data Status")

    if st.button("🔄 Refresh Status", use_container_width=True):
        # Clear caches and rerun
        from g2.ui.components.status import get_system_stats, get_latest_data_date
        get_system_stats.clear()
        get_latest_data_date.clear()
        _get_symbol_coverage.clear()
        st.rerun()

    try:
        # Use cached stats from status component
        from g2.ui.components.status import get_system_stats, _check_cache_invalidation

        _check_cache_invalidation()
        stats = get_system_stats()

        if stats is None:
            st.error("Could not load data status")
            return

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Total Stocks", stats.total_stocks)
            st.metric("Active Stocks", stats.active_stocks)

        with col2:
            st.metric("Price Records", f"{stats.ohlcv_rows:,}")
            st.metric("Feature Records", f"{stats.feature_rows:,}")

        with col3:
            st.metric("Data Start", str(stats.date_start) if stats.date_start else "N/A")
            st.metric("Data End", str(stats.date_end) if stats.date_end else "N/A")

        st.markdown("---")

        # Per-symbol stats
        st.subheader("Coverage by Symbol")

        coverage = _get_symbol_coverage()
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
        st.markdown("### Trim Data by Date")
        st.markdown("Remove price and feature data outside a date range.")

        trim_mode = st.radio(
            "Trim mode",
            ["Delete old data", "Delete recent data", "Keep date range"],
            horizontal=True,
            help="Choose which data to remove",
        )

        if trim_mode == "Delete old data":
            before_date = st.date_input(
                "Delete data before this date",
                help="All data BEFORE this date will be deleted (keeps this date and later)",
            )
            after_date = None
        elif trim_mode == "Delete recent data":
            before_date = None
            after_date = st.date_input(
                "Delete data after this date",
                help="All data AFTER this date will be deleted (keeps this date and earlier)",
            )
        else:  # Keep date range
            st.caption("Keep only data within this range, delete everything outside:")
            before_date = st.date_input(
                "Start date (delete before this)",
                key="trim_before",
                help="Data BEFORE this date will be deleted",
            )
            after_date = st.date_input(
                "End date (delete after this)",
                key="trim_after",
                help="Data AFTER this date will be deleted",
            )

        trim_features = st.checkbox(
            "Also trim features",
            value=True,
            help="Remove computed_features for the same date range",
        )

        symbols_filter = st.text_input(
            "Symbols (optional)",
            placeholder="AAPL,MSFT",
            help="Comma-separated symbols to trim (leave empty for all)",
        )

        if st.button("🗑️ Trim Data", type="secondary"):
            if not before_date and not after_date:
                st.error("Please select at least one date boundary")
            else:
                # Build command
                cmd = [sys.executable, "-m", "g2.cli", "prices-trim", "--json"]
                cli_parts = ["g2", "prices-trim"]

                if before_date:
                    cmd.extend(["--before", str(before_date)])
                    cli_parts.extend(["--before", str(before_date)])
                if after_date:
                    cmd.extend(["--after", str(after_date)])
                    cli_parts.extend(["--after", str(after_date)])
                if symbols_filter:
                    cmd.extend(["--symbols", symbols_filter.upper()])
                    cli_parts.extend(["--symbols", symbols_filter.upper()])
                if not trim_features:
                    cmd.append("--no-trim-features")
                    cli_parts.append("--no-trim-features")

                # Show CLI command
                st.code(" ".join(cli_parts), language="bash")

                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                with st.status("Trimming data...", expanded=True) as status:
                    phase_display = st.empty()
                    col1, col2 = st.columns(2)
                    prices_metric = col1.empty()
                    features_metric = col2.empty()
                    status_text = st.empty()

                    try:
                        # Step 1: Estimate rows to delete
                        phase_display.write("Phase: **Estimating**")
                        status_text.write("Counting rows to delete...")

                        from g2.ui.components.database import get_connection
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                # Build date conditions
                                date_conds = []
                                params = []
                                if before_date:
                                    date_conds.append("date < %s")
                                    params.append(str(before_date))
                                if after_date:
                                    date_conds.append("date > %s")
                                    params.append(str(after_date))
                                date_where = " AND ".join(date_conds) if date_conds else "1=1"

                                # Count prices
                                if symbols_filter:
                                    sym_list = [s.strip().upper() for s in symbols_filter.split(",")]
                                    sym_placeholders = ",".join(["%s"] * len(sym_list))
                                    cur.execute(f"""
                                        SELECT COUNT(*) FROM stock_ohlcv o
                                        JOIN stocks s ON o.data_id = s.id
                                        WHERE ({date_where}) AND s.symbol IN ({sym_placeholders})
                                    """, params + sym_list)
                                else:
                                    cur.execute(f"SELECT COUNT(*) FROM stock_ohlcv WHERE {date_where}", params)
                                price_count = cur.fetchone()[0]
                                prices_metric.metric("Price Rows", f"~{price_count:,}")

                                if trim_features:
                                    if symbols_filter:
                                        cur.execute(f"""
                                            SELECT COUNT(*) FROM computed_features cf
                                            JOIN stocks s ON cf.data_id = s.id
                                            WHERE ({date_where.replace('date', 'cf.date')}) AND s.symbol IN ({sym_placeholders})
                                        """, params + sym_list)
                                    else:
                                        cur.execute(f"SELECT COUNT(*) FROM computed_features WHERE {date_where}", params)
                                    feature_count = cur.fetchone()[0]
                                    features_metric.metric("Feature Rows", f"~{feature_count:,}")

                        # Step 2: Delete prices
                        phase_display.write("Phase: **Deleting Prices**")
                        status_text.write("Deleting price rows...")

                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            env=env,
                        )

                        if result.returncode == 0:
                            # Parse the complete JSON output
                            try:
                                data = json.loads(result.stdout)
                                deleted = data.get("deleted_prices", 0)
                                deleted_features = data.get("deleted_features", 0)
                                prices_metric.metric("Price Rows", f"{deleted:,}", "deleted")
                                if trim_features:
                                    features_metric.metric("Feature Rows", f"{deleted_features:,}", "deleted")
                                phase_display.write("Phase: **Complete**")
                                status.update(label="✅ Trim complete!", state="complete")
                                st.success(
                                    f"Deleted {deleted:,} price rows"
                                    + (f", {deleted_features:,} feature rows" if trim_features else "")
                                )
                            except json.JSONDecodeError:
                                status.update(label="✅ Trim complete!", state="complete")
                                st.info(result.stdout)
                        else:
                            status.update(label="❌ Trim failed", state="error")
                            st.error(f"Failed: {result.stderr}")

                    except Exception as e:
                        status.update(label="❌ Error", state="error")
                        st.error(f"Error: {e}")

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

    # Backup/Restore Section
    st.subheader("Backup & Restore")

    backup_tab, restore_tab = st.tabs(["💾 Backup", "📥 Restore"])

    with backup_tab:
        render_backup_section()

    with restore_tab:
        render_restore_section()


def render_backup_section():
    """Render backup controls."""
    st.markdown("Create a backup of database tables to Parquet files.")

    col1, col2 = st.columns(2)

    with col1:
        backup_path = st.text_input(
            "Output Directory",
            value="",
            placeholder="/path/to/backup",
            help="Directory to save backup files (will be created if needed)",
        )

        data_types = st.multiselect(
            "Data Types",
            ["all", "ohlcv", "features", "definitions", "functions", "strategies", "ml", "predictions", "experiments", "meta"],
            default=["all"],
            help="Select which data types to backup ('all' includes everything)",
        )

        symbols = st.text_input(
            "Symbols (optional)",
            placeholder="AAPL,MSFT",
            help="Comma-separated symbols to backup (leave empty for all)",
            key="backup_symbols",
        )

    with col2:
        start_date = st.date_input(
            "Start Date (optional)",
            value=None,
            help="Only backup data after this date",
            key="backup_start",
        )

        end_date = st.date_input(
            "End Date (optional)",
            value=None,
            help="Only backup data before this date",
            key="backup_end",
        )

        col2a, col2b = st.columns(2)
        with col2a:
            incremental = st.checkbox(
                "Incremental",
                help="Only backup data since last backup",
            )
        with col2b:
            compress = st.checkbox(
                "Compress",
                value=True,
                help="Compress output files",
            )

    # Build CLI command preview
    cli_parts = ["g2", "backup", "--output", backup_path or "<path>"]
    if data_types and data_types != ["all"]:
        cli_parts.extend(["--data-types", ",".join(data_types)])
    if symbols:
        cli_parts.extend(["--symbols", symbols.upper()])
    if start_date:
        cli_parts.extend(["--start-date", str(start_date)])
    if end_date:
        cli_parts.extend(["--end-date", str(end_date)])
    if incremental:
        cli_parts.append("--incremental")
    if not compress:
        cli_parts.append("--no-compress")

    st.code(" ".join(cli_parts), language="bash")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📊 Estimate Size", use_container_width=True):
            if not backup_path:
                st.error("Please specify an output directory")
            else:
                _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run=True)

    with col2:
        if st.button("💾 Create Backup", type="primary", use_container_width=True):
            if not backup_path:
                st.error("Please specify an output directory")
            else:
                _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run=False)


def _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run):
    """Execute backup command."""
    cmd = [sys.executable, "-m", "g2.cli", "backup", "--output", backup_path, "--json"]

    if data_types:
        cmd.extend(["--data-types", ",".join(data_types)])
    if symbols:
        cmd.extend(["--symbols", symbols.upper()])
    if start_date:
        cmd.extend(["--start-date", str(start_date)])
    if end_date:
        cmd.extend(["--end-date", str(end_date)])
    if incremental:
        cmd.append("--incremental")
    if not compress:
        cmd.append("--no-compress")
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    action = "Estimating size" if dry_run else "Creating backup"
    with st.status(f"{action}...", expanded=True) as status:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)

            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    if dry_run:
                        status.update(label="✅ Size estimate", state="complete")
                        # Show estimate details
                        if "estimate" in data:
                            est = data["estimate"]
                            st.metric("Total Rows", f"{est.get('total_rows', 0):,}")
                            st.metric("Estimated Size", _format_bytes(est.get("total_bytes", 0)))
                            if "tables" in est:
                                st.markdown("**By Table:**")
                                for table, info in est["tables"].items():
                                    st.caption(f"  • {table}: {info.get('rows', 0):,} rows ({_format_bytes(info.get('estimated_bytes', 0))})")
                        else:
                            st.json(data)
                    else:
                        status.update(label="✅ Backup complete!", state="complete")
                        st.success(f"Backup saved to: {data.get('output_dir', backup_path)}")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('rows', 0):,} rows")
                except json.JSONDecodeError:
                    status.update(label="✅ Complete", state="complete")
                    st.info(result.stdout)
            else:
                status.update(label="❌ Failed", state="error")
                st.error(result.stderr or result.stdout)

        except subprocess.TimeoutExpired:
            status.update(label="❌ Timeout", state="error")
            st.error("Backup timed out after 10 minutes")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Error: {e}")


def render_restore_section():
    """Render restore controls."""
    st.markdown("Restore database from a backup directory.")

    col1, col2 = st.columns(2)

    with col1:
        restore_path = st.text_input(
            "Backup Directory",
            value="",
            placeholder="/path/to/backup",
            help="Directory containing backup files and manifest.json",
        )

        restore_mode = st.radio(
            "Restore Mode",
            ["merge", "replace"],
            horizontal=True,
            help="merge: skip conflicts, replace: overwrite existing data",
        )

    with col2:
        data_types_filter = st.multiselect(
            "Data Types (optional filter)",
            ["ohlcv", "features", "definitions", "functions", "strategies", "ml", "predictions", "experiments", "meta"],
            default=[],
            help="Filter which data types to restore (leave empty for all)",
        )

        col2a, col2b = st.columns(2)
        with col2a:
            verify = st.checkbox(
                "Verify integrity",
                value=True,
                help="Check backup integrity before restoring",
            )
        with col2b:
            dry_run_restore = st.checkbox(
                "Dry run",
                help="Preview what would be restored",
            )

    # Build CLI command preview
    cli_parts = ["g2", "restore", "--input", restore_path or "<path>"]
    cli_parts.extend(["--mode", restore_mode])
    if data_types_filter:
        cli_parts.extend(["--data-types", ",".join(data_types_filter)])
    if not verify:
        cli_parts.append("--no-verify")
    if dry_run_restore:
        cli_parts.append("--dry-run")

    st.code(" ".join(cli_parts), language="bash")

    if st.button("📥 Restore Backup", type="primary", use_container_width=True):
        if not restore_path:
            st.error("Please specify the backup directory")
        else:
            _run_restore(restore_path, restore_mode, data_types_filter, verify, dry_run_restore)


def _run_restore(restore_path, mode, data_types_filter, verify, dry_run):
    """Execute restore command."""
    cmd = [sys.executable, "-m", "g2.cli", "restore", "--input", restore_path, "--json"]
    cmd.extend(["--mode", mode])

    if data_types_filter:
        cmd.extend(["--data-types", ",".join(data_types_filter)])
    if not verify:
        cmd.append("--no-verify")
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    action = "Previewing restore" if dry_run else "Restoring backup"
    with st.status(f"{action}...", expanded=True) as status:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)

            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    if dry_run:
                        status.update(label="✅ Restore preview", state="complete")
                        st.markdown("**Would restore:**")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('rows', 0):,} rows")
                        else:
                            st.json(data)
                    else:
                        status.update(label="✅ Restore complete!", state="complete")
                        st.success("Database restored successfully!")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('restored', info.get('rows', 0)):,} rows restored")
                except json.JSONDecodeError:
                    status.update(label="✅ Complete", state="complete")
                    st.info(result.stdout)
            else:
                status.update(label="❌ Failed", state="error")
                st.error(result.stderr or result.stdout)

        except subprocess.TimeoutExpired:
            status.update(label="❌ Timeout", state="error")
            st.error("Restore timed out after 10 minutes")
        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Error: {e}")


def _format_bytes(size_bytes):
    """Format bytes as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
