"""Experiments page - AI-driven parameter optimization."""

import streamlit as st
import subprocess
import sys
from gefion.ui.components.chat import render_chat_widget
import json
import os
from gefion.observability import create_span, set_attributes


def get_page_context():
    """Return compact context dict for the Experiments page."""
    context = {"page_name": "Experiments", "summary": "AI experimentation framework for strategy optimization."}
    try:
        from gefion.ui.components.database import get_connection
        with create_span("ui.experiments.get_page_context"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT status, COUNT(*) FROM experiments GROUP BY status")
                    by_status = {r[0]: r[1] for r in cur.fetchall()}
        context["data_stats"] = {"experiments_by_status": by_status}
    except Exception:
        pass
    return context


def render_experiments():
    """Render the experiments page."""
    st.markdown("# :material/science: Experiments")
    render_chat_widget(get_page_context())
    st.markdown("Autonomous experimentation with human approval gates.")
    st.markdown(
        "Launch experiments to optimize strategies, tune hyperparameters, compare models, "
        "and engineer features. Each experiment proposes → gets approved → runs trials → reports results."
    )

    tab1, tab2, tab3, tab4 = st.tabs([
        ":material/explore: Discovery",
        ":material/list: Experiments",
        ":material/assessment: Results",
        ":material/loop: Cycles",
    ])

    with tab1:
        render_discovery_section()

    with tab2:
        render_list_section()

    with tab3:
        render_results_section()

    with tab4:
        render_cycles_section()


def render_list_section():
    """Render experiment list with filtering."""
    st.subheader("Experiment List")
    st.caption("Experiments flow: **proposed** → **approved** → **running** → **completed**. "
               "Approve or reject proposed experiments below.")

    col1, col2, col3 = st.columns(3)

    with col1:
        status_filter = st.selectbox(
            "Status",
            ["all", "proposed", "approved", "running", "completed", "failed", "rejected"],
            help="Filter by experiment status",
        )

    with col2:
        type_filter = st.selectbox(
            "Type",
            ["all", "strategy_params", "hyperparameter", "model_comparison",
             "feature_engineering", "feature_selection", "label_engineering", "pipeline"],
            help="Filter by experiment type",
        )

    with col3:
        limit = st.number_input(
            "Limit",
            min_value=5,
            max_value=100,
            value=20,
        )

    if st.button("Refresh", width="stretch"):
        st.rerun()

    # Build and show CLI command
    cmd_parts = ["gefion", "experiment", "list"]
    if status_filter != "all":
        cmd_parts.extend(["--status", status_filter])
    if type_filter != "all":
        cmd_parts.extend(["--type", type_filter])
    cmd_parts.extend(["--limit", str(limit)])
    cmd_parts.append("--json")

    # Load experiments from database
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT
                        id,
                        name,
                        experiment_type,
                        status,
                        objective_metric,
                        config->>'search_method' as search_method,
                        total_trials,
                        completed_trials,
                        best_score,
                        created_at
                    FROM experiments
                    WHERE 1=1
                """
                params = []

                if status_filter != "all":
                    query += " AND status = %s"
                    params.append(status_filter)

                if type_filter != "all":
                    query += " AND experiment_type = %s"
                    params.append(type_filter)

                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)
                experiments = cur.fetchall()

        if experiments:
            import pandas as pd
            df = pd.DataFrame(
                experiments,
                columns=[
                    "ID", "Name", "Type", "Status", "Objective",
                    "Search", "Trials", "Done", "Best Score", "Created"
                ]
            )
            st.dataframe(df, use_container_width=True)

            # Quick actions for proposed experiments
            proposed = [e for e in experiments if e[3] == "proposed"]
            if proposed:
                st.markdown("### Pending Approval")
                for exp in proposed:
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.write(f"**{exp[1]}** (ID: {exp[0]}) - {exp[2]}")
                    with col2:
                        if st.button("Approve", key=f"approve_{exp[0]}"):
                            approve_experiment(exp[0])
                    with col3:
                        if st.button("Reject", key=f"reject_{exp[0]}"):
                            reject_experiment(exp[0])

            # Quick actions for approved experiments (ready to run)
            approved = [e for e in experiments if e[3] == "approved"]
            if approved:
                st.markdown("### Ready to Run")
                for exp in approved:
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.write(f"**{exp[1]}** (ID: {exp[0]}) - {exp[2]} ({exp[6]} trials)")
                    with col2:
                        if st.button("Run", key=f"run_{exp[0]}", type="primary"):
                            run_experiment(exp[0])
        else:
            st.info("No experiments found matching the filter.")

    except Exception as e:
        st.error(f"Error loading experiments: {e}")
        st.info("The experiments table may not exist. Run `gefion db-migrate` to create it.")


def render_propose_section():
    """Render experiment proposal form supporting all experiment types."""
    st.subheader("Propose New Experiment")
    st.info("""
    **Experiment Types:**
    - **Strategy Params** — optimize trading strategy parameters via backtesting
    - **Hyperparameter** — tune ML model settings (learning rate, depth, etc.) with cross-validation
    - **Model Comparison** — compare algorithms (LightGBM vs XGBoost vs Linear) on identical data splits
    - **Feature Engineering** — test new computed features (rolling z-score, momentum, etc.)
    - **Feature Selection** — find the best subset of features for model performance
    - **Label Engineering** — test different prediction targets (raw returns, log returns, winsorized, etc.)
    """)

    experiment_type = st.selectbox(
        "Experiment Type",
        ["strategy_params", "hyperparameter", "model_comparison",
         "feature_engineering", "feature_selection", "label_engineering"],
        help="Type of experiment to run",
    )

    col1, col2 = st.columns(2)

    with col1:
        name = st.text_input(
            "Experiment Name",
            placeholder="tune-lgbm-h7",
            help="Descriptive name for the experiment",
        )

        search_method = st.selectbox(
            "Search Method",
            ["bayesian", "random", "grid"],
            help="bayesian is most efficient, grid is exhaustive",
        )

        max_trials = st.number_input(
            "Max Trials",
            min_value=1,
            max_value=500,
            value=10,
        )

    with col2:
        if experiment_type == "strategy_params":
            objective = st.selectbox("Objective", ["sharpe_ratio", "total_return_pct", "max_drawdown_pct"],
                                     help="Metric to optimize. Sharpe = risk-adjusted return, Total Return = raw gain, Max Drawdown = worst peak-to-trough loss.")
            direction = st.selectbox("Direction", ["maximize", "minimize"])
        else:
            objective = st.selectbox("Objective", ["quantile_loss", "q50_calibration", "avg_iqr"],
                                     help="quantile_loss = prediction accuracy (lower is better). q50_calibration = how well median predictions match reality. avg_iqr = width of prediction intervals.")
            direction = st.selectbox("Direction", ["minimize", "maximize"])

        horizon_days = None
        dataset_uri = None
        if experiment_type != "strategy_params":
            horizon_days = st.selectbox("Horizon (days)", [7, 30], index=0)
            # Auto-detect datasets
            from pathlib import Path
            dataset_dirs = sorted(Path("datasets").glob("*/manifest.json")) if Path("datasets").exists() else []
            dataset_options = [str(d) for d in dataset_dirs]
            if dataset_options:
                dataset_uri = st.selectbox("Dataset", dataset_options)
            else:
                dataset_uri = st.text_input("Dataset URI", placeholder="datasets/baseline_v2/manifest.json")

    # Type-specific configuration
    search_space = {}
    extra_config = {}

    if experiment_type == "strategy_params":
        _render_strategy_params_config(search_space, extra_config)
    elif experiment_type == "hyperparameter":
        _render_hyperparameter_config(search_space, extra_config)
    elif experiment_type == "model_comparison":
        _render_model_comparison_config(search_space, extra_config)
    elif experiment_type == "feature_engineering":
        _render_feature_engineering_config(search_space, extra_config)
    elif experiment_type == "feature_selection":
        _render_feature_selection_config(search_space, extra_config)
    elif experiment_type == "label_engineering":
        _render_label_engineering_config(search_space, extra_config)

    with st.expander("View Search Space JSON"):
        st.json(search_space)

    if st.button("Propose Experiment", type="primary", width="stretch"):
        if not name:
            st.error("Please enter an experiment name")
            return

        search_space_json = json.dumps(search_space)

        cmd = [
            sys.executable, "-m", "gefion.cli", "experiment", "propose",
            "--name", name,
            "--type", experiment_type,
            "--search-space", search_space_json,
            "--objective", objective,
            "--objective-direction", direction,
            "--search-method", search_method,
            "--max-trials", str(max_trials),
            "--json",
        ]

        if dataset_uri:
            cmd.extend(["--dataset-uri", str(dataset_uri)])
        if horizon_days:
            cmd.extend(["--horizon-days", str(horizon_days)])

        # Add extra config
        if extra_config:
            cmd.extend(["--config", json.dumps(extra_config)])

        # Strategy-specific options
        if extra_config.get("strategy"):
            cmd.extend(["--strategy", extra_config["strategy"]])
        if extra_config.get("symbols"):
            cmd.extend(["--symbols", extra_config["symbols"]])
        if extra_config.get("start_date"):
            cmd.extend(["--start-date", extra_config["start_date"]])
        if extra_config.get("end_date"):
            cmd.extend(["--end-date", extra_config["end_date"]])
        if extra_config.get("model_type"):
            cmd.extend(["--model-type", extra_config["model_type"]])

        env = os.environ.copy()

        with st.status("Proposing experiment...", expanded=True) as status:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)

                if result.returncode == 0:
                    status.update(label="Experiment proposed!", state="complete")
                    try:
                        data = json.loads(result.stdout)
                        exp_id = data.get("experiment_id", data.get("id"))
                        st.success(f"Experiment #{exp_id} created. Go to List tab to approve it.")
                    except json.JSONDecodeError:
                        st.success("Experiment created successfully!")
                else:
                    status.update(label="Failed", state="error")
                    st.error("Failed to create experiment")
                    st.code(result.stderr)
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error: {e}")


def _render_strategy_params_config(search_space, extra_config):
    """Render strategy parameter search space config."""
    from datetime import date, timedelta

    strategy = st.selectbox("Strategy", ["momentum", "mean_reversion", "ma_crossover", "breakout"])
    extra_config["strategy"] = strategy

    from gefion.ui.components.database import get_symbols
    symbols = get_symbols()
    selected = st.multiselect("Symbols", symbols, default=symbols[:10] if len(symbols) >= 10 else symbols)
    extra_config["symbols"] = ",".join(selected)

    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input("Start Date", value=date.today() - timedelta(days=365), key="sp_start")
        extra_config["start_date"] = str(start)
    with col2:
        end = st.date_input("End Date", value=date.today() - timedelta(days=1), key="sp_end")
        extra_config["end_date"] = str(end)

    st.markdown("##### Search Space")
    if strategy == "momentum":
        col1, col2 = st.columns(2)
        with col1:
            search_space["lookback_days"] = {"type": "int", "low": st.number_input("Lookback Min", value=5, min_value=1, key="m_lb_lo"), "high": st.number_input("Lookback Max", value=30, min_value=2, key="m_lb_hi")}
        with col2:
            search_space["top_n"] = {"type": "int", "low": st.number_input("Top N Min", value=3, min_value=1, key="m_tn_lo"), "high": st.number_input("Top N Max", value=15, min_value=2, key="m_tn_hi")}
    elif strategy == "mean_reversion":
        col1, col2 = st.columns(2)
        with col1:
            search_space["rsi_oversold"] = {"type": "int", "low": st.number_input("RSI Oversold Min", value=20, key="mr_os_lo"), "high": st.number_input("RSI Oversold Max", value=40, key="mr_os_hi")}
        with col2:
            search_space["rsi_overbought"] = {"type": "int", "low": st.number_input("RSI Overbought Min", value=60, key="mr_ob_lo"), "high": st.number_input("RSI Overbought Max", value=80, key="mr_ob_hi")}


def _render_hyperparameter_config(search_space, extra_config):
    """Render hyperparameter tuning config."""
    model_type = st.selectbox("Model Type", ["lightgbm", "xgboost", "quantile_regression"], key="hp_model")
    extra_config["model_type"] = model_type

    st.caption(f"Tuning {model_type} hyperparameters. The search will try different combinations "
               "within these ranges to minimize prediction error.")
    st.markdown("##### Search Space")
    col1, col2, col3 = st.columns(3)
    with col1:
        search_space["learning_rate"] = {"type": "float", "low": 0.005, "high": 0.3, "log": True}
        st.caption("Learning rate: 0.005 - 0.3 (log)")
    with col2:
        search_space["n_estimators"] = {"type": "int", "low": 50, "high": 500}
        st.caption("Estimators: 50 - 500")
    with col3:
        search_space["max_depth"] = {"type": "int", "low": 2, "high": 12}
        st.caption("Max depth: 2 - 12")


def _render_model_comparison_config(search_space, extra_config):
    """Render model comparison config."""
    st.caption("Each model is trained on identical data splits for fair comparison. "
               "The model with the best objective score wins.")
    models = st.multiselect(
        "Models to Compare",
        ["lightgbm", "xgboost", "quantile_regression"],
        default=["lightgbm", "xgboost", "quantile_regression"],
    )
    search_space["model_type"] = models
    extra_config["model_types"] = models


def _render_feature_engineering_config(search_space, extra_config):
    """Render feature engineering config."""
    function_name = st.selectbox(
        "Feature Function",
        ["rolling_zscore", "rolling_return", "rolling_std", "momentum", "ema", "log_return"],
        help="rolling_zscore: how far price deviates from its moving average (in std devs). "
             "rolling_return: % change over window. rolling_std: volatility. "
             "momentum: price ratio vs N days ago. ema: exponential moving average. "
             "log_return: natural log of price change.",
    )
    source_column = st.selectbox("Source Column", ["close", "volume", "high", "low", "open"])
    extra_config["feature_config"] = {"function_name": function_name}
    extra_config["source_column"] = source_column

    st.markdown("##### Parameter Search")
    if function_name in ("rolling_zscore", "rolling_return", "rolling_std", "momentum", "ema"):
        low = st.number_input("Window Min", value=5, min_value=2, key="fe_w_lo")
        high = st.number_input("Window Max", value=30, min_value=3, key="fe_w_hi")
        step = st.number_input("Window Step", value=5, min_value=1, key="fe_w_step")
        search_space["window"] = {"type": "int", "low": low, "high": high, "step": step}


def _render_feature_selection_config(search_space, extra_config):
    """Render feature selection config."""
    st.markdown("Define feature subsets to compare as JSON arrays.")
    subsets_json = st.text_area(
        "Feature Subsets (JSON)",
        value='[["indicator_rsi_14", "indicator_ema_12", "indicator_psar"], '
              '["indicator_bb_middle", "indicator_bb_upper", "indicator_bb_lower"]]',
        help="Each inner array is a feature subset to test. The experiment trains a model "
             "on each subset and compares performance to find the best combination.",
    )
    try:
        subsets = json.loads(subsets_json)
        search_space["features"] = subsets
        all_features = list({f for s in subsets for f in s})
        extra_config["feature_names"] = all_features
    except json.JSONDecodeError:
        st.error("Invalid JSON for feature subsets")


def _render_label_engineering_config(search_space, extra_config):
    """Render label engineering config."""
    label_types = st.multiselect(
        "Label Transforms to Compare",
        ["raw", "log_return", "winsorized", "threshold_return", "sign", "rank"],
        default=["raw", "log_return", "winsorized"],
        help="raw: unmodified forward returns. log_return: log(1+return), reduces outlier impact. "
             "winsorized: clips extreme returns to percentile bounds. "
             "threshold_return: caps returns at a fixed threshold. "
             "sign: square root of absolute return with sign preserved. "
             "rank: converts returns to percentile ranks (0-1 scale).",
    )
    search_space["label_type"] = label_types
    extra_config["label_type"] = "raw"


def render_run_section():
    """Render experiment run section."""
    st.subheader("Run Experiment")

    st.info("""
    Run an approved experiment. Each trial tests a different parameter combination
    using the configured search method (Bayesian, random, or grid). Progress is shown
    in real-time. Results are saved to the database when complete.
    """)

    # Get approved experiments
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, experiment_type, total_trials
                    FROM experiments
                    WHERE status = 'approved'
                    ORDER BY created_at DESC
                """)
                approved = cur.fetchall()

        if approved:
            exp_options = {f"{e[1]} (ID: {e[0]}, {e[3]} trials)": e[0] for e in approved}

            selected = st.selectbox(
                "Select Experiment",
                list(exp_options.keys()),
            )
            exp_id = exp_options[selected]

            if st.button("Run Experiment", type="primary", width="stretch"):
                run_experiment(exp_id)
        else:
            st.warning("No approved experiments to run. Approve an experiment from the List tab first.")

    except Exception as e:
        st.error(f"Error: {e}")

    # Also allow running by ID
    st.markdown("---")
    st.markdown("##### Run by ID")

    exp_id_input = st.number_input(
        "Experiment ID",
        min_value=1,
        value=1,
        key="run_exp_id",
    )

    if st.button("Run by ID", width="stretch"):
        run_experiment(exp_id_input)


