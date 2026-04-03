"""Features management page - Manage feature definitions and functions."""

import streamlit as st
import subprocess
import sys
from gefion.ui.components.chat import render_chat_widget
import os
import json
import time
from pathlib import Path
from typing import Optional

from gefion.observability import create_span, set_attributes
from gefion.ui.views.data import (
    get_process_state, start_background_process,
    render_process_status, stop_process, clear_process_state,
)


def get_page_context():
    """Return compact context dict for the Features page."""
    context = {"page_name": "Features", "summary": "Technical indicator and cross-sectional feature management."}
    try:
        from gefion.ui.components.database import get_connection
        with create_span("ui.features.get_page_context"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Feature definitions with details
                    cur.execute(
                        "SELECT name, function_name, active FROM feature_definitions "
                        "ORDER BY active DESC, name LIMIT 30"
                    )
                    definitions = []
                    for r in cur.fetchall():
                        name, fn, is_active = r[0], r[1], r[2]
                        if name.startswith("exp_"):
                            label = "experimental" if not is_active else "promoted"
                            definitions.append(f"{name} (fn: {fn}, {label})")
                        else:
                            definitions.append(f"{name} (fn: {fn}, {'active' if is_active else 'inactive'})")

                    # Feature functions
                    cur.execute(
                        "SELECT name, version, enabled, status FROM feature_functions "
                        "ORDER BY enabled DESC, name"
                    )
                    functions = []
                    for r in cur.fetchall():
                        name, ver, enabled, status = r[0], r[1], r[2], r[3] if len(r) > 3 else "active"
                        if status == "experimental":
                            functions.append(f"{name} v{ver} (experimental)")
                        else:
                            functions.append(f"{name} v{ver} ({'enabled' if enabled else 'disabled'})")

                    cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE active = true")
                    active = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM feature_definitions")
                    total = cur.fetchone()[0]

                    # Coverage stats — use TimescaleDB chunk stats (parent n_live_tup is always 0)
                    from gefion.ui.components.database import hypertable_approx_row_count
                    approx_feature_rows = hypertable_approx_row_count(cur, 'computed_features')
                    has_features = approx_feature_rows > 0
                    if has_features:
                        cur.execute("SELECT COUNT(*) FROM stocks")
                        symbols_with_features = cur.fetchone()[0]
                    else:
                        symbols_with_features = 0

        context["data_stats"] = {
            "active_definitions": active,
            "total_definitions": total,
            "symbols_with_computed_features": symbols_with_features,
            "definitions": definitions,
            "functions": functions,
        }
        if active == 0:
            context["empty_states"] = ["no active feature definitions"]
            context["suggestions"] = ["Import features: gefion feat-def-import --directory feature-definitions"]
    except Exception:
        pass
    return context


def get_project_root() -> Path:
    """Get the project root directory (where feature-definitions/ lives)."""
    # Navigate up from src/gefion/ui/views to project root
    return Path(__file__).parent.parent.parent.parent.parent


def render_features():
    """Render the features management page."""
    st.markdown("# :material/tune: Features")
    render_chat_widget(get_page_context())
    st.markdown("Manage feature definitions, functions, and view computed data coverage.")

    tab1, tab2, tab3, tab4 = st.tabs([":material/list_alt: Definitions", ":material/code: Functions", ":material/donut_large: Coverage", ":material/memory: Compute"])

    with tab1:
        render_definitions_tab()

    with tab2:
        render_functions_tab()

    with tab3:
        render_coverage_tab()

    with tab4:
        render_compute_tab()


def render_definitions_tab():
    """Render the feature definitions management tab."""
    st.subheader("Feature Definitions")
    st.markdown("Definitions link functions to specific parameters and storage targets.")

    # Show CLI command reference
    with st.expander("CLI Commands"):
        st.code("""# List all feature definitions
gefion feat-def-list

# Show details for a specific feature
gefion feat-def-show indicator_rsi_14

# Export definitions to JSON files
gefion feat-def-export --dir feature-definitions/

# Import definitions from JSON files
gefion feat-def-import --dir feature-definitions/""", language="bash")

    # Filters
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        search = st.text_input("Search", placeholder="Filter by name...", key="def_search")
    with col2:
        status_filter = st.selectbox("Status", ["All", "Active", "Inactive"], key="def_status")
    with col3:
        if st.button("Refresh", key="refresh_defs"):
            st.rerun()

    try:
        definitions = get_feature_definitions_full()

        # Apply filters
        if search:
            definitions = [d for d in definitions if search.lower() in d["name"].lower()]
        if status_filter == "Active":
            definitions = [d for d in definitions if d.get("active", True)]
        elif status_filter == "Inactive":
            definitions = [d for d in definitions if not d.get("active", True)]

        if definitions:
            # Separate regular and experimental features
            regular = [d for d in definitions if not d.get("name", "").startswith("exp_")]
            experimental = [d for d in definitions if d.get("name", "").startswith("exp_")]

            # Regular features — group by function
            if regular:
                functions = sorted(set(d.get("function_name", "unknown") for d in regular))
                for func in functions:
                    func_defs = [d for d in regular if d.get("function_name") == func]
                    with st.expander(f"**{func}** ({len(func_defs)} definitions)", expanded=True):
                        for defn in func_defs:
                            render_definition_row(defn)

            # Experimental features — single grouped section
            if experimental:
                st.markdown("---")
                with st.expander(
                    f":material/science: **Experimental Features** ({len(experimental)} — AI-generated, pending promotion)",
                    expanded=False,
                ):
                    st.caption(
                        "These features were generated by autonomous experiment cycles. "
                        "They are inactive until promoted after surviving FDR statistical testing."
                    )
                    for defn in experimental:
                        render_definition_row(defn)

            st.caption(f"Total: {len(definitions)} definitions ({len(regular)} active, {len(experimental)} experimental)")
        else:
            st.info("No feature definitions found.")

    except Exception as e:
        st.error(f"Error loading definitions: {e}")

    # Add new definition section
    st.markdown("---")
    render_add_definition_form()


def render_definition_row(defn: dict):
    """Render a single definition row with actions."""
    col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])

    with col1:
        active_icon = "●" if defn.get("active", True) else "○"
        st.markdown(f"{active_icon} **{defn['name']}**")

    with col2:
        params = defn.get("params", {})
        if params:
            params_str = ", ".join(f"{k}={v}" for k, v in params.items())
            st.caption(params_str[:40] + "..." if len(params_str) > 40 else params_str)

    with col3:
        if st.button("", key=f"view_{defn['name']}",  icon=":material/visibility:", help="View details"):
            st.session_state[f"show_detail_{defn['name']}"] = True

    with col4:
        if st.button("", key=f"edit_{defn['name']}",  icon=":material/edit:", help="Edit definition"):
            st.session_state[f"edit_definition_{defn['name']}"] = True

    with col5:
        if st.button("", key=f"del_{defn['name']}",  icon=":material/delete:", help="Delete definition"):
            st.session_state[f"confirm_delete_{defn['name']}"] = True

    # Show detail modal if requested
    if st.session_state.get(f"show_detail_{defn['name']}"):
        with st.container():
            st.markdown(f"### {defn['name']}")
            st.json(defn)
            if st.button("Close", key=f"close_{defn['name']}"):
                st.session_state[f"show_detail_{defn['name']}"] = False
                st.rerun()

    # Show edit form if requested
    if st.session_state.get(f"edit_definition_{defn['name']}"):
        render_edit_definition(defn)

    # Confirm delete dialog
    if st.session_state.get(f"confirm_delete_{defn['name']}"):
        st.warning(f"Delete **{defn['name']}**? This will also delete computed data.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Yes, delete", key=f"yes_del_{defn['name']}", type="primary"):
                success = delete_feature_definition(defn['name'])
                if success:
                    st.success(f"Deleted {defn['name']}")
                    st.session_state[f"confirm_delete_{defn['name']}"] = False
                    st.rerun()
                else:
                    st.error("Failed to delete")
        with col_no:
            if st.button("Cancel", key=f"no_del_{defn['name']}"):
                st.session_state[f"confirm_delete_{defn['name']}"] = False
                st.rerun()


