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

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        ":material/explore: Discovery",
        ":material/list: List",
        ":material/add_circle: Propose",
        ":material/play_arrow: Run",
        ":material/assessment: Results",
        ":material/loop: Cycles",
    ])

    with tab1:
        render_discovery_section()

    with tab2:
        render_list_section()

    with tab3:
        render_propose_section()

    with tab4:
        render_run_section()

    with tab5:
        render_results_section()

    with tab6:
        render_cycles_section()


def render_list_section():
    """Render experiment list with filtering."""
    st.subheader("Experiment List")

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
        else:
            st.info("No experiments found matching the filter.")

    except Exception as e:
        st.error(f"Error loading experiments: {e}")
        st.info("The experiments table may not exist. Run `gefion db-migrate` to create it.")


def render_propose_section():
    """Render experiment proposal form supporting all experiment types."""
    st.subheader("Propose New Experiment")

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
            objective = st.selectbox("Objective", ["sharpe_ratio", "total_return_pct", "max_drawdown_pct"])
            direction = st.selectbox("Direction", ["maximize", "minimize"])
        else:
            objective = st.selectbox("Objective", ["quantile_loss", "q50_calibration", "avg_iqr"])
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
        help="JSON array of arrays — each inner array is a feature subset to test",
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
    )
    search_space["label_type"] = label_types
    extra_config["label_type"] = "raw"


def render_run_section():
    """Render experiment run section."""
    st.subheader("Run Experiment")

    st.info("""
    Run an approved experiment. The system will execute all trials
    (or until the goal is achieved with early stopping).
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
                f"{e[1]} (ID: {e[0]}, best {e[2]}: {e[3]:.4f if e[3] else 'N/A'})": e[0]
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
        cmd.append("--show-trials")

    cli_cmd = f"gefion experiment results --id {exp_id}"
    if show_trials:
        cli_cmd += " --show-trials"
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


def render_discovery_section():
    """Render data discovery and hypothesis generation."""
    st.subheader("Data Discovery")
    st.markdown("Inventory available data, identify gaps, and generate experiment hypotheses.")

    if st.button("Run Discovery", type="primary", width="stretch"):
        env = os.environ.copy()
        cmd = [sys.executable, "-m", "gefion.cli", "experiment", "discover", "--json"]

        with st.status("Discovering data sources...", expanded=True) as status:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
                if result.returncode == 0:
                    status.update(label="Discovery complete", state="complete")
                    try:
                        data = json.loads(result.stdout)

                        # Data sources
                        sources = data.get("data_sources", data.get("sources", []))
                        if sources:
                            st.markdown("### Data Sources")
                            import pandas as pd
                            if isinstance(sources, list):
                                df = pd.DataFrame(sources)
                                st.dataframe(df, use_container_width=True)
                            elif isinstance(sources, dict):
                                for name, info in sources.items():
                                    with st.expander(f"{name}"):
                                        st.json(info)

                        # Hypotheses
                        hypotheses = data.get("hypotheses", [])
                        if hypotheses:
                            st.markdown("### Generated Hypotheses")
                            for i, h in enumerate(hypotheses):
                                with st.expander(
                                    f"{h.get('name', f'Hypothesis {i+1}')} "
                                    f"({'ready' if h.get('status') == 'ready' else h.get('status', '?')})"
                                ):
                                    st.markdown(f"**Type:** {h.get('experiment_type', 'N/A')}")
                                    st.markdown(f"**Principle:** {h.get('principle_id', 'N/A')}")
                                    if h.get('null_hypothesis'):
                                        st.markdown(f"**H0:** {h['null_hypothesis']}")
                                    st.markdown(f"**Status:** {h.get('status', 'N/A')}")
                                    if h.get('blocked_reason'):
                                        st.warning(f"Blocked: {h['blocked_reason']}")

                        if not sources and not hypotheses:
                            st.info("Discovery returned no results. Ensure data is loaded.")

                    except json.JSONDecodeError:
                        st.code(result.stdout)
                else:
                    status.update(label="Failed", state="error")
                    st.error(f"Discovery failed: {result.stderr}")
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error: {e}")

    # Autonomous cycle launcher
    st.markdown("---")
    st.markdown("### Start Autonomous Cycle")
    st.markdown(
        "Launch an autonomous experiment cycle: discover data, consult principles, "
        "propose experiments, and run them with FDR-controlled evaluation."
    )

    col1, col2 = st.columns(2)
    with col1:
        cycle_name = st.text_input("Cycle Name", placeholder="exploration-cycle-1", key="disc_cycle_name")
        max_experiments = st.number_input("Max Experiments", value=5, min_value=1, max_value=50, key="disc_max_exp")
    with col2:
        fdr_rate = st.slider("FDR Rate", min_value=0.01, max_value=0.20, value=0.05, step=0.01,
                             help="False Discovery Rate threshold for multiple testing correction")
        holdout_weeks = st.number_input("Holdout Weeks", value=4, min_value=1, max_value=12, key="disc_holdout")

    if st.button("Start Cycle", type="secondary", width="stretch", key="disc_start_cycle"):
        if not cycle_name:
            st.error("Please enter a cycle name")
            return
        cmd = [
            sys.executable, "-m", "gefion.cli", "experiment", "cycle-start",
            cycle_name,
            "--fdr-rate", str(fdr_rate),
            "--holdout-weeks", str(holdout_weeks),
            "--max-experiments", str(max_experiments),
            "--json",
        ]
        env = os.environ.copy()
        with st.status("Starting cycle...", expanded=True) as status:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
                if result.returncode == 0:
                    status.update(label="Cycle started!", state="complete")
                    try:
                        data = json.loads(result.stdout)
                        st.success(f"Cycle created: ID {data.get('cycle_id', '?')}")
                    except json.JSONDecodeError:
                        st.success("Cycle started!")
                else:
                    status.update(label="Failed", state="error")
                    st.error(result.stderr)
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error: {e}")


def render_cycles_section():
    """Render experiment cycles list and status."""
    st.subheader("Experiment Cycles")
    st.markdown("View and manage experiment cycles with FDR-controlled evaluation.")

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