def render_results_section():
    """Render experiment results section."""
    st.subheader("Experiment Results")

    # Get completed experiments
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, objective_metric, best_score, completed_trials, total_trials
                    FROM experiments
                    WHERE status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 20
                """)
                completed = cur.fetchall()

        if completed:
            exp_options = {
                f"{e[1]} (ID: {e[0]}, best {e[2]}: {f'{e[3]:.4f}' if e[3] is not None else 'N/A'})": e[0]
                for e in completed
            }

            selected = st.selectbox(
                "Select Experiment",
                list(exp_options.keys()),
            )
            exp_id = exp_options[selected]

            if st.button("Load Results", width="stretch"):
                load_experiment_results(exp_id)
        else:
            st.info("No completed experiments yet.")

    except Exception as e:
        st.error(f"Error: {e}")

    # Results by ID
    st.markdown("---")
    st.markdown("##### View by ID")

    exp_id_input = st.number_input(
        "Experiment ID",
        min_value=1,
        value=1,
        key="results_exp_id",
    )

    show_trials = st.checkbox("Show all trials", value=False)

    if st.button("View Results", width="stretch"):
        load_experiment_results(exp_id_input, show_trials=show_trials)


def approve_experiment(exp_id: int):
    """Approve an experiment."""
    env = os.environ.copy()
    # OTEL_ENABLED inherited from parent

    cmd = [sys.executable, "-m", "gefion.cli", "experiment", "approve", "--id", str(exp_id)]

    st.code(f"gefion experiment approve --id {exp_id}", language="bash")

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode == 0:
        st.success(f"Experiment #{exp_id} approved!")
        st.rerun()
    else:
        st.error(f"Failed: {result.stderr}")


def reject_experiment(exp_id: int):
    """Reject an experiment."""
    env = os.environ.copy()
    # OTEL_ENABLED inherited from parent

    cmd = [
        sys.executable, "-m", "gefion.cli", "experiment", "reject",
        "--id", str(exp_id), "--reason", "Rejected via UI"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode == 0:
        st.success(f"Experiment #{exp_id} rejected.")
        st.rerun()
    else:
        st.error(f"Failed: {result.stderr}")


def run_experiment(exp_id: int):
    """Run an experiment."""
    env = os.environ.copy()
    # OTEL_ENABLED inherited from parent

    cmd = [sys.executable, "-m", "gefion.cli", "experiment", "run", "--id", str(exp_id), "--json"]

    st.code(f"gefion experiment run --id {exp_id}", language="bash")

    with st.status(f"Running experiment #{exp_id}...", expanded=True) as status:
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

            for line in process.stdout:
                line = line.strip()
                if not line or len(line) < 3:
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        trial = data.get("trial", 0)
                        total = data.get("total", 0)
                        score = data.get("score", 0)
                        if trial and total:
                            status_text.write(f"Trial {trial}/{total} - Score: {score:.4f}")
                except json.JSONDecodeError:
                    if not line.startswith(('{', '}', '[', ']')):
                        status_text.write(line)

            returncode = process.wait()

            if returncode == 0:
                status.update(label="Experiment completed!", state="complete")
                st.success("Experiment finished. Check Results tab for details.")
            else:
                stderr = process.stderr.read()
                status.update(label="Failed", state="error")
                st.error(f"Failed: {stderr}")

        except Exception as e:
            status.update(label="Error", state="error")
            st.error(f"Error: {e}")


def load_experiment_results(exp_id: int, show_trials: bool = False):
    """Load and display experiment results."""
    env = os.environ.copy()
    # OTEL_ENABLED inherited from parent

    cmd = [sys.executable, "-m", "gefion.cli", "experiment", "results", "--id", str(exp_id), "--json"]
    if show_trials:
        cmd.append("--trials")

    cli_cmd = f"gefion experiment results --id {exp_id}"
    if show_trials:
        cli_cmd += " --trials"
    st.code(cli_cmd, language="bash")

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)

            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("Status", data.get("status", "N/A"))
            with col2:
                st.metric("Best Score", f"{data.get('best_score', 0):.4f}")
            with col3:
                completed = data.get("completed_trials", 0)
                total = data.get("total_trials", 0)
                st.metric("Trials", f"{completed}/{total}")
            with col4:
                goal = "Yes" if data.get("goal_achieved") else "No"
                st.metric("Goal Achieved", goal)

            # Best parameters
            if "best_params" in data and data["best_params"]:
                st.markdown("### Best Parameters")
                st.json(data["best_params"])

            # Trials table
            if show_trials and "trials" in data:
                st.markdown("### All Trials")
                import pandas as pd
                trials_df = pd.DataFrame(data["trials"])
                st.dataframe(trials_df, use_container_width=True)

        except json.JSONDecodeError:
            st.code(result.stdout)
    else:
        st.error(f"Failed: {result.stderr}")


def _get_theme_map():
    """Map source books to research themes."""
    return {
        "The Econometrics of Financial Markets": "Statistical Methods",
        "Time Series Analysis": "Statistical Methods",
        "Advances in Financial Machine Learning": "ML for Finance",
        "Machine Learning for Algorithmic Trading": "ML for Finance",
        "Active Portfolio Management": "Portfolio Construction",
        "Asset Management: A Systematic Approach to Factor Investing": "Factor Investing",
        "Empirical Asset Pricing: The Cross Section of Stock Returns": "Factor Investing",
        "Trading and Exchanges": "Market Microstructure",
        "Algorithmic Trading": "Systematic Trading",
        "Quantitative Trading": "Systematic Trading",
        "Risk and Asset Allocation": "Risk Management",
        "Antifragile": "Tail Risk & Robustness",
        "The Black Swan": "Tail Risk & Robustness",
        "Fooled by Randomness": "Tail Risk & Robustness",
    }


def _get_data_action(requirement: str) -> str:
    """Map a data requirement to an actionable command."""
    actions = {
        "market_cap": "gefion fundamentals-update",
        "book_value": "gefion fundamentals-update",
        "fundamentals": "gefion fundamentals-update",
        "beta": "gefion cross-sectional-compute --feature beta",
        "sector": "gefion fundamentals-update",
        "ml_predictions": "gefion ml predict (train a model first)",
        "strategy_configs": "Create configs in Backtesting → Configs tab",
        "vix": "VIX data not yet supported — external data source needed",
    }
    req_lower = requirement.lower()
    for key, action in actions.items():
        if key in req_lower:
            return action
    if req_lower.startswith("ohlcv") or req_lower in ("close", "volume", "open", "high", "low"):
        return "Already available (OHLCV data)"
    if req_lower.startswith("features"):
        return "gefion data-update (computes features automatically)"
    return "Check data requirements"


def _group_principles_by_theme(principles):
    """Group principles by research theme, with ready/blocked counts."""
    theme_map = _get_theme_map()
    themes = {}
    for p in principles:
        source = p.get("source", {})
        book = source.get("title", "Other")
        theme = theme_map.get(book, "Other")
        if theme not in themes:
            themes[theme] = {"principles": [], "sources": set()}
        themes[theme]["principles"].append(p)
        themes[theme]["sources"].add(book)
    return themes


def render_discovery_section():
    """Render theme-based discovery — browse research themes, explore principles."""
    st.subheader("Discovery")
    st.markdown(
        "Browse research themes from quantitative finance literature. "
        "Each theme contains testable principles that can become experiments."
    )

    # Data summary
    try:
        from gefion.ui.components.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM stocks) as stocks,
                        (SELECT COUNT(*) FROM stock_ohlcv) as prices,
                        (SELECT COUNT(*) FROM computed_features) as features,
                        (SELECT COUNT(*) FROM feature_definitions WHERE active = true) as feat_defs,
                        (SELECT COUNT(*) FROM ml_models) as models,
                        (SELECT COUNT(*) FROM experiments) as experiments
                """)
                row = cur.fetchone()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Stocks", f"{row[0]:,}")
            st.metric("Feature Definitions", row[3])
        with col2:
            st.metric("Price Records", f"{row[1]:,}")
            st.metric("Trained Models", row[4])
        with col3:
            st.metric("Feature Records", f"{row[2]:,}")
            st.metric("Experiments Run", row[5])
    except Exception:
        st.caption("Could not load data summary.")

    # Load principles (used by both cycle launcher and theme explorer)
    try:
        from gefion.experiments.principles import load_principles
        all_principles = load_principles()
    except Exception:
        all_principles = []

    if not all_principles:
        st.info("No principles catalog found. Ensure `data/principles/` directory exists.")
        return

    themes = _group_principles_by_theme(all_principles)

    # Check which data requirements are satisfiable
    available_prefixes = {"ohlcv", "close", "volume", "open", "high", "low", "features"}

    def _is_ready(p):
        for req in p.get("data_requirements", []):
            req_base = req.split(".")[0].lower()
            if req_base not in available_prefixes:
                return False
        return True

    st.markdown("---")

    # =====================================================================
    # AUTONOMOUS CYCLE LAUNCHER (primary action — shown first)
    # =====================================================================
    _render_cycle_launcher(themes, _is_ready, all_principles, available_prefixes)

    st.markdown("---")

    # =====================================================================
    # RESEARCH THEMES (browse & explore — shown below)
    # =====================================================================
    st.markdown("### Explore Research Themes")
    st.caption("Browse themes to understand what principles are available and what data is needed.")

    # Theme cards
    for theme_name in sorted(themes.keys(), key=lambda t: -len(themes[t]["principles"])):
        theme = themes[theme_name]
        principles = theme["principles"]
        ready_count = sum(1 for p in principles if _is_ready(p))
        blocked_count = len(principles) - ready_count
        sources = ", ".join(sorted(theme["sources"]))

        status_label = f"{ready_count} ready" if ready_count > 0 else "all blocked"
        if blocked_count > 0 and ready_count > 0:
            status_label += f", {blocked_count} need data"

        with st.expander(f"**{theme_name}** — {len(principles)} principles ({status_label})"):
            st.caption(f"Sources: {sources}")

            # Ready principles
            ready_principles = [p for p in principles if _is_ready(p)]
            if ready_principles:
                st.markdown(f"**Ready to test ({len(ready_principles)}):**")
                for p in ready_principles:
                    claim = p.get("claim", "")
                    short_claim = claim[:150] + "..." if len(claim) > 150 else claim
                    exp_types = ", ".join(p.get("experiment_types", []))
                    name = p["id"].replace("-", " ").title()
                    st.markdown(f"- **{name}** — {short_claim}")
                    st.caption(f"  Experiment types: {exp_types}")

            # Blocked principles
            blocked_principles = [p for p in principles if not _is_ready(p)]
            if blocked_principles:
                st.markdown(f"**Need more data ({len(blocked_principles)}):**")
                for p in blocked_principles:
                    name = p["id"].replace("-", " ").title()
                    missing = [
                        req for req in p.get("data_requirements", [])
                        if req.split(".")[0].lower() not in available_prefixes
                    ]
                    actions = set(_get_data_action(r) for r in missing)
                    actions.discard("Already available (OHLCV data)")
                    st.markdown(f"- **{name}**")
                    if missing:
                        st.caption(f"  Needs: {', '.join(missing)}")
                    if actions:
                        for action in actions:
                            st.caption(f"  Fix: `{action}`")

    # Data gaps summary
    st.markdown("---")
    st.markdown("### Data Gaps")
    st.caption("What data would unlock the most new experiments?")

    all_missing = {}
    for p in all_principles:
        if not _is_ready(p):
            for req in p.get("data_requirements", []):
                req_base = req.split(".")[0].lower()
                if req_base not in available_prefixes:
                    if req not in all_missing:
                        all_missing[req] = {"count": 0, "action": _get_data_action(req)}
                    all_missing[req]["count"] += 1

    if all_missing:
        sorted_gaps = sorted(all_missing.items(), key=lambda x: -x[1]["count"])
        for req, info in sorted_gaps[:8]:
            st.markdown(f"- **{req}** — would unlock {info['count']} principle(s). Fix: `{info['action']}`")
    else:
        st.success("All principles have the data they need!")

    # Manual experiment propose
    st.markdown("---")
    st.markdown("### Manual Experiment")
    st.caption("Propose a single experiment with specific parameters. For broad exploration, use the Autonomous Cycle above.")

    with st.expander("Propose a manual experiment"):
        render_propose_section()


