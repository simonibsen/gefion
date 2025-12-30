"""ML Pipeline page - Model training, predictions, and evaluation."""

import streamlit as st
import subprocess
import sys
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
        with st.spinner("Building dataset... This may take a few minutes."):
            try:
                import os
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

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )

                if result.returncode == 0:
                    st.success("✅ Dataset built successfully!")
                    with st.expander("Output"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Build failed")
                    st.code(result.stderr)

            except Exception as e:
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
        with st.spinner("Training model... This may take several minutes."):
            try:
                import os
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

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=1800,  # 30 min timeout
                )

                if result.returncode == 0:
                    st.success("✅ Model trained successfully!")
                    with st.expander("Output"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Training failed")
                    st.code(result.stderr)

            except Exception as e:
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
        with st.spinner("Generating predictions..."):
            try:
                import os
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
                else:
                    cmd.extend(["--exchange", exchange, "--limit", str(pred_limit)])

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=300,
                )

                if result.returncode == 0:
                    st.success("✅ Predictions generated!")
                    with st.expander("Output"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Prediction failed")
                    st.code(result.stderr)

            except Exception as e:
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
        with st.spinner("Evaluating model..."):
            try:
                import os
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

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=300,
                )

                if result.returncode == 0:
                    st.success("✅ Evaluation complete!")
                    with st.expander("Results"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Evaluation failed")
                    st.code(result.stderr)

            except Exception as e:
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
