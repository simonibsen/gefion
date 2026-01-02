"""ML Pipeline page - Model training, predictions, and evaluation."""

import streamlit as st
import subprocess
import sys
import json
import os
import re
from datetime import datetime, date, timedelta
from typing import Dict, Any


@st.cache_resource
def _detect_device() -> Dict[str, Any]:
    """Detect available compute device for ML training."""
    try:
        from g2.ml.device import detect_device
        return detect_device(return_info=True)
    except Exception:
        return {"device": "cpu", "cuda_available": False}


@st.cache_data(ttl=60)
def _get_available_features() -> list[str]:
    """Get list of available feature names from database."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name FROM feature_definitions
                    WHERE active = true
                    ORDER BY name
                """)
                return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


@st.cache_data(ttl=30)
def _get_datasets() -> list[dict]:
    """Get list of datasets from database."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get datasets (ml_datasets must exist for this view to be useful)
                cur.execute("""
                    SELECT d.id, d.name, d.version, d.created_at,
                           d.universe, d.horizons_days
                    FROM ml_datasets d
                    ORDER BY d.created_at DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()

                # Get model counts separately - ml_models may not exist yet
                model_counts = {}
                try:
                    cur.execute("""
                        SELECT dataset_id, COUNT(*) FROM ml_models GROUP BY dataset_id
                    """)
                    model_counts = {r[0]: r[1] for r in cur.fetchall()}
                except Exception:
                    pass  # ml_models table may not exist

                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "version": r[2],
                        "created_at": r[3],
                        "universe": r[4],
                        "horizons": r[5],
                        "model_count": model_counts.get(r[0], 0),
                    }
                    for r in rows
                ]
    except Exception as e:
        import logging
        logging.warning(f"Failed to get datasets: {e}")
        return []