def _render_cycle_launcher(themes, _is_ready, all_principles, available_prefixes):
    """Render the autonomous cycle launcher with theme selection and guardrails."""
    st.markdown("### Autonomous Experiment Cycle")
    st.markdown(
        "An autonomous cycle does everything in one shot: scans for opportunities, "
        "proposes experiments, runs them, and uses statistical testing to filter out "
        "false discoveries. Only genuine improvements survive."
    )

    # Basic settings
    col1, col2 = st.columns(2)
    with col1:
        cycle_name = st.text_input("Cycle Name", placeholder="exploration-cycle-1", key="disc_cycle_name")
        max_experiments = st.number_input(
            "Max Experiments", value=5, min_value=1, max_value=50, key="disc_max_exp",
            help="How many experiments to run in this cycle. More = broader search but takes longer.",
        )
        fdr_rate = st.slider(
            "False Discovery Filter", min_value=0.01, max_value=0.20, value=0.05, step=0.01,
            help="At 5%, at most 5% of reported discoveries may be due to chance. Lower = stricter.",
        )
    with col2:
        holdout_weeks = st.number_input(
            "Holdout Weeks", value=4, min_value=1, max_value=12, key="disc_holdout",
            help="Weeks of recent data reserved for final validation.",
        )
        max_trials = st.number_input(
            "Trials per Experiment", value=10, min_value=1, max_value=100, key="disc_max_trials",
            help="How many parameter combinations to try per experiment.",
        )
        search_method = st.selectbox(
            "Search Method", ["bayesian", "random", "grid"], key="disc_search_method",
            help="Bayesian is most efficient (learns from results). Grid is exhaustive.",
        )

    # Theme selection
    st.markdown("##### Select Research Themes")
    st.caption("Choose which themes the agent can explore. Only ready principles (with available data) will be used.")

    theme_options = list(sorted(themes.keys(), key=lambda t: -len(themes[t]["principles"])))
    # Show ready counts per theme
    theme_labels = {}
    for t in theme_options:
        ready = sum(1 for p in themes[t]["principles"] if _is_ready(p))
        total = len(themes[t]["principles"])
        theme_labels[t] = f"{t} ({ready}/{total} ready)"

    selected_themes = st.multiselect(
        "Themes",
        theme_options,
        default=[t for t in theme_options if sum(1 for p in themes[t]["principles"] if _is_ready(p)) > 0][:3],
        format_func=lambda t: theme_labels.get(t, t),
        help="Select research themes to explore. The agent will only use principles from these themes.",
    )

    # Derive experiment types from selected themes' principles
    selected_exp_types = set()
    for t in selected_themes:
        for p in themes[t]["principles"]:
            if _is_ready(p):
                for et in p.get("experiment_types", []):
                    selected_exp_types.add(et)
    # Map any non-standard types
    type_map = {"strategy_optimization": "strategy_params"}
    allowed_types = [type_map.get(t, t) for t in selected_exp_types]

    if selected_themes:
        ready_in_selection = sum(
            1 for t in selected_themes
            for p in themes[t]["principles"] if _is_ready(p)
        )
        st.caption(f"{ready_in_selection} ready principles across {len(selected_themes)} themes. "
                   f"Experiment types: {', '.join(sorted(selected_exp_types)) or 'none'}.")

    # ML settings — each defaults to "Agent decides" unless user overrides
    AGENT_DECIDES = "Agent decides"

    with st.expander("ML Settings — override or let the agent decide"):
        st.caption("Each setting defaults to agent-controlled. Override any to constrain the search.")

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            # Algorithms
            allowed_algorithms = st.multiselect(
                "Allowed Algorithms",
                ["lightgbm", "xgboost", "quantile_regression"],
                default=["lightgbm", "xgboost", "quantile_regression"],
                help="Which ML algorithms the agent can use. Multiple = agent can compare them.",
                key="disc_algorithms",
            )

            # Horizon
            horizon_choice = st.selectbox(
                "Prediction Horizon",
                [AGENT_DECIDES, "7 days", "30 days"],
                help="Agent can try both horizons, or lock to one.",
                key="disc_horizon",
            )
            if horizon_choice == AGENT_DECIDES:
                horizon_days = None  # Agent picks
                allowed_horizons = [7, 30]
            else:
                horizon_days = int(horizon_choice.split()[0])
                allowed_horizons = [horizon_days]

            # Quantiles
            quantile_choice = st.selectbox(
                "Prediction Quantiles",
                [AGENT_DECIDES, "Standard (10/50/90)", "Wide (5/50/95)", "Tight (25/50/75)"],
                help="Which quantile levels to predict. Agent can experiment with different widths.",
                key="disc_quantiles",
            )
            quantile_map = {
                AGENT_DECIDES: None,
                "Standard (10/50/90)": [0.1, 0.5, 0.9],
                "Wide (5/50/95)": [0.05, 0.5, 0.95],
                "Tight (25/50/75)": [0.25, 0.5, 0.75],
            }
            quantiles = quantile_map.get(quantile_choice)

        with col_g2:
            # Dataset
            from pathlib import Path as _Path
            dataset_dirs = sorted(_Path("datasets").glob("*/manifest.json")) if _Path("datasets").exists() else []
            dataset_options = [AGENT_DECIDES] + [str(d) for d in dataset_dirs]
            dataset_choice = st.selectbox("Dataset", dataset_options, key="disc_dataset",
                                          help="Agent can auto-detect the latest dataset, or lock to a specific one.")
            dataset_uri = None if dataset_choice == AGENT_DECIDES else dataset_choice

            # CV folds
            cv_choice = st.selectbox(
                "Cross-Validation Folds",
                [AGENT_DECIDES, "3 folds", "5 folds", "10 folds"],
                help="More folds = more reliable but slower. Agent can optimize this.",
                key="disc_cv_folds",
            )
            cv_folds = None if cv_choice == AGENT_DECIDES else int(cv_choice.split()[0])

            # Embargo
            embargo_choice = st.selectbox(
                "CV Embargo Period",
                [AGENT_DECIDES, "1%", "2%", "5%"],
                help="Gap between train/test folds to prevent data leakage. Higher = stricter.",
                key="disc_embargo",
            )
            embargo_pct = None if embargo_choice == AGENT_DECIDES else float(embargo_choice.strip("%")) / 100

            # Max parallel
            max_parallel = st.number_input(
                "Max Parallel Experiments", value=2, min_value=1, max_value=5, key="disc_parallel",
                help="How many experiments to run simultaneously.",
            )

    # Build config from current UI state
    cycle_config = {
        "selected_themes": selected_themes,
        "allowed_types": allowed_types,
        "auto_approve": True,
        "dataset_uri": str(dataset_uri) if dataset_uri else None,
        "horizon_days": horizon_days,
        "allowed_horizons": allowed_horizons,
        "allowed_algorithms": allowed_algorithms,
        "algorithm": allowed_algorithms[0] if allowed_algorithms else "lightgbm",
        "quantiles": quantiles,
        "cv_folds": cv_folds,
        "embargo_pct": embargo_pct,
        "max_trials_per_experiment": max_trials,
        "search_method": search_method,
        "max_parallel": max_parallel,
    }

    # Config import/export
    with st.expander("Config JSON — view, export, or import"):
        config_tab1, config_tab2 = st.tabs(["View / Export", "Import"])

        with config_tab1:
            full_config = {
                "cycle_name": cycle_name,
                "max_experiments": max_experiments,
                "fdr_rate": fdr_rate,
                "holdout_weeks": holdout_weeks,
                **cycle_config,
            }
            st.json(full_config)
            st.download_button(
                "Download Config",
                data=json.dumps(full_config, indent=2),
                file_name=f"cycle_config_{cycle_name or 'draft'}.json",
                mime="application/json",
            )

        with config_tab2:
            uploaded = st.file_uploader("Load config JSON", type=["json"], key="disc_config_upload")
            if uploaded is not None:
                try:
                    loaded = json.loads(uploaded.read())
                    st.json(loaded)
                    st.info(
                        "Config loaded. To apply it, copy the values into the form above. "
                        "Full auto-apply from uploaded configs is coming soon."
                    )
                except json.JSONDecodeError:
                    st.error("Invalid JSON file")

    if st.button("Start & Run Cycle", type="primary", width="stretch", key="disc_start_cycle"):
        if not cycle_name:
            st.error("Please enter a cycle name")
            return
        if not selected_themes:
            st.error("Select at least one research theme")
            return

        # Step 1: Create cycle
        create_cmd = [
            sys.executable, "-m", "gefion.cli", "experiment", "cycle-start",
            "--name", cycle_name,
            "--fdr-rate", str(fdr_rate),
            "--holdout-weeks", str(holdout_weeks),
            "--max-experiments", str(max_experiments),
            "--json",
        ]
        env = os.environ.copy()

        with st.status("Running autonomous cycle...", expanded=True) as status:
            try:
                # Create cycle
                result = subprocess.run(create_cmd, capture_output=True, text=True, env=env, timeout=30)
                if result.returncode != 0:
                    status.update(label="Failed to create cycle", state="error")
                    st.error(result.stderr)
                    return

                data = json.loads(result.stdout)
                new_cycle_id = data.get("cycle_id")
                if not new_cycle_id:
                    status.update(label="Failed", state="error")
                    st.error("No cycle ID returned")
                    return

                st.write(f"Cycle #{new_cycle_id} created. Saving guardrails...")

                # Store guardrails in discovery_snapshot
                from gefion.ui.components.database import get_connection
                from psycopg.types.json import Json
                with get_connection() as conn:
                    conn.autocommit = True
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE experiment_cycles SET discovery_snapshot = %s WHERE id = %s",
                            (Json({"cycle_config": cycle_config}), new_cycle_id),
                        )

                # Step 2: Run the cycle with streaming progress
                run_cmd = [
                    sys.executable, "-m", "gefion.cli", "experiment", "cycle-run",
                    str(new_cycle_id), "--json",
                ]

                phase_icons = {
                    "loading": ":material/settings:",
                    "discovery": ":material/search:",
                    "proposing": ":material/edit_note:",
                    "proposed": ":material/arrow_forward:",
                    "approving": ":material/check_circle:",
                    "running": ":material/play_arrow:",
                    "experiment_done": ":material/trending_up:",
                    "experiment_failed": ":material/error:",
                    "evaluating": ":material/analytics:",
                    "complete": ":material/celebration:",
                }

                progress_area = st.empty()
                progress_lines = []

                process = subprocess.Popen(
                    run_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, env=env,
                )

                final_result = None
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict):
                            phase = data.get("phase", "")
                            msg = data.get("message", data.get("msg", ""))
                            if phase and msg:
                                icon = phase_icons.get(phase, ":material/arrow_forward:")
                                indent = "    " if phase.startswith("experiment") or phase == "proposed" else ""
                                progress_lines.append(f"{indent}{icon} {msg}")
                                progress_area.markdown("\n\n".join(progress_lines[-10:]))
                            if "fdr_survivors" in data:
                                final_result = data
                    except json.JSONDecodeError:
                        pass

                returncode = process.wait()

                if returncode == 0:
                    status.update(label="Cycle complete!", state="complete")
                    if final_result:
                        st.success(
                            f"Cycle #{new_cycle_id} complete. "
                            f"Proposed: {final_result.get('proposed', 0)}, "
                            f"Completed: {final_result.get('completed', 0)}, "
                            f"FDR Survivors: {final_result.get('fdr_survivors', 0)}"
                        )
                    else:
                        st.success("Cycle complete! Check the Cycles tab for results.")
                else:
                    stderr = process.stderr.read()
                    status.update(label="Cycle failed", state="error")
                    st.error(f"Cycle run failed: {stderr}")

            except subprocess.TimeoutExpired:
                status.update(label="Timeout", state="error")
                st.error("Cycle timed out. Check the Cycles tab for partial results.")
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error: {e}")


