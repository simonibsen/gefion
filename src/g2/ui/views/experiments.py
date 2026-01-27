"""Experiments page - AI-driven parameter optimization."""

import streamlit as st
import subprocess
import sys
import json
import os


def render_experiments():
    """Render the experiments page."""
    st.title("🧪 Experiments")
    st.markdown("AI-driven parameter optimization with human approval.")

    tab1, tab2, tab3, tab4 = st.tabs([
        "📋 List",
        "➕ Propose",
        "▶️ Run",
        "📊 Results"
    ])

    with tab1:
        render_list_section()

    with tab2:
        render_propose_section()

    with tab3:
        render_run_section()

    with tab4:
        render_results_section()


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
            ["all", "strategy_params", "feature_selection", "hyperparameter"],
            help="Filter by experiment type",
        )

    with col3:
        limit = st.number_input(
            "Limit",
            min_value=5,
            max_value=100,
            value=20,
        )

    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    # Build and show CLI command
    cmd_parts = ["g2", "experiment", "list"]
    if status_filter != "all":
        cmd_parts.extend(["--status", status_filter])
    if type_filter != "all":
        cmd_parts.extend(["--type", type_filter])
    cmd_parts.extend(["--limit", str(limit)])
    cmd_parts.append("--json")

    # Load experiments from database
    try:
        from g2.ui.components.database import get_connection

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
                        if st.button("✅ Approve", key=f"approve_{exp[0]}"):
                            approve_experiment(exp[0])
                    with col3:
                        if st.button("❌ Reject", key=f"reject_{exp[0]}"):
                            reject_experiment(exp[0])
        else:
            st.info("No experiments found matching the filter.")

    except Exception as e:
        st.error(f"Error loading experiments: {e}")
        st.info("The experiments table may not exist. Run `g2 db-migrate` to create it.")


def render_propose_section():
    """Render experiment proposal form."""
    st.subheader("Propose New Experiment")

    st.info("""
    Create a new experiment to optimize trading strategy parameters.
    The experiment will be created with status 'proposed' and requires approval before running.
    """)

    col1, col2 = st.columns(2)

    with col1:
        name = st.text_input(
            "Experiment Name",
            placeholder="momentum_optimization",
            help="Descriptive name for the experiment",
        )

        strategy = st.selectbox(
            "Strategy",
            ["momentum", "mean_reversion", "ma_crossover", "breakout"],
            help="Trading strategy to optimize",
        )

        search_method = st.selectbox(
            "Search Method",
            ["bayesian", "random", "grid"],
            help="bayesian is most efficient, grid is exhaustive",
        )

        max_trials = st.number_input(
            "Max Trials",
            min_value=5,
            max_value=500,
            value=50,
            help="Maximum number of parameter combinations to test",
        )

    with col2:
        objective = st.selectbox(
            "Objective Metric",
            ["sharpe_ratio", "total_return_pct", "sortino_ratio", "max_drawdown_pct"],
            help="Metric to optimize",
        )

        direction = st.selectbox(
            "Direction",
            ["maximize", "minimize"],
            help="Maximize for returns/Sharpe, minimize for drawdown",
        )

        # Symbol selection
        from g2.ui.components.database import get_symbols
        symbols = get_symbols()

        selected_symbols = st.multiselect(
            "Symbols",
            symbols,
            default=symbols[:10] if len(symbols) >= 10 else symbols,
            help="Symbols to backtest on",
        )

    # Date range
    st.markdown("##### Backtest Period")
    from datetime import date, timedelta
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date",
            value=date.today() - timedelta(days=365),
        )
    with col2:
        end_date = st.date_input(
            "End Date",
            value=date.today() - timedelta(days=1),
        )

    # Search space definition
    st.markdown("##### Search Space")
    st.caption("Define the parameter ranges to search")

    # Strategy-specific parameters
    search_space = {}

    if strategy == "momentum":
        col1, col2, col3 = st.columns(3)
        with col1:
            lb_low = st.number_input("Lookback Min", value=5, min_value=1)
            lb_high = st.number_input("Lookback Max", value=30, min_value=2)
            search_space["lookback_days"] = {"type": "int", "low": lb_low, "high": lb_high}
        with col2:
            tn_low = st.number_input("Top N Min", value=3, min_value=1)
            tn_high = st.number_input("Top N Max", value=15, min_value=2)
            search_space["top_n"] = {"type": "int", "low": tn_low, "high": tn_high}
        with col3:
            rb_low = st.number_input("Rebalance Min", value=1, min_value=1)
            rb_high = st.number_input("Rebalance Max", value=10, min_value=2)
            search_space["rebalance_days"] = {"type": "int", "low": rb_low, "high": rb_high}

    elif strategy == "mean_reversion":
        col1, col2 = st.columns(2)
        with col1:
            rsi_low = st.number_input("RSI Oversold Min", value=20, min_value=10)
            rsi_high = st.number_input("RSI Oversold Max", value=40, min_value=15)
            search_space["rsi_oversold"] = {"type": "int", "low": rsi_low, "high": rsi_high}
        with col2:
            rsi_ob_low = st.number_input("RSI Overbought Min", value=60, min_value=50)
            rsi_ob_high = st.number_input("RSI Overbought Max", value=80, min_value=55)
            search_space["rsi_overbought"] = {"type": "int", "low": rsi_ob_low, "high": rsi_ob_high}

    elif strategy == "ma_crossover":
        col1, col2 = st.columns(2)
        with col1:
            fast_low = st.number_input("Fast MA Min", value=10, min_value=5)
            fast_high = st.number_input("Fast MA Max", value=50, min_value=10)
            search_space["fast_period"] = {"type": "int", "low": fast_low, "high": fast_high}
        with col2:
            slow_low = st.number_input("Slow MA Min", value=100, min_value=50)
            slow_high = st.number_input("Slow MA Max", value=200, min_value=100)
            search_space["slow_period"] = {"type": "int", "low": slow_low, "high": slow_high}

    elif strategy == "breakout":
        col1, col2 = st.columns(2)
        with col1:
            lb_low = st.number_input("Lookback Min", value=10, min_value=5)
            lb_high = st.number_input("Lookback Max", value=30, min_value=10)
            search_space["lookback_days"] = {"type": "int", "low": lb_low, "high": lb_high}
        with col2:
            vol_low = st.number_input("Volume Threshold Min", value=1.0, min_value=1.0)
            vol_high = st.number_input("Volume Threshold Max", value=3.0, min_value=1.5)
            search_space["volume_threshold"] = {"type": "float", "low": vol_low, "high": vol_high}

    # Show search space JSON
    with st.expander("View Search Space JSON"):
        st.json(search_space)

    if st.button("📝 Propose Experiment", type="primary", use_container_width=True):
        if not name:
            st.error("Please enter an experiment name")
            return
        if not selected_symbols:
            st.error("Please select at least one symbol")
            return

        # Build CLI command
        search_space_json = json.dumps(search_space)
        symbols_str = ",".join(selected_symbols)

        cmd = [
            sys.executable, "-m", "g2.cli", "experiment", "propose",
            "--name", name,
            "--strategy", strategy,
            "--search-space", search_space_json,
            "--symbols", symbols_str,
            "--start-date", str(start_date),
            "--end-date", str(end_date),
            "--objective", objective,
            "--direction", direction,
            "--search-method", search_method,
            "--max-trials", str(max_trials),
            "--json",
        ]

        # Show CLI command
        cli_cmd = (f"g2 experiment propose --name {name} --strategy {strategy} "
                   f"--search-method {search_method} --max-trials {max_trials} "
                   f"--objective {objective}")
        st.code(cli_cmd, language="bash")

        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        with st.status("Proposing experiment...", expanded=True) as status:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                if result.returncode == 0:
                    status.update(label="✅ Experiment proposed!", state="complete")
                    try:
                        data = json.loads(result.stdout)
                        exp_id = data.get("experiment_id", data.get("id"))
                        st.success(f"Experiment #{exp_id} created. Go to List tab to approve it.")
                    except json.JSONDecodeError:
                        st.success("Experiment created successfully!")
                else:
                    status.update(label="❌ Failed", state="error")
                    st.error("Failed to create experiment")
                    st.code(result.stderr)

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")


