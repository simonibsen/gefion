"""Data Management page - Update and manage market data."""

import streamlit as st
import subprocess
import sys
from gefion.ui.components.chat import render_chat_widget
import json
import os
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List
from queue import Queue, Empty
from gefion.observability import create_span, set_attributes


def get_page_context():
    """Return compact context dict for the Data Management page."""
    context = {"page_name": "Data Management", "summary": "Stock data ingestion and coverage monitoring."}
    try:
        from gefion.ui.components.database import get_connection
        from datetime import date as date_type
        with create_span("ui.data.get_page_context"):
          with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stocks")
                total_stocks = cur.fetchone()[0]

                # Fast: use pg_stat for approximate row counts (instant vs 10s+ scans)
                cur.execute("SELECT COALESCE(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = 'stock_ohlcv'")
                row = cur.fetchone()
                total_rows = row[0] if row else 0

                # Fast: MIN/MAX on indexed columns
                cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                min_date, max_date = cur.fetchone()

                # Approximate symbols with data from stocks count (if rows exist, most stocks have data)
                symbols_with_data = total_stocks if total_rows > 0 else 0

                # Sectors breakdown (fast — stocks table is small)
                cur.execute("SELECT sector, COUNT(*) FROM stocks WHERE sector IS NOT NULL GROUP BY sector ORDER BY COUNT(*) DESC LIMIT 5")
                top_sectors = [f"{r[0]} ({r[1]})" for r in cur.fetchall()]

        data_age = (date_type.today() - max_date).days if max_date else None
        context["data_stats"] = {
            "total_stocks": total_stocks,
            "symbols_with_data": symbols_with_data,
            "total_ohlcv_rows": total_rows,
            "date_range": f"{min_date} to {max_date}" if min_date else "empty",
            "data_age_days": data_age,
            "top_sectors": top_sectors,
        }
        if data_age and data_age > 3:
            context["empty_states"] = [f"data is {data_age} days old"]
            context["suggestions"] = ["Update data: gefion data-update --exchange NASDAQ"]
    except Exception:
        pass
    return context


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
    work_events: List[str] = field(default_factory=list)
    error_message: str = ""
    completed: bool = False
    success: bool = False
    # Performance metrics
    rate_per_sec: float = 0.0
    eta_seconds: float = 0.0
    successes: int = 0
    last_ok_inserted: int = 0