def render_cycles_section():
    """Render experiment cycles list and status."""
    st.subheader("Experiment Cycles")
    st.markdown(
        "An experiment cycle groups multiple experiments and applies "
        "[False Discovery Rate](https://en.wikipedia.org/wiki/False_discovery_rate) "
        "correction to filter out discoveries that don't replicate. "
        "Only experiments that survive FDR testing are considered genuine."
    )

    # List cycles
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, status, fdr_rate, max_experiments,
                           created_at, completed_at
                    FROM experiment_cycles
                    ORDER BY created_at DESC
                    LIMIT 20
                """)
                cycles = cur.fetchall()

        if cycles:
            import pandas as pd
            df = pd.DataFrame(
                cycles,
                columns=["ID", "Name", "Status", "FDR Rate", "Max Experiments",
                         "Created", "Completed"]
            )
            st.dataframe(df, use_container_width=True)

            # Cycle detail
            cycle_options = {f"{c[1]} (ID: {c[0]})": c[0] for c in cycles}
            selected = st.selectbox("View Cycle Details", list(cycle_options.keys()))
            cycle_id = cycle_options[selected]

            if st.button("Load Cycle Status", width="stretch"):
                env = os.environ.copy()
                cmd = [
                    sys.executable, "-m", "gefion.cli", "experiment",
                    "cycle-status", str(cycle_id), "--json"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
                if result.returncode == 0:
                    try:
                        data = json.loads(result.stdout)
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Status", data.get("status", "N/A"))
                        with col2:
                            st.metric("Experiments", data.get("experiment_count", 0))
                        with col3:
                            st.metric("FDR Survivors", data.get("fdr_survivors", "N/A"))

                        if data.get("experiments"):
                            st.markdown("### Cycle Experiments")
                            exp_df = pd.DataFrame(data["experiments"])
                            st.dataframe(exp_df, use_container_width=True)
                    except json.JSONDecodeError:
                        st.code(result.stdout)
                else:
                    st.error(result.stderr)
        else:
            st.info("No experiment cycles yet. Start one from the Discovery tab.")

    except Exception as e:
        st.error(f"Error loading cycles: {e}")
        st.info("The experiment_cycles table may not exist. Run `gefion db-migrate` to create it.")