def render_run_section():
    """Render experiment run section."""
    st.subheader("Run Experiment")

    st.info("""
    Run an approved experiment. The system will execute all trials
    (or until the goal is achieved with early stopping).
    """)

    # Get approved experiments
    try:
        from g2.ui.components.database import get_connection

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

            if st.button("▶️ Run Experiment", type="primary", use_container_width=True):
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

    if st.button("Run by ID", use_container_width=True):
        run_experiment(exp_id_input)


def render_results_section():
    """Render experiment results section."""
    st.subheader("Experiment Results")

    # Get completed experiments
    try:
        from g2.ui.components.database import get_connection

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

            if st.button("📊 Load Results", use_container_width=True):
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

    if st.button("View Results", use_container_width=True):
        load_experiment_results(exp_id_input, show_trials=show_trials)


def approve_experiment(exp_id: int):
    """Approve an experiment."""
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    cmd = [sys.executable, "-m", "g2.cli", "experiment", "approve", "--id", str(exp_id)]

    st.code(f"g2 experiment approve --id {exp_id}", language="bash")

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode == 0:
        st.success(f"Experiment #{exp_id} approved!")
        st.rerun()
    else:
        st.error(f"Failed: {result.stderr}")


def reject_experiment(exp_id: int):
    """Reject an experiment."""
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    cmd = [
        sys.executable, "-m", "g2.cli", "experiment", "reject",
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
    env["OTEL_ENABLED"] = "false"

    cmd = [sys.executable, "-m", "g2.cli", "experiment", "run", "--id", str(exp_id), "--json"]

    st.code(f"g2 experiment run --id {exp_id}", language="bash")

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
                status.update(label="✅ Experiment completed!", state="complete")
                st.success("Experiment finished. Check Results tab for details.")
            else:
                stderr = process.stderr.read()
                status.update(label="❌ Failed", state="error")
                st.error(f"Failed: {stderr}")

        except Exception as e:
            status.update(label="❌ Error", state="error")
            st.error(f"Error: {e}")


def load_experiment_results(exp_id: int, show_trials: bool = False):
    """Load and display experiment results."""
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    cmd = [sys.executable, "-m", "g2.cli", "experiment", "results", "--id", str(exp_id), "--json"]
    if show_trials:
        cmd.append("--show-trials")

    cli_cmd = f"g2 experiment results --id {exp_id}"
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