@st.cache_data(ttl=300)
def _get_symbol_coverage() -> list:
    """Get symbol coverage stats with 60-second cache."""
    try:
        from gefion.ui.components.database import get_connection

        with create_span("ui.data._get_symbol_coverage"):
          with get_connection() as conn:
            with conn.cursor() as cur:
                # Fast: only check recent data (last 30 days) for coverage
                # instead of scanning entire hypertable with LEFT JOIN
                cur.execute("""
                    SELECT
                        s.symbol,
                        s.sector,
                        COUNT(o.date) as days,
                        MIN(o.date) as first_date,
                        MAX(o.date) as last_date
                    FROM stocks s
                    LEFT JOIN stock_ohlcv o ON s.id = o.data_id
                        AND o.date >= CURRENT_DATE - INTERVAL '30 days'
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
    with create_span("ui.data.start_background_process", process_key=key):
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

    def read_stderr(process, state):
        """Read stderr in a background thread, storing lines as work_events."""
        try:
            for line in process.stderr:
                line = line.strip()
                if line:
                    state.work_events.append(line)
                    if len(state.work_events) > 200:
                        state.work_events.pop(0)
        except Exception:
            pass

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

            # Start stderr reader thread
            stderr_thread = threading.Thread(
                target=read_stderr, args=(process, state), daemon=True
            )
            stderr_thread.start()

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
            stderr_thread.join(timeout=2)
            state.completed = True
            state.success = returncode == 0
            if returncode != 0:
                # stderr was captured by the reader thread into work_events
                stderr_text = "\n".join(state.work_events[-10:]) if state.work_events else ""
                if stderr_text:
                    state.error_message = stderr_text
                from gefion.ui.errors import log_ui_error
                log_ui_error(
                    source="background_process",
                    message=state.error_message or f"Process exited with code {returncode}",
                    context={"key": key, "returncode": returncode},
                )

        except Exception as e:
            state.error_message = str(e)
            state.completed = True
            state.success = False
            from gefion.ui.errors import log_ui_error
            log_ui_error(source="background_process", message=str(e), context={"key": key})
        finally:
            state.is_running = False

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    return True


def render_data():
    """Render the data management page."""
    st.markdown("# :material/storage: Data Management")
    render_chat_widget(get_page_context())
    st.markdown("Manage market data, features, and database operations.")

    tab1, tab2, tab3 = st.tabs([":material/cloud_download: Update Data", ":material/monitoring: Data Status", ":material/build: Maintenance"])

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
            phase_emoji = "▸" if state.phase == "prices" else "▸" if state.phase == "features" else "▸"
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
            with st.expander("CLI Output", expanded=False):
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
            st.caption("Auto-refreshing...")
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
    cli_parts = ["gefion", "data-update", "--exchange", exchange]
    if limit:
        cli_parts.extend(["--limit", str(limit)])
    cli_parts.extend(["--timeframe", timeframe])
    if refresh:
        cli_parts.append("--refresh")
    st.code(" ".join(cli_parts), language="bash")

    if st.button("Start Update", type="primary", width="stretch"):
        # Build command
        cmd = [sys.executable, "-m", "gefion.cli", "data-update", "--json"]
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

    if st.button("Update Symbol", width="stretch") and symbol:
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        # Show equivalent CLI commands
        st.code(f"""# Fetch prices
gefion prices-ingest --symbol {symbol.upper()} --timeframe full

# Compute features
gefion feat-compute --symbols {symbol.upper()} --all-features""", language="bash")

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
                ingest_cmd = [sys.executable, "-m", "gefion.cli", "prices-ingest",
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
                    status.update(label=f"Price fetch failed", state="error")
                    st.error(f"Failed: {stderr}")
                    raise Exception("Price fetch failed")

                inserted_metric.metric("Price Records", f"{price_inserted:,}")

                # Step 2: Compute features for this symbol
                phase_display.write("Phase: **Features**")
                phase_metric.metric("Feature Values", "0")
                status_text.write(f"Computing features for {symbol.upper()}...")

                feat_cmd = [sys.executable, "-m", "gefion.cli", "feat-compute",
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
                    status.update(label=f"{symbol.upper()} updated!", state="complete")
                    st.success(
                        f"Updated {symbol.upper()}: "
                        f"{price_inserted:,} price records, "
                        f"{feature_inserted:,} feature values"
                    )
                else:
                    stderr = process.stderr.read()
                    status.update(label=f"Feature compute failed", state="error")
                    st.error(f"Failed: {stderr}")

            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error: {e}")


def render_status_section():
    """Render data status section."""
    st.subheader("Data Status")

    if st.button("Refresh Status", width="stretch"):
        # Clear caches and rerun
        from gefion.ui.components.status import get_system_stats, get_latest_data_date
        get_system_stats.clear()
        get_latest_data_date.clear()
        _get_symbol_coverage.clear()
        st.rerun()

    try:
        # Use cached stats from status component
        from gefion.ui.components.status import get_system_stats, _check_cache_invalidation

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


def _render_cull_status(state):
    """Render status for a data cull process — shows JSON output, not data-update metrics."""
    if state.is_running:
        label = "Culling data..."
        st_state = "running"
    elif state.success:
        label = "Cull complete"
        st_state = "complete"
    else:
        label = "Cull failed"
        st_state = "error"

    with st.expander(label, expanded=state.is_running):
        if state.is_running:
            st.info("Deleting data in dependency order (predictions → features → OHLCV). This may take several minutes.")

        # Parse and display JSON output
        output_lines = getattr(state, 'output_lines', [])
        for line in output_lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Display structured cull results
                if "deleted" in data:
                    deleted = data["deleted"]
                    total = data.get("total_rows", sum(deleted.values()) if isinstance(deleted, dict) else 0)
                    if isinstance(deleted, dict):
                        for table, count in deleted.items():
                            st.markdown(f"- **{table}**: {count:,} rows deleted")
                    st.markdown(f"**Total: {total:,} rows deleted**")
                elif "tables" in data:
                    # Dry-run plan
                    for table, count in data["tables"].items():
                        st.markdown(f"- **{table}**: {count:,} rows")
                elif "message" in data:
                    st.info(data["message"])
                else:
                    st.json(data)
            except json.JSONDecodeError:
                # Plain text output
                if line.startswith("✓") or line.startswith("✗"):
                    st.markdown(line)
                elif "Vacuum" in line:
                    st.markdown(f"*{line}*")

        if state.error_message:
            st.error(state.error_message)

    # Auto-refresh while running
    if state.is_running:
        import time
        time.sleep(2)
        st.rerun()


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

        # Show cull process status if running or completed
        cull_state = get_process_state("data_cull")
        if cull_state.is_running or cull_state.completed:
            _render_cull_status(cull_state)
            if cull_state.completed:
                col_clear, _ = st.columns([1, 3])
                with col_clear:
                    if st.button("Clear", key="clear_cull"):
                        clear_process_state("data_cull")
                        st.rerun()

        if st.button("Cull Data", type="secondary", disabled=cull_state.is_running):
            if not before_date and not after_date:
                st.error("Please select at least one date boundary")
            elif trim_mode != "Delete old data":
                st.warning("Only 'Delete old data' mode is supported for cascading cull. Use the CLI for other modes.")
            else:
                sym_list = [s.strip().upper() for s in symbols_filter.split(",")] if symbols_filter else None

                # Build CLI command for background execution
                cmd = [sys.executable, "-m", "gefion.cli", "data", "cull",
                       str(before_date), "--confirm", "--json"]
                if sym_list:
                    cmd.extend(["--symbols", ",".join(sym_list)])

                # Show equivalent CLI command
                cli_cmd = f"gefion data cull {before_date} --confirm"
                if sym_list:
                    cli_cmd += f" --symbols {','.join(sym_list)}"
                st.code(cli_cmd, language="bash")

                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                clear_process_state("data_cull")
                start_background_process("data_cull", cmd, env)
                st.rerun()

    with col2:
        st.markdown("### Vacuum Database")
        st.markdown("Reclaim disk space and optimize performance. Runs automatically after cull.")

        if st.button("Vacuum", type="secondary"):
            with st.spinner("Vacuuming..."):
                try:
                    from gefion.ui.components.database import get_connection
                    with get_connection() as conn:
                        conn.autocommit = True
                        with conn.cursor() as cur:
                            cur.execute("VACUUM ANALYZE")
                    st.success("Vacuum complete")
                except Exception as e:
                    st.error(f"Error: {e}")

    st.markdown("---")

    # Backup/Restore Section
    st.subheader("Backup & Restore")

    backup_tab, restore_tab = st.tabs([":material/backup: Backup", ":material/restore: Restore"])

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
    cli_parts = ["gefion", "backup", "--output", backup_path or "<path>"]
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
        if st.button("Estimate Size", width="stretch"):
            if not backup_path:
                st.error("Please specify an output directory")
            else:
                _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run=True)

    with col2:
        if st.button("Create Backup", type="primary", width="stretch"):
            if not backup_path:
                st.error("Please specify an output directory")
            else:
                _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run=False)


def _run_backup(backup_path, data_types, symbols, start_date, end_date, incremental, compress, dry_run):
    """Execute backup command."""
    cmd = [sys.executable, "-m", "gefion.cli", "backup", "--output", backup_path, "--json"]

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
                        status.update(label="Size estimate", state="complete")
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
                        status.update(label="Backup complete!", state="complete")
                        st.success(f"Backup saved to: {data.get('output_dir', backup_path)}")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('rows', 0):,} rows")
                except json.JSONDecodeError:
                    status.update(label="Complete", state="complete")
                    st.info(result.stdout)
            else:
                status.update(label="Failed", state="error")
                st.error(result.stderr or result.stdout)

        except subprocess.TimeoutExpired:
            status.update(label="Timeout", state="error")
            st.error("Backup timed out after 10 minutes")
        except Exception as e:
            status.update(label="Error", state="error")
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
    cli_parts = ["gefion", "restore", "--input", restore_path or "<path>"]
    cli_parts.extend(["--mode", restore_mode])
    if data_types_filter:
        cli_parts.extend(["--data-types", ",".join(data_types_filter)])
    if not verify:
        cli_parts.append("--no-verify")
    if dry_run_restore:
        cli_parts.append("--dry-run")

    st.code(" ".join(cli_parts), language="bash")

    if st.button("Restore Backup", type="primary", width="stretch"):
        if not restore_path:
            st.error("Please specify the backup directory")
        else:
            _run_restore(restore_path, restore_mode, data_types_filter, verify, dry_run_restore)


def _run_restore(restore_path, mode, data_types_filter, verify, dry_run):
    """Execute restore command."""
    cmd = [sys.executable, "-m", "gefion.cli", "restore", "--input", restore_path, "--json"]
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
                        status.update(label="Restore preview", state="complete")
                        st.markdown("**Would restore:**")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('rows', 0):,} rows")
                        else:
                            st.json(data)
                    else:
                        status.update(label="Restore complete!", state="complete")
                        st.success("Database restored successfully!")
                        if "tables" in data:
                            for table, info in data["tables"].items():
                                st.caption(f"  • {table}: {info.get('restored', info.get('rows', 0)):,} rows restored")
                except json.JSONDecodeError:
                    status.update(label="Complete", state="complete")
                    st.info(result.stdout)
            else:
                status.update(label="Failed", state="error")
                st.error(result.stderr or result.stdout)

        except subprocess.TimeoutExpired:
            status.update(label="Timeout", state="error")
            st.error("Restore timed out after 10 minutes")
        except Exception as e:
            status.update(label="Error", state="error")
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