def render_edit_definition(defn: dict):
    """Render form to edit a feature definition."""
    name = defn["name"]

    with st.container():
        st.markdown(f"### Edit: {name}")
        st.caption("Changes will be saved to JSON and imported to database.")

        # Get available functions for dropdown
        try:
            functions = get_feature_functions()
            func_names = sorted(set(f["name"] for f in functions)) if functions else []
        except Exception:
            func_names = ["indicator", "cross_sectional", "price_change"]

        col1, col2 = st.columns(2)

        with col1:
            function_name = st.selectbox(
                "Function",
                func_names if func_names else ["indicator"],
                index=func_names.index(defn.get("function_name", "indicator")) if defn.get("function_name") in func_names else 0,
                key=f"edit_func_{name}",
            )
            source_table = st.selectbox(
                "Source Table",
                ["stock_ohlcv", "computed_features"],
                index=0 if defn.get("source_table") == "stock_ohlcv" else 1,
                key=f"edit_source_{name}",
            )
            source_column = st.text_input(
                "Source Column",
                value=defn.get("source_column", "close"),
                key=f"edit_source_col_{name}",
            )

        with col2:
            params_str = st.text_area(
                "Parameters (JSON)",
                value=json.dumps(defn.get("params", {}), indent=2),
                height=100,
                key=f"edit_params_{name}",
            )
            store_table = st.selectbox(
                "Store Table",
                ["computed_features"],
                key=f"edit_store_{name}",
            )
            active = st.checkbox(
                "Active",
                value=defn.get("active", True),
                key=f"edit_active_{name}",
            )

        col_save, col_cancel = st.columns(2)

        with col_save:
            if st.button("Save", key=f"save_edit_{name}", type="primary"):
                try:
                    params = json.loads(params_str) if params_str else {}

                    # Build the definition dict
                    updated_def = {
                        "name": name,
                        "function_name": function_name,
                        "params": params,
                        "source_table": source_table,
                        "source_column": source_column,
                        "store_table": store_table,
                        "store_column": defn.get("store_column", "value"),
                        "store_type": defn.get("store_type", "double precision"),
                        "active": active,
                    }

                    # Save to JSON and import
                    success, message = save_definition_to_json(updated_def)
                    if success:
                        st.success(message)
                        st.session_state[f"edit_definition_{name}"] = False
                        st.rerun()
                    else:
                        st.error(message)

                except json.JSONDecodeError:
                    st.error("Invalid JSON in parameters")
                except Exception as e:
                    st.error(f"Error: {e}")

        with col_cancel:
            if st.button("Cancel", key=f"cancel_edit_{name}"):
                st.session_state[f"edit_definition_{name}"] = False
                st.rerun()


