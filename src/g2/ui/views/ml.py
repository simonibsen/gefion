"""ML Pipeline page - Model training, predictions, and evaluation."""

import streamlit as st
import subprocess
import sys
import json
import os
from datetime import datetime, date, timedelta


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
            "--json",
        ]

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml dataset-build --name {dataset_name} --version {dataset_version} "
                   f"--exchange {exchange} --limit {limit} --horizons {horizons_str} "
                   f"--format {export_format}")
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
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            status_text.write(str(data))
                            continue
                        last_data = data
                        msg = data.get("message", data.get("status", ""))
                        if msg:
                            status_text.write(msg)
                    except json.JSONDecodeError:
                        status_text.write(line)

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


def render_train_section():
    """Render model training section."""
    st.subheader("Train Model")

    st.info("""
    💡 **Training** builds quantile regression (q10/q50/q90) or classification models
    on your prepared dataset. Models are saved locally and registered in the database.
    """)

    col1, col2 = st.columns(2)

    with col1:
        model_type = st.selectbox(
            "Model Type",
            ["Quantile Regression", "Trend Classifier"],
            help="Quantile predicts price ranges, Classifier predicts trend direction",
        )

        dataset_name = st.text_input(
            "Dataset Name",
            value="training",
            key="train_dataset_name",
        )

        dataset_version = st.text_input(
            "Dataset Version",
            value=datetime.now().strftime("%Y%m%d"),
            key="train_dataset_version",
        )

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
                "--json",
            ]
            cli_subcommand = "train-classifier"

        # Show equivalent CLI command
        cli_cmd = (f"g2 ml {cli_subcommand} --dataset-name {dataset_name} "
                   f"--dataset-version {dataset_version} --model-name {model_name} "
                   f"--model-version {model_version} --algorithm {algorithm}")
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
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            status_text.write(str(data))
                            continue
                        last_data = data
                        # Show training progress
                        msg = data.get("message", data.get("status", ""))
                        horizon = data.get("horizon")
                        quantile = data.get("quantile")
                        if horizon and quantile:
                            status_text.write(f"Training horizon {horizon}d, quantile {quantile}...")
                        elif msg:
                            status_text.write(msg)
                    except json.JSONDecodeError:
                        status_text.write(line)

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
    try:
        from g2.ui.components.database import get_models
        models = get_models()
        model_options = [f"{m['name']} v{m['version']}" for m in models]
    except Exception:
        models = []
        model_options = []

    col1, col2 = st.columns(2)

    with col1:
        if model_options:
            selected_model = st.selectbox(
                "Model",
                model_options,
                help="Select a trained model",
            )
            # Parse model name and version
            if selected_model:
                parts = selected_model.rsplit(" v", 1)
                model_name = parts[0]
                model_version = parts[1] if len(parts) > 1 else ""
        else:
            st.warning("No models found. Train a model first.")
            model_name = st.text_input("Model Name", key="pred_model_name")
            model_version = st.text_input("Model Version", key="pred_model_version")

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
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            status_text.write(str(data))
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
                        status_text.write(line)

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

    try:
        from g2.ui.components.database import get_models
        models = get_models()
        model_options = [f"{m['name']} v{m['version']}" for m in models]
    except Exception:
        model_options = []

    col1, col2 = st.columns(2)

    with col1:
        if model_options:
            selected_model = st.selectbox(
                "Model to Evaluate",
                model_options,
                key="eval_model",
            )
            parts = selected_model.rsplit(" v", 1)
            model_name = parts[0]
            model_version = parts[1] if len(parts) > 1 else ""
        else:
            model_name = st.text_input("Model Name", key="eval_model_name")
            model_version = st.text_input("Model Version", key="eval_model_version")

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
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            status_text.write(str(data))
                            continue
                        last_data = data
                        msg = data.get("message", data.get("status", ""))
                        if msg:
                            status_text.write(msg)
                        # Show horizon results as they come in
                        horizon = data.get("horizon")
                        if horizon and "q50_coverage" in data:
                            with results_container:
                                st.write(f"**Horizon {horizon}d**: Q50={data.get('q50_coverage', 0):.1%}")
                    except json.JSONDecodeError:
                        status_text.write(line)

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
