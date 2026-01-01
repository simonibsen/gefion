"""ML Pipeline page - Model training, predictions, and evaluation."""

import streamlit as st
import subprocess
import sys
import json
import os
from datetime import datetime, date, timedelta


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
                cur.execute("""
                    SELECT d.id, d.name, d.version, d.created_at,
                           d.universe, d.horizons_days,
                           (SELECT COUNT(*) FROM ml_models m WHERE m.dataset_id = d.id) as model_count
                    FROM ml_datasets d
                    ORDER BY d.created_at DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "version": r[2],
                        "created_at": r[3],
                        "universe": r[4],
                        "horizons": r[5],
                        "model_count": r[6],
                    }
                    for r in rows
                ]
    except Exception:
        return []


@st.cache_data(ttl=30)
def _get_models() -> list[dict]:
    """Get list of trained models from database."""
    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m.id, m.name, m.version, m.model_type, m.algorithm,
                           m.created_at, d.name as dataset_name, d.version as dataset_version
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
                        "model_type": r[3],
                        "algorithm": r[4],
                        "created_at": r[5],
                        "dataset_name": r[6],
                        "dataset_version": r[7],
                    }
                    for r in rows
                ]
    except Exception:
        return []


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
            help="Name for the dataset (e.g., 'nasdaq_v1')",
        )

        dataset_version = st.text_input(
            "Version",
            value=datetime.now().strftime("%Y%m%d"),
            help="Version identifier (e.g., date stamp)",
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

    if st.button("🔨 Build Dataset", type="primary", use_container_width=True):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        horizons_str = ",".join(str(h) for h in horizons)
        weak_str = ",".join([f"{weak_threshold/100:.2f}"] * len(horizons))
        strong_str = ",".join([f"{strong_threshold/100:.2f}"] * len(horizons))

        cmd = [
            sys.executable, "-m", "g2.cli", "ml", "dataset-build",
            "--name", dataset_name,
            "--version", dataset_version,
            "--exchange", exchange,
            "--limit", str(limit),
            "--horizons", horizons_str,
            "--weak-thresholds", weak_str,
            "--strong-thresholds", strong_str,
            "--format", export_format,
            "--export",
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
                   f"--format {export_format} --export{cli_features}")
        st.code(cli_cmd, language="bash")

        with st.status("Building dataset...", expanded=True) as status:
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
                    # Accumulate lines for multi-line JSON parsing
                    json_buffer.append(line)
                    try:
                        data = json.loads("\n".join(json_buffer))
                        json_buffer = []  # Reset buffer on successful parse
                        if not isinstance(data, dict):
                            continue
                        last_data = data
                        # Show status message if present
                        msg = data.get("message", "")
                        if msg:
                            status_text.write(msg)
                    except json.JSONDecodeError:
                        # Not yet complete JSON, keep buffering
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="✅ Dataset built!", state="complete")
                    st.success(f"Dataset {dataset_name} v{dataset_version} built successfully!")
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
    st.subheader("Manage Datasets")

    datasets = _get_datasets()

    if not datasets:
        st.info("No datasets found. Build one above.")
    else:
        for ds in datasets:
            col1, col2, col3 = st.columns([3, 2, 1])

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
                created_str = created.strftime("%Y-%m-%d") if created else "?"
                model_count = ds.get("model_count", 0)
                st.caption(f"Created: {created_str}")
                if model_count > 0:
                    st.caption(f"🔗 {model_count} model(s)")

            with col3:
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

        # Show CLI command
        st.code("g2 ml dataset-delete --name <name> --version <version>", language="bash")


def render_train_section():
    """Render model training section."""
    st.subheader("Train Model")

    st.info("""
    💡 **Training** builds quantile regression (q10/q50/q90) or classification models
    on your prepared dataset. Models are saved locally and registered in the database.
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
            ["Quantile Regression", "Trend Classifier"],
            help="Quantile predicts price ranges, Classifier predicts trend direction",
        )

        selected_dataset = st.selectbox(
            "Dataset",
            options=dataset_options,
            help="Select a dataset to train on",
        )
        # Parse selected dataset
        selected_idx = dataset_options.index(selected_dataset)
        dataset_name = datasets[selected_idx]["name"]
        dataset_version = datasets[selected_idx]["version"]

        st.caption(f"Training on: `{dataset_name}` version `{dataset_version}`")

        # Get horizons from selected dataset
        dataset_horizons = datasets[selected_idx].get("horizons") or [7, 30, 90]

    with col2:
        algorithm = st.selectbox(
            "Algorithm",
            ["lightgbm", "xgboost", "quantile_regression"] if model_type == "Quantile Regression"
            else ["xgboost", "lightgbm"],
            help="ML algorithm to use",
        )

        model_name = st.text_input(
            "Model Name",
            value="quantile" if model_type == "Quantile Regression" else "classifier",
            key="train_model_name",
        )

        model_version = st.text_input(
            "Model Version",
            value=datetime.now().strftime("%Y%m%d"),
            key="train_model_version",
        )

        # Horizon selection for classifier (trains one horizon at a time)
        if model_type == "Trend Classifier":
            horizon = st.selectbox(
                "Horizon (days)",
                options=dataset_horizons,
                help="Classifier trains one horizon at a time",
            )

    if st.button("🎯 Train Model", type="primary", use_container_width=True):
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        if model_type == "Quantile Regression":
            cmd = [
                sys.executable, "-m", "g2.cli", "ml", "train",
                "--dataset-name", dataset_name,
                "--dataset-version", dataset_version,
                "--model-name", model_name,
                "--model-version", model_version,
                "--algorithm", algorithm,
                "--json",
            ]
            cli_subcommand = "train"
        else:
            cmd = [
                sys.executable, "-m", "g2.cli", "ml", "train-classifier",
                "--dataset-name", dataset_name,
                "--dataset-version", dataset_version,
                "--model-name", model_name,
                "--model-version", model_version,
                "--algorithm", algorithm,
                "--horizon", str(horizon),
                "--json",
            ]
            cli_subcommand = "train-classifier"

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml {cli_subcommand} --dataset-name {dataset_name} "
                   f"--dataset-version {dataset_version} --model-name {model_name} "
                   f"--model-version {model_version} --algorithm {algorithm}")
        if model_type == "Trend Classifier":
            cli_cmd += f" --horizon {horizon}"
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
                else:
                    stderr = process.stderr.read()
                    status.update(label="❌ Training failed", state="error")
                    st.error("Training failed")
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
        model_type = models[selected_idx].get("model_type", "quantile")

        st.caption(f"Using: `{model_name}` version `{model_version}` ({model_type})")

        prediction_date = st.date_input(
            "Prediction Date",
            value=date.today(),
            help="Date to generate predictions for",
        )

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

        cmd = [
            sys.executable, "-m", "g2.cli", "ml", "predict",
            "--model-name", model_name,
            "--model-version", model_version,
            "--prediction-date", str(prediction_date),
            "--json",
        ]

        if predict_mode == "Selected Symbols":
            cmd.extend(["--symbols", ",".join(selected_symbols)])
            symbols_arg = f"--symbols {','.join(selected_symbols)}"
        else:
            cmd.extend(["--exchange", exchange, "--limit", str(pred_limit)])
            symbols_arg = f"--exchange {exchange} --limit {pred_limit}"

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml predict --model-name {model_name} --model-version {model_version} "
                   f"--prediction-date {prediction_date} {symbols_arg}")
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
                        # Update metrics if available
                        done = data.get("done", 0)
                        total = data.get("total", 0)
                        if total > 0:
                            progress_metric.metric("Progress", f"{done}/{total}")
                        symbols = data.get("symbols_processed", done)
                        symbols_metric.metric("Symbols", symbols)
                        preds = data.get("predictions_generated", 0)
                        predictions_metric.metric("Predictions", preds)
                        # Status message
                        label = data.get("label", data.get("symbol", ""))
                        if label:
                            status_text.write(f"Processing: **{label}**")
                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
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

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        s.symbol,
                        qp.prediction_date,
                        qp.horizon_days,
                        qp.q10,
                        qp.q50,
                        qp.q90,
                        m.name as model
                    FROM quantile_predictions qp
                    JOIN stocks s ON qp.data_id = s.id
                    JOIN ml_models m ON qp.model_id = m.id
                    ORDER BY qp.prediction_date DESC, s.symbol
                    LIMIT 100
                """)
                predictions = cur.fetchall()

                if predictions:
                    import pandas as pd
                    df = pd.DataFrame(
                        predictions,
                        columns=["Symbol", "Date", "Horizon", "Q10", "Q50", "Q90", "Model"]
                    )
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("No predictions found. Generate some predictions first.")

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

        cmd = [
            sys.executable, "-m", "g2.cli", "ml", "eval",
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
            results_container = st.container()

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
                        # Show horizon results as they come in
                        horizon = data.get("horizon")
                        if horizon and "q50_coverage" in data:
                            with results_container:
                                st.write(f"**Horizon {horizon}d**: Q50={data.get('q50_coverage', 0):.1%}")
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
                        mp.q10_coverage,
                        mp.q50_coverage,
                        mp.q90_coverage,
                        mp.pinball_loss,
                        mp.evaluated_at
                    FROM model_performance mp
                    JOIN ml_models m ON mp.model_id = m.id
                    ORDER BY mp.evaluated_at DESC
                    LIMIT 50
                """)
                performance = cur.fetchall()

                if performance:
                    import pandas as pd
                    df = pd.DataFrame(
                        performance,
                        columns=["Model", "Version", "Horizon", "Q10 Cov", "Q50 Cov", "Q90 Cov", "Pinball", "Evaluated"]
                    )
                    st.dataframe(df, use_container_width=True)

                    st.caption("""
                    **Target coverages:** Q10=10%, Q50=50%, Q90=90%
                    Lower pinball loss = better calibration
                    """)
                else:
                    st.info("No performance data. Run evaluations to populate.")

    except Exception as e:
        st.error(f"Error loading performance: {e}")