def render_add_definition_form():
    """Render form to add a new feature definition."""
    with st.expander(":material/add_circle: Add New Definition"):
        st.markdown("Create a new feature definition.")

        # Get available functions for dropdown
        try:
            functions = get_feature_functions()
            func_names = [f["name"] for f in functions] if functions else []
        except Exception:
            func_names = ["indicator", "cross_sectional", "price_change"]

        col1, col2 = st.columns(2)

        with col1:
            name = st.text_input("Name", placeholder="indicator_rsi_14", key="new_def_name")
            function_name = st.selectbox("Function", func_names if func_names else ["indicator"], key="new_def_func")
            source_table = st.selectbox("Source Table", ["stock_ohlcv", "computed_features"], key="new_def_source")

        with col2:
            params_str = st.text_area("Parameters (JSON)", value='{"period": 14}', key="new_def_params", height=100)
            store_table = st.selectbox("Store Table", ["computed_features"], key="new_def_store")
            active = st.checkbox("Active", value=True, key="new_def_active")

        if st.button("Create Definition", type="primary", key="create_def"):
            if not name:
                st.error("Name is required")
            else:
                try:
                    params = json.loads(params_str) if params_str else {}
                    success = create_feature_definition(
                        name=name,
                        function_name=function_name,
                        params=params,
                        source_table=source_table,
                        store_table=store_table,
                        active=active,
                    )
                    if success:
                        st.success(f"Created definition: {name}")
                        st.rerun()
                    else:
                        st.error("Failed to create definition")
                except json.JSONDecodeError:
                    st.error("Invalid JSON in parameters")
                except Exception as e:
                    st.error(f"Error: {e}")