@st.cache_data(ttl=30)
def _get_models() -> list[dict]:
    """Get list of trained models from database."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.id, m.name, m.version, m.algorithm, m.active,
                           m.created_at, m.artifact_uri,
                           d.name as dataset_name, d.version as dataset_version
                    FROM ml_models m
                    LEFT JOIN ml_datasets d ON d.id = m.dataset_id
                    ORDER BY m.created_at DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "version": r[2],
                        "algorithm": r[3],
                        "active": r[4],
                        "created_at": r[5],
                        "artifact_uri": r[6],
                        "dataset_name": r[7],
                        "dataset_version": r[8],
                    }
                    for r in rows
                ]
    except Exception:
        return []


def _render_dataset_inspection(ds: dict):
    """Render dataset inspection panel with details and dependent models."""
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    result = subprocess.run(
        [
            sys.executable, "-m", "g2.cli", "ml", "dataset-inspect",
            "--name", ds["name"],
            "--version", ds["version"],
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        st.error(f"Failed to inspect dataset: {result.stderr or result.stdout}")
        return

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        st.error("Invalid response from dataset-inspect")
        return

    with st.expander(f"📋 Dataset Details: {ds['name']} {ds['version']}", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Configuration**")
            st.write(f"- Created: {data.get('created_at', 'Unknown')}")
            universe = data.get("universe", {})
            if isinstance(universe, dict):
                if universe.get("exchange"):
                    st.write(f"- Exchange: {universe.get('exchange')}")
                if universe.get("limit"):
                    st.write(f"- Symbol limit: {universe.get('limit')}")
                if universe.get("symbols"):
                    st.write(f"- Symbols: {len(universe.get('symbols', []))}")
            horizons = data.get("horizons_days", [])
            st.write(f"- Horizons: {horizons} days")

        with col2:
            st.markdown("**Features**")
            features = data.get("feature_names", [])
            st.write(f"- Feature count: {len(features)}")
            if features:
                with st.popover("View features"):
                    for f in features:
                        st.write(f"- {f}")

            label_spec = data.get("label_spec", {})
            thresholds = label_spec.get("thresholds", {})
            if thresholds:
                st.markdown("**Thresholds**")
                for horizon, thresh in thresholds.items():
                    st.write(f"- {horizon}d: weak={thresh.get('weak')}, strong={thresh.get('strong')}")

        # Models section
        models = data.get("models", [])
        st.markdown(f"**Models using this dataset ({len(models)})**")
        if models:
            model_data = [
                {
                    "Name": m["name"],
                    "Version": m["version"],
                    "Algorithm": m.get("algorithm", "-"),
                    "Created": m.get("created_at", "-"),
                }
                for m in models
            ]
            st.dataframe(model_data, use_container_width=True, hide_index=True)
        else:
            st.info("No models trained on this dataset yet.")


def _render_feature_importance(model: dict, model_data: dict):
    """Render feature importance for a model."""
    # Get horizons from model data
    predictions = model_data.get("predictions", [])
    horizons = sorted(set(p.get("horizon_days") for p in predictions if p.get("horizon_days")))

    if not horizons:
        return

    st.markdown("**Feature Importance**")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        horizon = st.selectbox(
            "Horizon",
            options=horizons,
            format_func=lambda x: f"{x}d",
            key=f"fi_horizon_{model['id']}",
        )
    with col2:
        quantile = st.selectbox(
            "Quantile",
            options=["q50", "q10", "q90"],
            key=f"fi_quantile_{model['id']}",
        )
    with col3:
        top_k = st.number_input(
            "Top K",
            min_value=5,
            max_value=50,
            value=15,
            key=f"fi_topk_{model['id']}",
        )

    if st.button("📊 Compute", key=f"fi_compute_{model['id']}"):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        result = subprocess.run(
            [
                sys.executable, "-m", "g2.cli", "ml", "feature-importance",
                "--model-name", model["name"],
                "--model-version", model["version"],
                "--horizon", str(horizon),
                "--quantile", quantile,
                "--top-k", str(top_k),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                features = data.get("features", [])
                if features:
                    import pandas as pd
                    df = pd.DataFrame(features)
                    if "importance" in df.columns:
                        df["importance"] = df["importance"].apply(lambda x: f"{x:.4f}")
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.info("No feature importance data available.")
            except json.JSONDecodeError:
                st.code(result.stdout)
        else:
            st.error(f"Failed: {result.stderr or result.stdout}")


def _render_model_inspection(model: dict):
    """Render model inspection panel with details and predictions."""
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    result = subprocess.run(
        [
            sys.executable, "-m", "g2.cli", "ml", "model-inspect",
            "--name", model["name"],
            "--version", model["version"],
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        st.error(f"Failed to inspect model: {result.stderr}")
        return

    try:
        data = json.loads(result.stdout)
        if isinstance(data, str):
            st.warning(data)
            return
        data = data.get("data", data)
    except json.JSONDecodeError:
        st.error("Invalid response from model-inspect")
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Model Info**")
        st.write(f"- Algorithm: {data.get('algorithm', '-')}")
        st.write(f"- Active: {'Yes' if data.get('active') else 'No'}")
        st.write(f"- Created: {data.get('created_at', '-')}")
        st.write(f"- Artifact: `{data.get('artifact_uri', '-')}`")

        dataset = data.get("dataset")
        if dataset:
            st.write(f"- Dataset: {dataset.get('name')} {dataset.get('version')}")

    with col2:
        hyperparams = data.get("hyperparams", {})
        if hyperparams:
            st.markdown("**Hyperparameters**")
            for k, v in hyperparams.items():
                st.write(f"- {k}: {v}")

        metrics = data.get("metrics", {})
        if metrics:
            st.markdown("**Training Metrics**")
            for k, v in metrics.items():
                if isinstance(v, float):
                    st.write(f"- {k}: {v:.4f}")
                else:
                    st.write(f"- {k}: {v}")

    # Predictions section
    predictions = data.get("predictions", [])
    total_preds = sum(p.get("count", 0) for p in predictions)
    st.markdown(f"**Predictions ({total_preds:,} total)**")
    if predictions:
        pred_data = [
            {
                "Horizon": f"{p['horizon_days']}d",
                "Count": f"{p['count']:,}",
                "Date Range": p.get("date_range", "-"),
            }
            for p in predictions
        ]
        st.dataframe(pred_data, use_container_width=True, hide_index=True)
    else:
        st.info("No predictions generated yet.")

    # Performance section
    performance = data.get("performance", [])
    if performance:
        st.markdown("**Performance Metrics**")
        perf_data = [
            {
                "Horizon": f"{p['horizon_days']}d",
                "Q10 Calib": f"{p['q10_calibration']:.1f}%" if p.get('q10_calibration') else "-",
                "Q50 Calib": f"{p['q50_calibration']:.1f}%" if p.get('q50_calibration') else "-",
                "Q90 Calib": f"{p['q90_calibration']:.1f}%" if p.get('q90_calibration') else "-",
                "Loss": f"{p['quantile_loss']:.4f}" if p.get('quantile_loss') else "-",
            }
            for p in performance
        ]
        st.dataframe(perf_data, use_container_width=True, hide_index=True)

    # Feature importance section
    _render_feature_importance(model, data)


def render_ml():
    """Render the ML pipeline page."""
    st.title("🧠 ML Pipeline")
    st.markdown("Train models, generate predictions, and evaluate performance.")

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Dataset",
        "🎯 Train",
        "🔮 Predict",
        "📈 Evaluate"
    ])

    with tab1:
        render_dataset_section()

    with tab2:
        render_train_section()

    with tab3:
        render_predict_section()

    with tab4:
        render_evaluate_section()


def _get_next_version(name: str, base_version: str) -> str:
    """Get next available version for a dataset name."""
    datasets = _get_datasets()
    existing_versions = {ds["version"] for ds in datasets if ds["name"] == name}

    if base_version not in existing_versions:
        return base_version

    # Try incrementing with suffix: 20260101-1, 20260101-2, etc.
    for i in range(1, 100):
        candidate = f"{base_version}-{i}"
        if candidate not in existing_versions:
            return candidate

    # Fallback: use timestamp
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def render_dataset_section():
    """Render dataset building section."""
    st.subheader("Build Training Dataset")

    st.info("""
    💡 **Datasets** combine price data with computed features and labels
    for ML training. Labels are based on forward returns over specified horizons.
    """)

    col1, col2 = st.columns(2)

    with col1:
        dataset_name = st.text_input(
            "Dataset Name",
            value="training",
            key="ds_build_name",
            help="Name for the dataset (e.g., 'nasdaq_v1')",
        )

        # Track name changes to auto-update version
        prev_name = st.session_state.get("_ds_prev_name", "")
        base_version = datetime.now().strftime("%Y%m%d")

        # Auto-update version when name changes or on first load
        if prev_name != dataset_name or "ds_build_version" not in st.session_state:
            st.session_state["_ds_prev_name"] = dataset_name
            st.session_state["ds_build_version"] = _get_next_version(dataset_name, base_version)

        dataset_version = st.text_input(
            "Version",
            key="ds_build_version",
            help="Version identifier. Auto-updates when name changes to avoid conflicts.",
        )

        exchange = st.selectbox(
            "Exchange",
            ["NASDAQ", "NYSE"],
            help="Exchange to include in dataset",
        )

    with col2:
        limit = st.number_input(
            "Symbol Limit",
            min_value=10,
            max_value=500,
            value=100,
            help="Number of symbols to include",
        )

        horizons = st.multiselect(
            "Prediction Horizons (days)",
            [7, 14, 30, 60, 90],
            default=[7, 30, 90],
            help="Forward-looking periods for labels",
        )

        lookback_days = st.number_input(
            "Lookback Days",
            min_value=50,
            max_value=500,
            value=200,
            help="Rolling window for feature computation. Longer = more history but fewer samples.",
        )

        export_format = st.selectbox(
            "Export Format",
            ["parquet", "csv"],
            help="parquet is faster, csv is more portable",
        )

    st.markdown("##### Label Thresholds")
    st.caption("Define what constitutes 'weak' and 'strong' moves for classification")

    col1, col2 = st.columns(2)
    with col1:
        weak_threshold = st.slider(
            "Weak Move (%)",
            min_value=1.0,
            max_value=10.0,
            value=2.0,
            step=0.5,
            help="Threshold for weak_up/weak_down labels",
        )
    with col2:
        strong_threshold = st.slider(
            "Strong Move (%)",
            min_value=5.0,
            max_value=30.0,
            value=5.0,
            step=1.0,
            help="Threshold for strong_up/strong_down labels",
        )

    # Feature selection
    with st.expander("🔧 Feature Selection (optional)"):
        available_features = _get_available_features()

        if not available_features:
            st.warning("No features found. Run data update first to compute features.")
            feature_include = []
            feature_exclude = []
        else:
            st.caption(f"{len(available_features)} features available")

            feature_mode = st.radio(
                "Selection mode",
                ["Use all features", "Include only specific features", "Exclude specific features"],
                horizontal=True,
                help="Choose how to filter features for the dataset",
            )

            if feature_mode == "Include only specific features":
                feature_include = st.multiselect(
                    "Features to include",
                    available_features,
                    help="Only these features will be included in the dataset",
                )
                feature_exclude = []
            elif feature_mode == "Exclude specific features":
                feature_exclude = st.multiselect(
                    "Features to exclude",
                    available_features,
                    help="These features will be excluded from the dataset",
                )
                feature_include = []
            else:
                feature_include = []
                feature_exclude = []

    # Check if dataset already exists
    dataset_exists = False
    for ds in _get_datasets():
        if ds["name"] == dataset_name and ds["version"] == dataset_version:
            dataset_exists = True
            break

    # Show info and confirm checkbox if dataset exists
    confirm_overwrite = True
    if dataset_exists:
        st.info(f"ℹ️ Dataset `{dataset_name}` version `{dataset_version}` already exists.")
        confirm_overwrite = st.checkbox(
            "Overwrite existing dataset",
            value=False,
            help="Check to confirm you want to replace the existing dataset.",
        )

    if st.button(
        "🔨 Build Dataset",
        type="primary",
        use_container_width=True,
        disabled=(dataset_exists and not confirm_overwrite),
    ):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env["PYTHONUNBUFFERED"] = "1"  # Ensure real-time output

        horizons_str = ",".join(str(h) for h in horizons)
        weak_str = ",".join([f"{weak_threshold/100:.2f}"] * len(horizons))
        strong_str = ",".join([f"{strong_threshold/100:.2f}"] * len(horizons))

        cmd = [
            sys.executable, "-u", "-m", "g2.cli", "ml", "dataset-build",
            "--name", dataset_name,
            "--version", dataset_version,
            "--exchange", exchange,
            "--limit", str(limit),
            "--horizons", horizons_str,
            "--lookback-days", str(lookback_days),
            "--weak-thresholds", weak_str,
            "--strong-thresholds", strong_str,
            "--format", export_format,
            "--export",
            "--force",
            "--json",
        ]

        # Add feature selection if specified
        cli_features = ""
        if feature_include:
            features_str = ",".join(feature_include)
            cmd.extend(["--features", features_str])
            cli_features = f" \\\n    --features {features_str}"
        elif feature_exclude:
            exclude_str = ",".join(feature_exclude)
            cmd.extend(["--exclude-features", exclude_str])
            cli_features = f" \\\n    --exclude-features {exclude_str}"

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml dataset-build --name {dataset_name} --version {dataset_version} "
                   f"--exchange {exchange} --limit {limit} --horizons {horizons_str} "
                   f"--lookback-days {lookback_days} --format {export_format} --export --force{cli_features}")
        st.code(cli_cmd, language="bash")

        with st.status("Building dataset...", expanded=True) as status:
            # Create placeholders for each step
            step_discover = st.empty()
            step_prices = st.empty()
            step_features = st.empty()
            step_labels = st.empty()
            step_register = st.empty()
            warnings_container = st.empty()

            steps_completed = []
            warnings_list = []

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                json_buffer = []
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    # Accumulate lines for multi-line JSON parsing
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []  # Reset buffer on successful parse
                        if not isinstance(data, dict):
                            continue
                        msg = data.get("message", "")
                        if not msg:
                            continue

                        # Track warnings
                        if "WARNING" in msg or "⚠️" in msg:
                            warnings_list.append(msg)
                            warnings_container.warning("\n".join(warnings_list))
                            continue

                        # Update step indicators based on message content
                        if "Discovered" in msg and "features" in msg:
                            step_discover.markdown(f"✅ {msg}")
                            steps_completed.append("discover")
                        elif "Exporting prices" in msg:
                            step_prices.markdown(f"⏳ {msg}")
                        elif "Exported" in msg and "price" in msg:
                            step_prices.markdown(f"✅ {msg}")
                            steps_completed.append("prices")
                        elif "Exporting features" in msg:
                            step_features.markdown(f"⏳ {msg}")
                        elif "Features exported" in msg:
                            step_features.markdown(f"✅ {msg}")
                            steps_completed.append("features")
                        elif "Computing labels" in msg:
                            step_labels.markdown(f"⏳ {msg}")
                        elif "Labels computed" in msg:
                            step_labels.markdown(f"✅ {msg}")
                            steps_completed.append("labels")
                        elif "Dataset registered" in msg:
                            step_register.markdown(f"✅ {msg}")
                            steps_completed.append("register")

                    except json.JSONDecodeError:
                        # Not yet complete JSON, keep buffering
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="✅ Dataset built!", state="complete")
                    st.success(f"Dataset {dataset_name} v{dataset_version} built successfully!")
                    # Clear cache so new dataset shows up immediately
                    _get_datasets.clear()
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Build failed", state="error")
                    st.error("Build failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")

    # Dataset management
    st.markdown("---")
    col1, col2 = st.columns([4, 1])
    with col1:
        st.subheader("Manage Datasets")
    with col2:
        if st.button("🔄", key="refresh_datasets", help="Refresh dataset list"):
            _get_datasets.clear()
            st.rerun()

    datasets = _get_datasets()

    if not datasets:
        st.info("No datasets found. Build one above.")
    else:
        for ds in datasets:
            col1, col2, col3, col4 = st.columns([3, 2, 0.5, 0.5])

            with col1:
                universe = ds.get("universe", {})
                if isinstance(universe, dict):
                    universe_str = universe.get("exchange", "") or ", ".join(universe.get("symbols", [])[:3])
                else:
                    universe_str = str(universe)[:20]
                horizons = ds.get("horizons", [])
                horizons_str = ", ".join(str(h) for h in horizons) if horizons else "?"

                st.markdown(f"**{ds['name']}** `{ds['version']}`")
                st.caption(f"{universe_str} | Horizons: {horizons_str}d")

            with col2:
                created = ds.get("created_at")
                created_str = created.strftime("%Y-%m-%d %H:%M") if created else "?"
                model_count = ds.get("model_count", 0)
                st.caption(f"Created: {created_str}")
                if model_count > 0:
                    st.caption(f"🔗 {model_count} model(s)")

            with col3:
                if st.button("🔍", key=f"inspect_{ds['id']}", help="Inspect dataset"):
                    st.session_state[f"inspecting_{ds['id']}"] = True

            with col4:
                if ds.get("model_count", 0) > 0:
                    st.button(
                        "🗑️",
                        key=f"del_{ds['id']}",
                        disabled=True,
                        help=f"Cannot delete: {ds['model_count']} model(s) depend on this dataset",
                    )
                else:
                    if st.button("🗑️", key=f"del_{ds['id']}", help="Delete dataset"):
                        # Run delete command
                        env = os.environ.copy()
                        env["OTEL_ENABLED"] = "false"
                        result = subprocess.run(
                            [
                                sys.executable, "-m", "g2.cli", "ml", "dataset-delete",
                                "--name", ds["name"],
                                "--version", ds["version"],
                                "--json",
                            ],
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        if result.returncode == 0:
                            st.success(f"Deleted {ds['name']} {ds['version']}")
                            _get_datasets.clear()
                            st.rerun()
                        else:
                            st.error(f"Delete failed: {result.stderr or result.stdout}")

            # Show inspection panel if toggled
            if st.session_state.get(f"inspecting_{ds['id']}", False):
                _render_dataset_inspection(ds)
                if st.button("Close", key=f"close_inspect_{ds['id']}"):
                    st.session_state[f"inspecting_{ds['id']}"] = False
                    st.rerun()

        # Show CLI command
        st.code("g2 ml dataset-inspect --name <name> --version <version>", language="bash")


def render_train_section():
    """Render model training section."""
    st.subheader("Train Model")

    # Device detection
    device_info = _detect_device()
    device = device_info.get("device", "cpu")
    cuda_available = device_info.get("cuda_available", False)

    if cuda_available:
        gpu_name = device_info.get("cuda_device_name", "GPU")
        st.success(f"🚀 **GPU Detected:** {gpu_name} — Training will use CUDA acceleration")
    else:
        st.warning("⚠️ **No GPU Detected** — Training will use CPU. All algorithms work fine on CPU.")

    st.info("""
    💡 **Training** builds ML models from your dataset:
    - **Quantile Regression**: Predicts price ranges (q10=downside, q50=median, q90=upside)
    - **Trend Classifier**: Predicts direction (strong_down → strong_up)
    - **Ensemble**: Combines multiple algorithms for better accuracy
    """)

    # Get available datasets for selection
    datasets = _get_datasets()
    dataset_options = [f"{ds['name']} ({ds['version']})" for ds in datasets]

    if not datasets:
        st.warning("No datasets available. Build a dataset first in the Dataset tab.")
        return

    col1, col2 = st.columns(2)

    with col1:
        model_type = st.selectbox(
            "Model Type",
            ["Quantile Regression", "Trend Classifier", "Ensemble"],
            help=(
                "**Quantile Regression**: Predicts q10/q50/q90 return ranges. Best for risk assessment. "
                "**Trend Classifier**: Predicts 5 categories (strong_down to strong_up). Good for signals. "
                "**Ensemble**: Combines multiple algorithms. More robust but slower to train."
            ),
        )

        selected_dataset = st.selectbox(
            "Dataset",
            options=dataset_options,
            help="Dataset containing features and labels. Build one in the Dataset tab if none available.",
        )
        # Parse selected dataset
        selected_idx = dataset_options.index(selected_dataset)
        dataset_name = datasets[selected_idx]["name"]
        dataset_version = datasets[selected_idx]["version"]

        st.caption(f"Training on: `{dataset_name}` version `{dataset_version}`")

        # Get horizons from selected dataset
        dataset_horizons = datasets[selected_idx].get("horizons") or [7, 30, 90]

    with col2:
        if model_type == "Quantile Regression":
            algo_options = ["lightgbm", "xgboost", "quantile_regression"]
            algorithm = st.selectbox(
                "Algorithm",
                algo_options,
                help=(
                    "**LightGBM**: Fast, GPU-accelerated, great for large datasets. "
                    "**XGBoost**: Robust, GPU-accelerated, slightly slower. "
                    "**Quantile Regression**: Simple sklearn model, CPU-only, good baseline."
                ),
            )
        elif model_type == "Trend Classifier":
            algo_options = ["xgboost", "lightgbm"]
            algorithm = st.selectbox(
                "Algorithm",
                algo_options,
                help=(
                    "**XGBoost**: Industry standard for classification. "
                    "**LightGBM**: Faster training, similar accuracy."
                ),
            )
        else:  # Ensemble
            ensemble_algos = st.multiselect(
                "Algorithms",
                ["xgboost", "lightgbm", "quantile_regression"],
                default=["xgboost", "lightgbm"],
                help=(
                    "Select 2+ algorithms to combine. Ensemble averages predictions from each. "
                    "More diverse algorithms = more robust predictions."
                ),
            )
            if len(ensemble_algos) < 2:
                st.warning("Select at least 2 algorithms for ensemble")

            # Ensemble weights (optional)
            use_custom_weights = st.checkbox(
                "Custom weights",
                value=False,
                help="By default, all algorithms are weighted equally. Enable to set custom weights.",
            )
            ensemble_weights = None
            if use_custom_weights and len(ensemble_algos) >= 2:
                st.caption(f"Set weights for each algorithm (must sum to 1.0)")
                weight_cols = st.columns(len(ensemble_algos))
                weights = []
                default_weight = round(1.0 / len(ensemble_algos), 2)
                for i, algo in enumerate(ensemble_algos):
                    with weight_cols[i]:
                        w = st.number_input(
                            algo,
                            min_value=0.0,
                            max_value=1.0,
                            value=default_weight,
                            step=0.05,
                            key=f"weight_{algo}",
                        )
                        weights.append(w)
                weight_sum = sum(weights)
                if abs(weight_sum - 1.0) > 0.01:
                    st.warning(f"Weights sum to {weight_sum:.2f}, should be 1.0")
                else:
                    ensemble_weights = weights

        model_name = st.text_input(
            "Model Name",
            value="quantile" if model_type == "Quantile Regression" else (
                "classifier" if model_type == "Trend Classifier" else "ensemble"
            ),
            key="train_model_name",
        )

        model_version = st.text_input(
            "Model Version",
            value=datetime.now().strftime("%Y%m%d"),
            key="train_model_version",
            help="Use a unique version to avoid overwriting. Tip: add a suffix like -2, -exp1, etc.",
        )

        # Check if model already exists and warn user
        existing_models = _get_models()
        existing_match = next(
            (m for m in existing_models if m["name"] == model_name and m["version"] == model_version),
            None
        )
        if existing_match:
            st.warning(
                f"⚠️ **Model `{model_name}` v`{model_version}` already exists** "
                f"(algorithm: {existing_match.get('algorithm', 'unknown')}).\n\n"
                "Training will **overwrite** it. To keep both, change the name or version."
            )

        # Horizon selection for classifier (trains one horizon at a time)
        if model_type == "Trend Classifier":
            horizon = st.selectbox(
                "Horizon (days)",
                options=dataset_horizons,
                help="Classifier trains one horizon at a time",
            )

    # Warm-start option (only for Quantile Regression with xgboost/lightgbm)
    warm_start = False
    base_model_path = None

    # Get existing models for warm-start selection
    models = _get_models()

    if model_type == "Quantile Regression" and algorithm in ["xgboost", "lightgbm"]:
        with st.expander("🚀 Warm-Start Training (Advanced)", expanded=False):
            st.info("""
            **Warm-start** continues training from an existing model instead of starting fresh.
            This is **10-100x faster** for incremental updates (e.g., new day of data).

            **Requirements:**
            - Base model must use same algorithm ({})
            - Base model must have same features
            """.format(algorithm))

            warm_start = st.checkbox(
                "Enable warm-start",
                value=False,
                key="train_warm_start",
                help="Continue training from a base model for faster incremental updates",
            )

            if warm_start:
                # Get models with matching algorithm for base model selection
                base_model_candidates = [
                    m for m in models
                    if m.get("algorithm", "").lower() == algorithm.lower()
                ]

                if not base_model_candidates:
                    st.warning(f"No existing {algorithm} models found to use as base.")
                    warm_start = False
                else:
                    base_model_options = [
                        f"{m['name']} ({m['version']})" for m in base_model_candidates
                    ]
                    selected_base = st.selectbox(
                        "Base Model",
                        base_model_options,
                        key="train_base_model",
                        help="Model to continue training from. Must use same algorithm.",
                    )

                    # Get artifact path for selected base model
                    base_idx = base_model_options.index(selected_base)
                    base_model_path = base_model_candidates[base_idx].get("artifact_uri")

                    if base_model_path:
                        st.caption(f"Base model: `{base_model_path}`")
                    else:
                        st.warning("Base model artifact path not found.")
                        warm_start = False

    # Validate ensemble has enough algorithms
    if model_type == "Ensemble" and len(ensemble_algos) < 2:
        st.error("Select at least 2 algorithms for ensemble training")
        return

    if st.button("🎯 Train Model", type="primary", use_container_width=True):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env["PYTHONUNBUFFERED"] = "1"  # Real-time output

        if model_type == "Quantile Regression":
            # Use detected device (cuda if available, else cpu)
            train_device = device if algorithm != "quantile_regression" else "cpu"
            cmd = [
                sys.executable, "-u", "-m", "g2.cli", "ml", "train",
                "--dataset-name", dataset_name,
                "--dataset-version", dataset_version,
                "--model-name", model_name,
                "--model-version", model_version,
                "--algorithm", algorithm,
                "--device", train_device,
                "--json",
            ]

            # Add warm-start flags if enabled
            warm_start_cli = ""
            if warm_start and base_model_path:
                cmd.extend(["--warm-start", "--base-model", base_model_path])
                warm_start_cli = f" \\\n    --warm-start --base-model {base_model_path}"

            cli_subcommand = "train"
            cli_cmd = (f"g2 ml {cli_subcommand} --dataset-name {dataset_name} "
                       f"--dataset-version {dataset_version} --model-name {model_name} "
                       f"--model-version {model_version} --algorithm {algorithm} "
                       f"--device {train_device}{warm_start_cli}")
        elif model_type == "Trend Classifier":
            train_device = device
            cmd = [
                sys.executable, "-u", "-m", "g2.cli", "ml", "train-classifier",
                "--dataset-name", dataset_name,
                "--dataset-version", dataset_version,
                "--model-name", model_name,
                "--model-version", model_version,
                "--device", train_device,
                "--algorithm", algorithm,
                "--horizon", str(horizon),
                "--json",
            ]
            cli_subcommand = "train-classifier"
            cli_cmd = (f"g2 ml {cli_subcommand} --dataset-name {dataset_name} "
                       f"--dataset-version {dataset_version} --model-name {model_name} "
                       f"--model-version {model_version} --algorithm {algorithm} "
                       f"--device {train_device} --horizon {horizon}")
        else:  # Ensemble
            algos_str = ",".join(ensemble_algos)
            cmd = [
                sys.executable, "-u", "-m", "g2.cli", "ml", "train-ensemble",
                "--dataset-name", dataset_name,
                "--dataset-version", dataset_version,
                "--model-name", model_name,
                "--model-version", model_version,
                "--algorithms", algos_str,
                "--json",
            ]

            # Add weights if specified
            weights_cli = ""
            if ensemble_weights:
                weights_str = ",".join(str(w) for w in ensemble_weights)
                cmd.extend(["--weights", weights_str])
                weights_cli = f" --weights {weights_str}"

            cli_subcommand = "train-ensemble"
            cli_cmd = (f"g2 ml {cli_subcommand} --dataset-name {dataset_name} "
                       f"--dataset-version {dataset_version} --model-name {model_name} "
                       f"--model-version {model_version} --algorithms {algos_str}{weights_cli}")

        st.code(cli_cmd, language="bash")

        with st.status("Training model...", expanded=True) as status:
            status_text = st.empty()
            metrics_container = st.empty()

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                last_data = {}
                json_buffer = []
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []
                        if not isinstance(data, dict):
                            continue
                        last_data = data
                        # Show training progress
                        msg = data.get("message", "")
                        horizon = data.get("horizon")
                        quantile = data.get("quantile")
                        if horizon and quantile:
                            status_text.write(f"Training horizon {horizon}d, quantile {quantile}...")
                        elif msg:
                            status_text.write(msg)
                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="✅ Model trained!", state="complete")
                    st.success(f"Model {model_name} v{model_version} trained successfully!")
                    # Clear cache so new model shows up immediately
                    _get_models.clear()
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Training failed", state="error")
                    st.error("Training failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")

    # Model management
    st.markdown("---")
    st.subheader("Manage Models")

    models = _get_models()

    if not models:
        st.info("No models found. Train one above.")
    else:
        for model in models:
            col1, col2, col3, col4 = st.columns([3, 2, 0.5, 0.5])

            with col1:
                algo = model.get("algorithm", "-")
                active_badge = "✅" if model.get("active") else "⏸️"
                st.markdown(f"**{model['name']}** `{model['version']}` {active_badge}")
                st.caption(f"{algo} | Dataset: {model.get('dataset_name', '?')} {model.get('dataset_version', '')}")

            with col2:
                created = model.get("created_at")
                created_str = created.strftime("%Y-%m-%d %H:%M") if created else "?"
                st.caption(f"Created: {created_str}")

            with col3:
                if st.button("🔍", key=f"inspect_model_{model['id']}", help="Inspect model"):
                    st.session_state[f"inspecting_model_{model['id']}"] = True

            with col4:
                if st.button("🗑️", key=f"del_model_{model['id']}", help="Delete model"):
                    # Run delete command
                    env = os.environ.copy()
                    env["OTEL_ENABLED"] = "false"
                    result = subprocess.run(
                        [
                            sys.executable, "-m", "g2.cli", "ml", "model-delete",
                            "--name", model["name"],
                            "--version", model["version"],
                            "--json",
                        ],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    if result.returncode == 0:
                        st.success(f"Deleted {model['name']} {model['version']}")
                        _get_models.clear()
                        st.rerun()
                    else:
                        st.error(f"Delete failed: {result.stderr or result.stdout}")

            # Show inspection panel if toggled
            if st.session_state.get(f"inspecting_model_{model['id']}", False):
                with st.expander(f"📋 Model Details: {model['name']} {model['version']}", expanded=True):
                    _render_model_inspection(model)
                if st.button("Close", key=f"close_inspect_model_{model['id']}"):
                    st.session_state[f"inspecting_model_{model['id']}"] = False
                    st.rerun()

        # Show CLI command
        st.code("g2 ml model-inspect --name <name> --version <version>", language="bash")

    # Hyperparameter Tuning
    st.markdown("---")
    with st.expander("🔧 Hyperparameter Tuning", expanded=False):
        _render_tune_section(datasets)


def _render_tune_section(datasets: list):
    """Render hyperparameter tuning section."""
    st.info("""
    💡 **Hyperparameter Tuning** uses Optuna (Bayesian optimization) to find optimal model settings.

    **How it works:**
    1. Runs multiple trials with different parameter combinations
    2. Uses time-series cross-validation to prevent data leakage
    3. Optimizes for pinball loss (quantile) or accuracy (classifier)
    4. Returns best parameters to use when training
    """)

    if not datasets:
        st.warning("No datasets available. Build a dataset first.")
        return

    # Filter datasets that have exported files
    from pathlib import Path
    datasets_with_files = []
    for ds in datasets:
        manifest_path = Path("datasets") / f"{ds['name']}_{ds['version']}" / "manifest.json"
        if manifest_path.exists():
            datasets_with_files.append(ds)

    if not datasets_with_files:
        st.warning(
            "No datasets with exported files found. "
            "Build a dataset with `--export` flag first."
        )
        st.code("g2 ml dataset-build --name <name> --version <version> --export", language="bash")
        return

    dataset_options = [f"{ds['name']} ({ds['version']})" for ds in datasets_with_files]
    datasets = datasets_with_files  # Use filtered list

    col1, col2 = st.columns(2)

    with col1:
        selected_dataset = st.selectbox(
            "Dataset",
            options=dataset_options,
            key="tune_dataset",
            help="Dataset with exported files (features.parquet, labels.parquet).",
        )
        selected_idx = dataset_options.index(selected_dataset)
        dataset_name = datasets[selected_idx]["name"]
        dataset_version = datasets[selected_idx]["version"]
        dataset_horizons = datasets[selected_idx].get("horizons") or [7, 30, 90]

        algorithm = st.selectbox(
            "Algorithm",
            ["xgboost", "lightgbm"],
            key="tune_algo",
            help="Algorithm to tune. sklearn quantile regression not supported (no hyperparameters).",
        )

        model_type = st.selectbox(
            "Model Type",
            ["quantile", "classifier"],
            key="tune_model_type",
            help="Quantile optimizes pinball loss. Classifier optimizes accuracy.",
        )

    with col2:
        horizon = st.selectbox(
            "Horizon (days)",
            options=dataset_horizons,
            key="tune_horizon",
            help="Prediction horizon to optimize for. Tune each horizon separately.",
        )

        if model_type == "quantile":
            quantile = st.selectbox(
                "Quantile",
                options=[0.5, 0.1, 0.9],
                format_func=lambda x: f"q{int(x*100)}",
                key="tune_quantile",
                help="q50 (median) is most important. Tune q10/q90 separately if needed.",
            )

        n_trials = st.number_input(
            "Number of Trials",
            min_value=10,
            max_value=500,
            value=50,
            key="tune_trials",
            help="More trials = better results but slower",
        )

        cv_splits = st.number_input(
            "CV Splits",
            min_value=2,
            max_value=10,
            value=5,
            key="tune_cv_splits",
            help="Number of time-series cross-validation folds. More = more robust but slower.",
        )

        timeout = st.number_input(
            "Timeout (seconds)",
            min_value=60,
            max_value=3600,
            value=300,
            key="tune_timeout",
            help="Stop after this many seconds",
        )

    if st.button("🔍 Start Tuning", type="primary", key="tune_start"):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env["PYTHONUNBUFFERED"] = "1"  # Real-time output

        cmd = [
            sys.executable, "-u", "-m", "g2.cli", "ml", "tune",
            "--dataset-name", dataset_name,
            "--dataset-version", dataset_version,
            "--algorithm", algorithm,
            "--model-type", model_type,
            "--horizon", str(horizon),
            "--n-trials", str(n_trials),
            "--cv-splits", str(cv_splits),
            "--timeout", str(timeout),
            "--json",
        ]

        if model_type == "quantile":
            cmd.extend(["--quantile", str(quantile)])

        # Show CLI command
        cli_cmd = " ".join(cmd[2:]).replace("g2.cli", "g2")
        st.code(cli_cmd, language="bash")

        with st.status("Tuning hyperparameters...", expanded=True) as status:
            status_text = st.empty()
            metrics_container = st.empty()

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                last_data = {}
                json_buffer = []
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []
                        if not isinstance(data, dict):
                            continue
                        last_data = data
                        # Show progress
                        msg = data.get("message", "")
                        if msg:
                            status_text.write(msg)
                        # Show best score if available
                        best_score = data.get("best_score")
                        trial_num = data.get("trial")
                        if best_score is not None and trial_num:
                            metrics_container.metric(
                                "Best Score",
                                f"{best_score:.4f}",
                                delta=f"Trial {trial_num}",
                            )
                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="✅ Tuning complete!", state="complete")

                    # Show best hyperparameters
                    best_params = last_data.get("best_params", {})
                    if best_params:
                        st.success("**Best Hyperparameters Found:**")
                        st.json(best_params)
                        st.info("💡 Use these parameters when training your model.")
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Tuning failed", state="error")
                    st.error("Tuning failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")


def render_predict_section():
    """Render prediction generation section."""
    st.subheader("Generate Predictions")

    st.info("""
    💡 **Predictions** use trained models to forecast price ranges (quantiles)
    or trend directions for specified symbols.
    """)

    # Get available models
    models = _get_models()

    if not models:
        st.warning("No models available. Train a model first in the Train tab.")
        return

    model_options = [f"{m['name']} ({m['version']})" for m in models]

    col1, col2 = st.columns(2)

    with col1:
        selected_model = st.selectbox(
            "Model",
            model_options,
            help="Select a trained model",
        )
        # Get selected model details
        selected_idx = model_options.index(selected_model)
        model_name = models[selected_idx]["name"]
        model_version = models[selected_idx]["version"]
        algorithm = models[selected_idx].get("algorithm", "")

        # Determine model type from algorithm (not name - algorithm is authoritative)
        algorithm_lower = algorithm.lower() if algorithm else ""
        if algorithm_lower == "ensemble" or "ensemble" in model_name.lower():
            model_type = "ensemble"
        elif algorithm_lower.startswith("classifier_"):
            model_type = "classifier"
        else:
            model_type = "quantile"

        # Get artifact_uri for classifier models (they need --model-path)
        artifact_uri = models[selected_idx].get("artifact_uri", "")

        # Show model type with explanation
        if model_type == "classifier":
            st.info(f"🏷️ **Classifier Model**: `{model_name}` v`{model_version}` ({algorithm})\n\n"
                   "Predicts trend direction (strong_up, weak_up, flat, weak_down, strong_down)")
        elif model_type == "ensemble":
            st.info(f"🎯 **Ensemble Model**: `{model_name}` v`{model_version}` ({algorithm})\n\n"
                   "Combines multiple algorithms for improved predictions")
        else:
            st.info(f"📊 **Quantile Model**: `{model_name}` v`{model_version}` ({algorithm or 'unknown'})\n\n"
                   "Predicts price ranges (q10/q50/q90 quantiles)")

        # Get latest date with features
        latest_feature_date = None
        try:
            from g2.ui.components.database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT MAX(date) FROM computed_features")
                    row = cur.fetchone()
                    if row and row[0]:
                        latest_feature_date = row[0]
        except Exception:
            pass

        # Date selection mode
        date_mode = st.radio(
            "Date Selection",
            ["Single Date", "Date Range"],
            horizontal=True,
            help="Single date for one-off predictions, date range for batch backfill",
        )

        if date_mode == "Single Date":
            prediction_date = st.date_input(
                "Prediction Date",
                value=latest_feature_date or date.today(),
                help="Date to generate predictions for. Must have computed features.",
            )
            pred_start_date = None
            pred_end_date = None

            # Warn if date is in the future or after latest features
            if latest_feature_date and prediction_date > latest_feature_date:
                st.warning(f"⚠️ Latest features are from **{latest_feature_date}**. "
                          f"Run `g2 data-update` to compute features for more recent dates.")
        else:
            pred_end_date = st.date_input(
                "End Date",
                value=latest_feature_date or date.today(),
                key="pred_range_end",
                help="End of date range (inclusive)",
            )
            pred_start_date = st.date_input(
                "Start Date",
                value=pred_end_date - timedelta(days=30),
                key="pred_range_start",
                help="Start of date range (inclusive)",
            )
            prediction_date = None

            if latest_feature_date and pred_end_date > latest_feature_date:
                st.warning(f"⚠️ Latest features are from **{latest_feature_date}**.")

    with col2:
        from g2.ui.components.database import get_symbols
        symbols = get_symbols()

        predict_mode = st.radio(
            "Prediction Mode",
            ["Selected Symbols", "Exchange"],
            help="Predict for specific symbols or entire exchange",
        )

        if predict_mode == "Selected Symbols":
            selected_symbols = st.multiselect(
                "Symbols",
                symbols,
                default=symbols[:5] if len(symbols) >= 5 else symbols,
                help="Select symbols to predict",
            )
        else:
            exchange = st.selectbox(
                "Exchange",
                ["NASDAQ", "NYSE"],
                key="pred_exchange",
            )
            pred_limit = st.number_input(
                "Limit",
                min_value=10,
                max_value=500,
                value=100,
                key="pred_limit",
            )

    if st.button("🔮 Generate Predictions", type="primary", use_container_width=True):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env["PYTHONUNBUFFERED"] = "1"  # Real-time output

        # Select correct predict command based on model type
        if model_type == "ensemble":
            predict_cmd = "predict-ensemble"
        elif model_type == "classifier":
            predict_cmd = "predict-classifier"
        else:
            predict_cmd = "predict"

        cmd = [
            sys.executable, "-u", "-m", "g2.cli", "ml", predict_cmd,
        ]

        # Classifier uses --model-path, others use --model-name/--model-version
        if model_type == "classifier":
            if not artifact_uri:
                st.error("Classifier model missing artifact_uri. Cannot generate predictions.")
                st.stop()
            cmd.extend(["--model-path", artifact_uri])
        else:
            cmd.extend(["--model-name", model_name, "--model-version", model_version])

        cmd.append("--json")

        # Add date arguments based on mode
        if date_mode == "Single Date":
            cmd.extend(["--prediction-date", str(prediction_date)])
            date_arg = f"--prediction-date {prediction_date}"
        else:
            cmd.extend(["--start-date", str(pred_start_date), "--end-date", str(pred_end_date)])
            date_arg = f"--start-date {pred_start_date} --end-date {pred_end_date}"

        if predict_mode == "Selected Symbols":
            cmd.extend(["--symbols", ",".join(selected_symbols)])
            symbols_arg = f"--symbols {','.join(selected_symbols)}"
        else:
            cmd.extend(["--exchange", exchange, "--limit", str(pred_limit)])
            symbols_arg = f"--exchange {exchange} --limit {pred_limit}"

        # Show equivalent CLI command
        if model_type == "classifier":
            model_arg = f"--model-path {artifact_uri}"
        else:
            model_arg = f"--model-name {model_name} --model-version {model_version}"
        cli_cmd = f"g2 ml {predict_cmd} {model_arg} {date_arg} {symbols_arg}"
        st.code(cli_cmd, language="bash")

        with st.status("Generating predictions...", expanded=True) as status:
            col1, col2, col3 = st.columns(3)
            progress_metric = col1.empty()
            symbols_metric = col2.empty()
            predictions_metric = col3.empty()
            status_text = st.empty()

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                last_data = {}
                json_buffer = []
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []
                        if not isinstance(data, dict):
                            continue
                        last_data = data

                        # Update metrics based on what the CLI emits
                        msg = data.get("message", "")

                        # Parse "Generating predictions for X symbols" message
                        if "symbols on" in msg:
                            match = re.search(r"(\d+) symbols", msg)
                            if match:
                                symbols_metric.metric("Symbols", int(match.group(1)))

                        # Parse "Stored X predictions for Y-day horizon" message
                        if "Stored" in msg and "predictions" in msg:
                            status_text.write(msg)

                        # Final data contains total_predictions
                        total_preds = data.get("total_predictions", 0)
                        if total_preds:
                            predictions_metric.metric("Predictions", total_preds)

                        # Show horizon progress
                        horizons = data.get("horizons", [])
                        if horizons:
                            progress_metric.metric("Horizons", len(horizons))

                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
                    # Show final totals from last_data
                    total_preds = last_data.get("total_predictions", 0)
                    if total_preds:
                        predictions_metric.metric("Predictions", total_preds)
                    status.update(label="✅ Predictions generated!", state="complete")
                    st.success("Predictions generated successfully!")
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Prediction failed", state="error")
                    st.error("Prediction failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")

    st.markdown("---")

    # View existing predictions
    st.subheader("View Predictions")

    try:
        from g2.ui.components.database import get_connection
        import pandas as pd

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get filter options
                cur.execute("""
                    SELECT DISTINCT m.name, m.version
                    FROM quantile_predictions qp
                    JOIN ml_models m ON qp.model_id = m.id
                    ORDER BY m.name, m.version DESC
                """)
                model_opts = [f"{r[0]} {r[1]}" for r in cur.fetchall()]

                cur.execute("""
                    SELECT DISTINCT prediction_date
                    FROM quantile_predictions
                    ORDER BY prediction_date DESC
                    LIMIT 30
                """)
                date_opts = [str(r[0]) for r in cur.fetchall()]

        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            filter_model = st.selectbox(
                "Model",
                ["All"] + model_opts,
                key="pred_filter_model",
            )
        with col2:
            filter_date = st.selectbox(
                "Date",
                ["All"] + date_opts,
                key="pred_filter_date",
            )
        with col3:
            filter_symbol = st.text_input(
                "Symbol",
                placeholder="e.g., AAPL",
                key="pred_filter_symbol",
            )

        # Build query with filters
        with get_connection() as conn:
            with conn.cursor() as cur:
                conditions = []
                params = []

                if filter_model != "All":
                    parts = filter_model.rsplit(" ", 1)
                    if len(parts) == 2:
                        conditions.append("m.name = %s AND m.version = %s")
                        params.extend(parts)

                if filter_date != "All":
                    conditions.append("qp.prediction_date = %s")
                    params.append(filter_date)

                if filter_symbol:
                    conditions.append("s.symbol ILIKE %s")
                    params.append(f"%{filter_symbol}%")

                where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

                cur.execute(f"""
                    SELECT
                        s.symbol,
                        qp.prediction_date,
                        qp.horizon_days,
                        qp.q10,
                        qp.q50,
                        qp.q90,
                        m.name || ' ' || m.version as model
                    FROM quantile_predictions qp
                    JOIN stocks s ON qp.data_id = s.id
                    JOIN ml_models m ON qp.model_id = m.id
                    {where_clause}
                    ORDER BY qp.prediction_date DESC, s.symbol, qp.horizon_days
                    LIMIT 200
                """, params)
                predictions = cur.fetchall()

                if predictions:
                    df = pd.DataFrame(
                        predictions,
                        columns=["Symbol", "Date", "Horizon", "Q10", "Q50", "Q90", "Model"]
                    )

                    # Format percentages
                    df["Q10"] = df["Q10"].apply(lambda x: f"{float(x):.1%}" if x else "-")
                    df["Q50"] = df["Q50"].apply(lambda x: f"{float(x):.1%}" if x else "-")
                    df["Q90"] = df["Q90"].apply(lambda x: f"{float(x):.1%}" if x else "-")
                    df["Horizon"] = df["Horizon"].apply(lambda x: f"{x}d")

                    st.dataframe(df, use_container_width=True, hide_index=True)

                    # Quick inspect
                    unique_symbols = df["Symbol"].unique().tolist()
                    if unique_symbols:
                        inspect_symbol = st.selectbox(
                            "Inspect symbol",
                            ["Select..."] + unique_symbols,
                            key="pred_inspect_symbol",
                        )
                        if inspect_symbol != "Select...":
                            st.code(f"g2 ml predict-inspect --symbol {inspect_symbol}", language="bash")
                else:
                    st.info("No predictions found matching filters.")

    except Exception as e:
        st.error(f"Error loading predictions: {e}")


def render_evaluate_section():
    """Render model evaluation section."""
    st.subheader("Evaluate Model Performance")

    st.info("""
    💡 **Evaluation** measures how well model predictions matched actual outcomes.
    Key metrics include calibration (did q10/q50/q90 predictions have correct coverage?)
    and pinball loss.
    """)

    # Get available models
    models = _get_models()

    if not models:
        st.warning("No models available. Train a model first in the Train tab.")
        return

    model_options = [f"{m['name']} ({m['version']})" for m in models]

    col1, col2 = st.columns(2)

    with col1:
        selected_model = st.selectbox(
            "Model to Evaluate",
            model_options,
            key="eval_model",
        )
        # Get selected model details
        selected_idx = model_options.index(selected_model)
        model_name = models[selected_idx]["name"]
        model_version = models[selected_idx]["version"]

        st.caption(f"Evaluating: `{model_name}` version `{model_version}`")

    with col2:
        end_date = st.date_input(
            "End Date",
            value=date.today(),
            key="eval_end",
        )
        start_date = st.date_input(
            "Start Date",
            value=end_date - timedelta(days=90),
            key="eval_start",
        )

    if st.button("📊 Evaluate", type="primary", use_container_width=True):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env["PYTHONUNBUFFERED"] = "1"  # Real-time output

        cmd = [
            sys.executable, "-u", "-m", "g2.cli", "ml", "eval",
            "--model-name", model_name,
            "--model-version", model_version,
            "--start-date", str(start_date),
            "--end-date", str(end_date),
            "--json",
        ]

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml eval --model-name {model_name} --model-version {model_version} "
                   f"--start-date {start_date} --end-date {end_date}")
        st.code(cli_cmd, language="bash")

        with st.status("Evaluating model...", expanded=True) as status:
            status_text = st.empty()

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )

                last_data = {}
                json_buffer = []
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []
                        if not isinstance(data, dict):
                            continue
                        last_data = data
                        msg = data.get("message", "")
                        if msg:
                            status_text.write(msg)
                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="✅ Evaluation complete!", state="complete")
                    st.success("Evaluation completed!")
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Evaluation failed", state="error")
                    st.error("Evaluation failed")
                    if stderr:
                        st.code(stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")

    st.markdown("---")

    # Historical performance
    st.subheader("Historical Performance")

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        m.name,
                        m.version,
                        mp.horizon_days,
                        mp.q10_calibration,
                        mp.q50_calibration,
                        mp.q90_calibration,
                        mp.quantile_loss,
                        mp.num_predictions,
                        mp.eval_start_date,
                        mp.eval_end_date
                    FROM model_performance mp
                    JOIN ml_models m ON mp.model_id = m.id
                    ORDER BY m.name, m.version, mp.horizon_days
                """)
                performance = cur.fetchall()

                if performance:
                    import pandas as pd
                    df = pd.DataFrame(
                        performance,
                        columns=["Model", "Version", "Horizon", "Q10 Cal", "Q50 Cal", "Q90 Cal", "Quantile Loss", "Samples", "Start", "End"]
                    )
                    # Format horizon as "Xd"
                    df["Horizon"] = df["Horizon"].apply(lambda x: f"{x}d")
                    # Format calibration as percentages
                    for col in ["Q10 Cal", "Q50 Cal", "Q90 Cal"]:
                        df[col] = df[col].apply(lambda x: f"{float(x):.1f}%" if x else "-")
                    # Format quantile loss
                    df["Quantile Loss"] = df["Quantile Loss"].apply(lambda x: f"{float(x):.4f}" if x else "-")
                    # Format samples with commas
                    df["Samples"] = df["Samples"].apply(lambda x: f"{x:,}" if x else "-")

                    st.dataframe(df, use_container_width=True, hide_index=True)

                    st.caption("""
                    **Calibration:** Ideal values are Q10=10%, Q50=50%, Q90=90% (fraction of actuals below prediction).
                    **Quantile Loss:** Lower = better calibrated predictions.
                    """)
                else:
                    st.info("No performance data. Run evaluations to populate.")

    except Exception as e:
        st.error(f"Error loading performance: {e}")