def render_functions_tab():
    """Render the feature functions tab."""
    st.subheader("Feature Functions")
    st.markdown("Registered computation functions that definitions reference.")

    # Show CLI command reference
    with st.expander("CLI Commands"):
        st.code("""# List all registered functions
gefion feat-fx-list

# Export functions to JSON files
gefion feat-fx-export --dir feature-functions/

# Import functions from JSON files
gefion feat-fx-import --dir feature-functions/""", language="bash")

    col1, col2 = st.columns([3, 1])
    with col1:
        search = st.text_input("Search", placeholder="Filter by name...", key="func_search")
    with col2:
        if st.button("Refresh", key="refresh_funcs"):
            st.rerun()

    try:
        functions = get_feature_functions()

        if search:
            functions = [f for f in functions if search.lower() in f["name"].lower()]

        if functions:
            # Separate regular and experimental functions
            regular = [f for f in functions if f.get("status") != "experimental"]
            experimental = [f for f in functions if f.get("status") == "experimental"]

            # Regular functions
            if regular:
                for func in regular:
                    render_function_row(func)

            # Experimental functions — grouped
            if experimental:
                st.markdown("---")
                with st.expander(
                    f":material/science: **Experimental Functions** ({len(experimental)} — AI-generated)",
                    expanded=False,
                ):
                    st.caption(
                        "Generated by autonomous experiment cycles. "
                        "These become active when promoted after FDR statistical testing."
                    )
                    for func in experimental:
                        render_function_row(func)

            st.caption(f"Total: {len(functions)} functions ({len(regular)} active, {len(experimental)} experimental)")
        else:
            st.info("No feature functions found.")

    except Exception as e:
        st.error(f"Error loading functions: {e}")

    # Add new function section
    st.markdown("---")
    render_add_function_form()


def render_function_row(func: dict):
    """Render a single function row with expandable details."""
    enabled = func.get("enabled", True)
    status = func.get("status", "active")
    version = func.get("version", "v1")
    name = func.get("name", "unknown")
    func_key = f"{name}_v{version}"

    enabled_icon = "●" if enabled else "○"
    status_color = "green" if status == "active" else "yellow"

    # Row with name and edit button (visible without expanding)
    col1, col2, col3 = st.columns([4, 1, 1])

    with col1:
        st.markdown(f"{enabled_icon} **{name}** (v{version})")

    with col2:
        if st.button("", key=f"edit_func_{func_key}",  icon=":material/edit:", help="Edit function"):
            st.session_state[f"edit_function_{func_key}"] = True

    with col3:
        if st.button("", key=f"view_func_{func_key}",  icon=":material/visibility:", help="View details"):
            st.session_state[f"view_function_{func_key}"] = not st.session_state.get(f"view_function_{func_key}", False)

    # Show edit form if requested
    if st.session_state.get(f"edit_function_{func_key}"):
        render_edit_function(func)

    # Show details if requested
    elif st.session_state.get(f"view_function_{func_key}"):
        with st.container():
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(f"**Status:** :{status_color}[{status}]")
                st.markdown(f"**Language:** {func.get('language', 'python')}")

            with col2:
                st.markdown(f"**Version:** {version}")
                tags = func.get("tags", [])
                if tags:
                    st.markdown(f"**Tags:** {', '.join(tags)}")

            with col3:
                st.markdown(f"**Enabled:** {enabled_icon}")

            # Description
            if func.get("description"):
                st.markdown(f"**Description:** {func['description']}")

            # Parameter schema
            param_schema = func.get("param_schema")
            if param_schema:
                st.markdown("**Parameter Schema:**")
                st.json(param_schema)

            # Defaults
            defaults = func.get("defaults")
            if defaults:
                st.markdown("**Defaults:**")
                st.json(defaults)

            # Function body (code)
            function_body = func.get("function_body")
            if function_body:
                st.markdown("**Code:**")
                st.code(function_body, language="python")

            if st.button("Close", key=f"close_func_{func_key}"):
                st.session_state[f"view_function_{func_key}"] = False
                st.rerun()


def render_add_function_form():
    """Render form to add a new feature function."""
    with st.expander(":material/add_circle: Add New Function"):
        st.markdown("Create a new feature function.")

        col1, col2 = st.columns(2)

        with col1:
            name = st.text_input("Name", placeholder="my_indicator", key="new_func_name")
            version = st.text_input("Version", value="1.0", key="new_func_version")
            language = st.selectbox("Language", ["python", "sql"], key="new_func_lang")
            status = st.selectbox("Status", ["active", "experimental", "deprecated"], key="new_func_status")

        with col2:
            description = st.text_area("Description", placeholder="What does this function compute?", height=80, key="new_func_desc")
            tags_str = st.text_input("Tags (comma-separated)", placeholder="indicator, momentum", key="new_func_tags")
            enabled = st.checkbox("Enabled", value=True, key="new_func_enabled")

        # Function body template
        default_body = '''import pandas as pd
import numpy as np

def compute(rows, specs):
    """
    Compute feature values.

    Args:
        rows: Price data with 'date', 'close', etc.
        specs: List of specs with parameters

    Returns:
        List of dicts with date and computed values
    """
    if not rows:
        return []

    df = pd.DataFrame(rows)
    results = []

    # Your computation logic here

    return results
'''
        function_body = st.text_area(
            "Function Body (Python code)",
            value=default_body,
            height=300,
            key="new_func_body",
        )

        if st.button("Create Function", type="primary", key="create_func"):
            if not name:
                st.error("Name is required")
            elif not function_body.strip():
                st.error("Function body is required")
            else:
                try:
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

                    new_func = {
                        "name": name,
                        "version": version,
                        "language": language,
                        "status": status,
                        "enabled": enabled,
                        "description": description,
                        "tags": tags,
                        "function_body": function_body,
                        "created_by": "ui",
                    }

                    success, message = save_function_to_json(new_func)
                    if success:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)

                except Exception as e:
                    st.error(f"Error: {e}")


def render_edit_function(func: dict):
    """Render form to edit a feature function."""
    name = func.get("name", "unknown")
    version = func.get("version", "v1")
    func_key = f"{name}_v{version}"

    st.markdown("#### Edit Function")
    st.caption("Changes will be saved to JSON in feature-functions/ and imported to database.")

    col1, col2 = st.columns(2)

    with col1:
        new_version = st.text_input(
            "Version",
            value=version,
            key=f"edit_version_{func_key}",
            help="Increment version for significant changes",
        )
        language = st.selectbox(
            "Language",
            ["python", "sql"],
            index=0 if func.get("language") == "python" else 1,
            key=f"edit_lang_{func_key}",
        )
        status = st.selectbox(
            "Status",
            ["active", "deprecated", "experimental"],
            index=["active", "deprecated", "experimental"].index(func.get("status", "active")),
            key=f"edit_status_{func_key}",
        )
        enabled = st.checkbox(
            "Enabled",
            value=func.get("enabled", True),
            key=f"edit_enabled_{func_key}",
        )

    with col2:
        description = st.text_area(
            "Description",
            value=func.get("description", ""),
            height=80,
            key=f"edit_desc_{func_key}",
        )
        tags_str = st.text_input(
            "Tags (comma-separated)",
            value=", ".join(func.get("tags", [])),
            key=f"edit_tags_{func_key}",
        )
        param_schema_str = st.text_area(
            "Parameter Schema (JSON)",
            value=json.dumps(func.get("param_schema") or {}, indent=2),
            height=80,
            key=f"edit_schema_{func_key}",
        )

    # Function body - full width
    function_body = st.text_area(
        "Function Body (Python code)",
        value=func.get("function_body", ""),
        height=300,
        key=f"edit_body_{func_key}",
    )

    col_save, col_cancel = st.columns(2)

    with col_save:
        if st.button("Save", key=f"save_func_{func_key}", type="primary"):
            try:
                param_schema = json.loads(param_schema_str) if param_schema_str.strip() else None
                tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

                updated_func = {
                    "name": name,
                    "version": new_version,
                    "language": language,
                    "status": status,
                    "enabled": enabled,
                    "description": description,
                    "param_schema": param_schema,
                    "defaults": func.get("defaults"),
                    "tags": tags,
                    "function_body": function_body,
                    "created_by": func.get("created_by", "ui"),
                }

                success, message = save_function_to_json(updated_func)
                if success:
                    st.success(message)
                    st.session_state[f"edit_function_{func_key}"] = False
                    st.rerun()
                else:
                    st.error(message)

            except json.JSONDecodeError:
                st.error("Invalid JSON in parameter schema")
            except Exception as e:
                st.error(f"Error: {e}")

    with col_cancel:
        if st.button("Cancel", key=f"cancel_func_{func_key}"):
            st.session_state[f"edit_function_{func_key}"] = False
            st.rerun()


def render_coverage_tab():
    """Render the feature data coverage tab."""
    st.subheader("Feature Coverage")
    st.markdown("View computed feature data availability and statistics.")

    # Show CLI command reference
    with st.expander("CLI Commands"):
        st.code("""# Compute features for symbols
gefion feat-compute --symbols AAPL,MSFT --all-features

# Trim feature data by date
gefion feat-trim --before 2020-01-01

# Query feature data
gefion query-database --sql 'SELECT * FROM computed_features LIMIT 10'""", language="bash")

    col1, col2 = st.columns([3, 1])
    with col1:
        feature_filter = st.text_input("Filter by feature", placeholder="indicator_rsi", key="cov_filter")
    with col2:
        if st.button("Refresh", key="refresh_cov"):
            st.rerun()

    try:
        coverage = get_feature_coverage()

        if feature_filter:
            coverage = [c for c in coverage if feature_filter.lower() in c["feature_name"].lower()]

        if coverage:
            import pandas as pd

            df = pd.DataFrame(coverage)

            # Format for display
            if "row_count" in df.columns:
                df["row_count"] = df["row_count"].apply(lambda x: f"{x:,}")

            st.dataframe(df, use_container_width=True)

            # Summary stats
            total_rows = sum(c.get("row_count", 0) for c in coverage if isinstance(c.get("row_count"), int))
            st.caption(f"Total: {len(coverage)} features, {total_rows:,} computed values")

        else:
            st.info("No feature coverage data found.")

    except Exception as e:
        st.error(f"Error loading coverage: {e}")


def render_compute_tab():
    """Render the feature computation tab."""
    st.subheader("Compute Features")
    st.markdown("Run feature computation for selected symbols and features.")

    state = get_process_state("feat_compute")

    # If process is running or completed, show status and return early
    if state.is_running or state.completed:
        render_process_status("feat_compute", "Feature Compute")

        # Auto-refresh while running
        if state.is_running:
            st.caption("Auto-refreshing...")
            time.sleep(1.5)
            st.rerun()
        return  # Don't show form while process is active

    # --- Form controls ---
    col1, col2 = st.columns(2)

    with col1:
        compute_symbols = st.text_input(
            "Symbols",
            placeholder="AAPL,MSFT,GOOGL",
            help="Comma-separated symbols (leave empty for all active symbols)",
            key="compute_symbols",
        )

        all_features = st.checkbox(
            "All Features",
            value=True,
            help="Compute all active feature definitions",
            key="compute_all_features",
        )

        # Feature multiselect when not using all features
        selected_features = []
        if not all_features:
            try:
                definitions = get_feature_definitions_full()
                feature_names = sorted(d["name"] for d in definitions if d.get("active", True))
            except Exception:
                feature_names = []

            selected_features = st.multiselect(
                "Features",
                feature_names,
                help="Select specific features to compute",
                key="compute_feature_select",
            )

    with col2:
        mode = st.radio(
            "Mode",
            ["Incremental", "Full"],
            horizontal=True,
            help="Incremental: only compute missing dates. Full: recompute everything.",
            key="compute_mode",
        )

        update_existing = st.checkbox(
            "Update existing rows",
            help="Overwrite existing computed values on conflict",
            key="compute_update_existing",
        )

    # Build CLI command preview
    cli_parts = ["gefion", "feat-compute"]
    if compute_symbols:
        cli_parts.extend(["--symbols", compute_symbols.upper()])
    if all_features:
        cli_parts.append("--all-features")
    elif selected_features:
        cli_parts.extend(["--features", ",".join(selected_features)])
    if mode == "Full":
        cli_parts.append("--full")
    if update_existing:
        cli_parts.append("--update-existing")

    st.code(" ".join(cli_parts), language="bash")

    if st.button("Compute", type="primary", width="stretch", key="compute_start"):
        if not all_features and not selected_features:
            st.error("Select at least one feature or enable 'All Features'")
            return

        # Build command
        cmd = [sys.executable, "-m", "gefion.cli", "feat-compute", "--json"]
        if compute_symbols:
            cmd.extend(["--symbols", compute_symbols.upper()])
        if all_features:
            cmd.append("--all-features")
        elif selected_features:
            cmd.extend(["--features", ",".join(selected_features)])
        if mode == "Full":
            cmd.append("--full")
        if update_existing:
            cmd.append("--update-existing")

        # Set environment
        env = os.environ.copy()
        # OTEL_ENABLED inherited from parent

        # Start background process
        start_background_process("feat_compute", cmd, env)
        st.rerun()


# Database helper functions

def get_feature_definitions_full() -> list:
    """Get all feature definitions with full details."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, name, function_name, params,
                        source_table, source_column,
                        store_table, store_column, store_type,
                        active, version, created_at
                    FROM feature_definitions
                    ORDER BY function_name, name
                """)
                rows = cur.fetchall()

                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "function_name": r[2],
                        "params": r[3] or {},
                        "source_table": r[4],
                        "source_column": r[5],
                        "store_table": r[6],
                        "store_column": r[7],
                        "store_type": r[8],
                        "active": r[9],
                        "version": r[10],
                        "created_at": str(r[11]) if r[11] else None,
                    }
                    for r in rows
                ]
    except Exception:
        return []


def get_feature_functions() -> list:
    """Get all feature functions from the database."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        name, version, language, status,
                        description, param_schema, defaults,
                        function_body, tags, enabled
                    FROM feature_functions
                    ORDER BY name, version
                """)
                rows = cur.fetchall()

                return [
                    {
                        "name": r[0],
                        "version": r[1],
                        "language": r[2],
                        "status": r[3],
                        "description": r[4],
                        "param_schema": r[5],
                        "defaults": r[6],
                        "function_body": r[7],
                        "tags": r[8] or [],
                        "enabled": r[9] if r[9] is not None else True,
                    }
                    for r in rows
                ]
    except Exception:
        return []


def get_feature_coverage() -> list:
    """Get feature data coverage statistics."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        fd.name as feature_name,
                        fd.function_name,
                        COUNT(DISTINCT cf.data_id) as symbol_count,
                        COUNT(*) as row_count,
                        MIN(cf.date) as min_date,
                        MAX(cf.date) as max_date
                    FROM feature_definitions fd
                    LEFT JOIN computed_features cf ON fd.id = cf.feature_id
                    WHERE fd.active = true
                    GROUP BY fd.id, fd.name, fd.function_name
                    ORDER BY fd.name
                """)
                rows = cur.fetchall()

                return [
                    {
                        "feature_name": r[0],
                        "function_name": r[1],
                        "symbol_count": r[2] or 0,
                        "row_count": r[3] or 0,
                        "min_date": str(r[4]) if r[4] else "N/A",
                        "max_date": str(r[5]) if r[5] else "N/A",
                    }
                    for r in rows
                ]
    except Exception:
        return []


def create_feature_definition(
    name: str,
    function_name: str,
    params: dict,
    source_table: str = "stock_ohlcv",
    store_table: str = "computed_features",
    active: bool = True,
) -> bool:
    """Create a new feature definition."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO feature_definitions
                        (name, function_name, params, source_table, store_table, active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        function_name = EXCLUDED.function_name,
                        params = EXCLUDED.params,
                        source_table = EXCLUDED.source_table,
                        store_table = EXCLUDED.store_table,
                        active = EXCLUDED.active
                """, (name, function_name, json.dumps(params), source_table, store_table, active))
                conn.commit()
                return True
    except Exception as e:
        st.error(f"Database error: {e}")
        return False


def delete_feature_definition(name: str) -> bool:
    """Delete a feature definition and its computed data."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # First get the feature ID
                cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (name,))
                row = cur.fetchone()
                if not row:
                    return False

                feature_id = row[0]

                # Delete computed data
                cur.execute("DELETE FROM computed_features WHERE feature_id = %s", (feature_id,))

                # Delete the definition
                cur.execute("DELETE FROM feature_definitions WHERE id = %s", (feature_id,))

                conn.commit()
                return True
    except Exception as e:
        st.error(f"Database error: {e}")
        return False


def save_definition_to_json(defn: dict) -> tuple[bool, str]:
    """Save a feature definition to JSON file and import to database.

    Writes to feature-definitions/{name}.json and runs gefion feat-def-import.

    Returns:
        Tuple of (success, message)
    """
    try:
        name = defn["name"]
        project_root = get_project_root()
        definitions_dir = project_root / "feature-definitions"

        # Ensure directory exists
        definitions_dir.mkdir(exist_ok=True)

        # Write JSON file
        json_path = definitions_dir / f"{name}.json"
        with open(json_path, "w") as f:
            json.dump(defn, f, indent=2)
            f.write("\n")

        # Run import command
        env = os.environ.copy()
        # OTEL_ENABLED inherited from parent

        result = subprocess.run(
            [sys.executable, "-m", "gefion.cli", "feat-def-import", "--dir", str(definitions_dir)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(project_root),
        )

        if result.returncode == 0:
            return True, f"Saved {name}.json and imported to database"
        else:
            return False, f"Import failed: {result.stderr or result.stdout}"

    except Exception as e:
        return False, f"Error: {e}"


def save_function_to_json(func: dict) -> tuple[bool, str]:
    """Save a feature function to JSON file and import to database.

    Writes to feature-functions/{name}.json and runs gefion feat-fx-import.

    Returns:
        Tuple of (success, message)
    """
    try:
        name = func["name"]
        version = func.get("version", "1.0")
        project_root = get_project_root()
        functions_dir = project_root / "feature-functions"

        # Ensure directory exists
        functions_dir.mkdir(exist_ok=True)

        # Write JSON file (use name.json format like existing files)
        json_path = functions_dir / f"{name}.json"
        with open(json_path, "w") as f:
            json.dump(func, f, indent=2)
            f.write("\n")

        # Run import command
        env = os.environ.copy()
        # OTEL_ENABLED inherited from parent

        result = subprocess.run(
            [sys.executable, "-m", "gefion.cli", "feat-fx-import", "--dir", str(functions_dir)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(project_root),
        )

        if result.returncode == 0:
            return True, f"Saved {name}.json and imported to database"
        else:
            return False, f"Import failed: {result.stderr or result.stdout}"

    except Exception as e:
        return False, f"Error: {e}"
