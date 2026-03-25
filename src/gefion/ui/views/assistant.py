"""AI Actions — context-aware operations center with conversation history."""

import json
import logging
import streamlit as st
import sys
import os
import shlex
import time
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import date

logger = logging.getLogger(__name__)

from gefion.ui.history import append_exchange, read_exchanges, clear_history
from gefion.ui.errors import read_session_errors
from gefion.ui.views.data import (
    start_background_process,
    render_process_status,
    get_process_state,
    stop_process,
    clear_process_state,
)


# MCP tool name -> (CLI command prefix, description)
MCP_TOOL_MAP = {
    "data_update": ("data-update", "Update prices and features"),
    "system_status": ("health", "System health check"),
    "health_check": ("health", "Infrastructure health check"),
    "ml_dataset_build": ("ml dataset-build", "Build ML dataset"),
    "ml_dataset_inspect": ("ml dataset-inspect", "Inspect dataset"),
    "ml_train": ("ml train", "Train quantile model"),
    "ml_predict": ("ml predict", "Generate predictions"),
    "ml_eval": ("ml eval", "Evaluate model"),
    "ml_tune": ("ml tune", "Tune hyperparameters"),
    "ml_train_classifier": ("ml train-classifier", "Train classifier"),
    "ml_predict_classifier": ("ml predict-classifier", "Predict with classifier"),
    "ml_train_ensemble": ("ml train-ensemble", "Train ensemble"),
    "ml_predict_ensemble": ("ml predict-ensemble", "Predict with ensemble"),
    "ml_feature_importance": ("ml feature-importance", "Feature importance"),
    "ml_e2e_test": ("ml e2e-test", "End-to-end ML test"),
    "ml_calibrate": ("ml calibrate", "Calibrate model"),
    "feature_compute": ("feat-compute", "Compute features"),
    "features_list": ("feat-def-list", "List features"),
    "feature_show": ("feat-def-show", "Show feature details"),
    "feature_functions_list": ("feat-fx-list", "List feature functions"),
    "feature_definitions_export": ("feat-def-export", "Export definitions"),
    "feature_definitions_import": ("feat-def-import", "Import definitions"),
    "feature_functions_export": ("feat-fx-export", "Export functions"),
    "feature_functions_import": ("feat-fx-import", "Import functions"),
    "cross_sectional_compute": ("cross-sectional-compute", "Compute rankings"),
    "backtest_run": ("backtest run", "Run backtest"),
    "backtest_compare": ("backtest compare", "Compare strategies"),
    "volatility_compute": ("volatility compute", "Compute volatility"),
    "strategy_list": ("strategy list", "List strategies"),
    "strategy_configs": ("strategy configs", "List strategy configs"),
    "strategy_create_config": ("strategy create-config", "Create strategy config"),
    "experiment_propose": ("experiment propose", "Propose experiment"),
    "experiment_list": ("experiment list", "List experiments"),
    "experiment_approve": ("experiment approve", "Approve experiment"),
    "experiment_run": ("experiment run", "Run experiment"),
    "experiment_results": ("experiment results", "Experiment results"),
    "chart_price": ("chart price", "Price chart"),
    "chart_predictions": ("chart predictions", "Prediction chart"),
    "chart_features": ("chart features", "Feature chart"),
    "backup": ("backup", "Backup data"),
    "restore": ("restore", "Restore data"),
}

# MCP param name -> CLI flag (snake_case to kebab-case)
def _param_to_flag(param: str) -> str:
    """Convert MCP parameter name to CLI flag."""
    return f"--{param.replace('_', '-')}"


UI_OPERATOR_PROMPT = (
    "You are responding to a request from the g2 web UI. "
    "You are an OPERATOR — use Gefion MCP tools to answer questions and "
    "execute operations. Do NOT modify source code, create files, or "
    "perform developer operations. Focus on data analysis, ML workflows, "
    "and system operations using the available MCP tools. "
    "Keep responses concise and actionable."
)


def parse_stream_event(line: str) -> Optional[dict]:
    """Parse a stream-json event line from claude -p stderr.

    Returns a dict with 'type' and relevant fields, or None if not parseable.
    Event types: 'tool_use', 'text', 'result', 'init', 'other'.
    """
    try:
        data = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    event_type = data.get("type", "")

    if event_type == "assistant":
        message = data.get("message", {})
        for content in message.get("content", []):
            ct = content.get("type", "")
            if ct == "tool_use":
                tool_name = content.get("name", "unknown")
                tool_input = content.get("input", {})
                # Summarize input (first 150 chars of JSON)
                input_summary = json.dumps(tool_input)
                if len(input_summary) > 150:
                    input_summary = input_summary[:150] + "..."
                return {
                    "type": "tool_use",
                    "tool": tool_name,
                    "input": input_summary,
                }
            elif ct == "text":
                return {"type": "text", "text": content.get("text", "")}
        return None

    if event_type == "tool_result":
        return {
            "type": "tool_result",
            "content": json.dumps(data.get("content", ""))[:200],
        }

    if event_type == "result":
        return {
            "type": "result",
            "result": data.get("result", ""),
            "duration_ms": data.get("duration_ms", 0),
            "cost_usd": data.get("total_cost_usd", 0),
        }

    if event_type == "system":
        return {"type": "init"}

    return None


def _is_command(text: str) -> bool:
    """Check if text looks like a CLI/MCP command vs natural language."""
    parts = text.strip().split()
    if not parts:
        return False
    first = parts[0]
    # Starts with g2 CLI prefix
    if first in ("g2", "gefion"):
        return True
    # Is a known MCP tool name
    if first in MCP_TOOL_MAP:
        return True
    # Starts with -- (flag)
    if first.startswith("--"):
        return True
    # Contains CLI-style subcommands (kebab-case like data-update, ml)
    if "-" in first and not first.startswith("-"):
        return True
    return False


def parse_command_input(text: str) -> Tuple[List[str], str, str]:
    """Parse input as natural language, MCP tool call, or CLI command.

    Returns (cmd_args, display_cmd, mode) where:
      - cmd_args: subprocess args list
      - display_cmd: human-readable display string
      - mode: "ai", "cli", or "mcp"
    """
    text = text.strip()
    if not text:
        return [], "", ""

    # Natural language -> route to claude -p with stream-json for transparency
    if not _is_command(text):
        cmd = [
            "claude",
            "-p", text,
            "--append-system-prompt", UI_OPERATOR_PROMPT,
            "--allowedTools", "mcp__gefion__*",
            "--output-format", "stream-json",
            "--verbose",
        ]
        # Continue previous AI session for multi-turn context
        if st.session_state.get("ai_session_active"):
            cmd.append("--continue")
        return cmd, text, "ai"

    # Try to parse as MCP tool name or CLI command
    parts = shlex.split(text)
    first = parts[0]

    # Strip "g2" prefix if present
    if first in ("g2", "gefion") and len(parts) > 1:
        first = parts[1]
        rest = parts[2:]
    else:
        rest = parts[1:]

    if first in MCP_TOOL_MAP:
        cli_prefix = MCP_TOOL_MAP[first][0]
        # Convert key=value params to --key value flags
        cli_args = []
        for arg in rest:
            if "=" in arg and not arg.startswith("--"):
                key, val = arg.split("=", 1)
                cli_args.append(_param_to_flag(key))
                cli_args.append(val)
            else:
                cli_args.append(arg)
        cli_parts = cli_prefix.split() + cli_args
        display = f"g2 {' '.join(cli_parts)}"
        cmd = [sys.executable, "-m", "gefion.cli"] + cli_parts + ["--json"]
        return cmd, display, "mcp"

    # Raw CLI command
    if parts[0] in ("g2", "gefion"):
        cli_parts = parts[1:]
    else:
        cli_parts = parts
    display = f"g2 {' '.join(cli_parts)}"
    cmd = [sys.executable, "-m", "gefion.cli"] + cli_parts + ["--json"]
    return cmd, display, "cli"


@dataclass
class SystemConditions:
    """Results of condition checks."""

    data_stale: bool = False
    data_last_date: Optional[date] = None
    data_days_old: int = 0
    no_models: bool = False
    model_count: int = 0
    no_predictions: bool = False
    prediction_count: int = 0
    needs_calibration: bool = False
    calibration_info: str = ""
    features_stale: bool = False
    feature_gap: int = 0
    no_eval: bool = False
    no_datasets: bool = False
    stock_count: int = 0
    predictions_aging: bool = False
    latest_prediction_date: Optional[date] = None


@dataclass
class Action:
    """A recommended action with reasoning."""

    title: str
    reason: str
    priority: str  # high, medium, low
    cli_cmd: str
    process_key: str


@st.cache_data(ttl=60)
def check_conditions() -> Optional[SystemConditions]:
    """Check system state and return conditions for action cards."""
    from gefion.ui.components.database import get_connection

    cond = SystemConditions()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Stock count
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM stocks WHERE status = 'Active'"
                    )
                    cond.stock_count = cur.fetchone()[0]
                except Exception as e:
                    logger.debug("Could not query stock count: %s", e)

                # 1. Data freshness
                cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                row = cur.fetchone()
                if row and row[0]:
                    cond.data_last_date = row[0]
                    today = date.today()
                    delta = (today - row[0]).days
                    cond.data_days_old = delta
                    # Stale if > 3 days old, or > 1 day on weekdays
                    cond.data_stale = delta > 3 or (today.weekday() < 5 and delta > 1)
                else:
                    cond.data_stale = True

                # 2. Model count
                try:
                    cur.execute("SELECT COUNT(*) FROM ml_models")
                    cond.model_count = cur.fetchone()[0]
                    cond.no_models = cond.model_count == 0
                except Exception as e:
                    logger.debug("Could not query ml_models: %s", e)
                    cond.no_models = True

                # 3. Dataset count
                try:
                    cur.execute("SELECT COUNT(*) FROM ml_datasets")
                    ds_count = cur.fetchone()[0]
                    cond.no_datasets = ds_count == 0
                except Exception as e:
                    logger.debug("Could not query ml_datasets: %s", e)
                    cond.no_datasets = True

                # 4. Recent predictions
                try:
                    cur.execute("""
                        SELECT COUNT(*), MAX(prediction_date)
                        FROM quantile_predictions
                        WHERE prediction_date > CURRENT_DATE - INTERVAL '7 days'
                    """)
                    row = cur.fetchone()
                    cond.prediction_count = row[0]
                    cond.latest_prediction_date = row[1]
                    cond.no_predictions = cond.model_count > 0 and cond.prediction_count == 0
                    # Predictions aging if latest is > 2 days old
                    if row[1]:
                        pred_age = (date.today() - row[1]).days
                        cond.predictions_aging = pred_age > 2
                except Exception as e:
                    logger.debug("Could not query quantile_predictions: %s", e)

                # 5. Calibration quality (from latest model_performance)
                try:
                    cur.execute("""
                        SELECT metrics FROM model_performance
                        ORDER BY evaluated_at DESC LIMIT 1
                    """)
                    row = cur.fetchone()
                    if row and row[0]:
                        metrics = row[0]
                        q50_cal = metrics.get("q50_calibration", 50)
                        if abs(q50_cal - 50) > 15:
                            cond.needs_calibration = True
                            cond.calibration_info = (
                                f"Q50 coverage at {q50_cal:.0f}% (target: 50%)"
                            )
                    else:
                        cond.no_eval = True
                except Exception as e:
                    logger.debug("Could not query model_performance: %s", e)
                    cond.no_eval = True

                # 6. Feature coverage gap
                try:
                    cur.execute(
                        "SELECT COUNT(DISTINCT feature_name) FROM computed_features"
                    )
                    actual = cur.fetchone()[0]
                    cur.execute(
                        "SELECT COUNT(*) FROM feature_definitions WHERE active = true"
                    )
                    expected = cur.fetchone()[0]
                    if expected > 0 and actual < expected:
                        cond.features_stale = True
                        cond.feature_gap = expected - actual
                except Exception as e:
                    logger.debug("Could not query feature coverage: %s", e)

        return cond
    except Exception as e:
        logger.warning("Failed to check system conditions: %s", e)
        return None


def build_actions(conditions: SystemConditions) -> List[Action]:
    """Build prioritized action list from system conditions.

    Always returns at least 4 actions by including proactive suggestions
    when fewer than 4 issues are detected.
    """
    actions: List[Action] = []

    # --- Issue-driven actions (only when condition is true) ---

    # High priority
    if conditions.data_stale:
        if conditions.data_last_date:
            reason = (
                f"Price data is {conditions.data_days_old} day(s) old "
                f"(last: {conditions.data_last_date}). Models and predictions "
                f"rely on current data — stale data means stale signals."
            )
        else:
            reason = (
                "No price data found. This is the first step — all features, "
                "models, and predictions depend on having OHLCV data."
            )
        actions.append(Action(
            title="Update Market Data",
            reason=reason,
            priority="high",
            cli_cmd="gefion data-update --exchange NASDAQ",
            process_key="action_data_update",
        ))

    if conditions.no_datasets and not conditions.no_models:
        actions.append(Action(
            title="Build Training Dataset",
            reason=(
                "No ML datasets found. A dataset bundles price data and features "
                "into train/test splits — required before training any model."
            ),
            priority="high",
            cli_cmd="gefion ml dataset-build --name nasdaq --version v1 "
                    "--exchange NASDAQ --export",
            process_key="action_dataset",
        ))

    if conditions.no_models:
        if conditions.no_datasets:
            reason = (
                "No ML models or datasets exist. Build a dataset first, then "
                "train a model to unlock predictions, backtesting with ML "
                "signals, and evaluation."
            )
            actions.append(Action(
                title="Build Training Dataset",
                reason=reason,
                priority="high",
                cli_cmd="gefion ml dataset-build --name nasdaq --version v1 "
                        "--exchange NASDAQ --export",
                process_key="action_dataset",
            ))
        actions.append(Action(
            title="Train First Model",
            reason=(
                "No ML models found. Training a quantile model enables "
                "price range predictions (q10/q50/q90) and ML-driven "
                "backtesting strategies."
            ),
            priority="high",
            cli_cmd="gefion ml train --dataset-name nasdaq --dataset-version v1 "
                    "--model-name quantile --model-version v1 --algorithm xgboost",
            process_key="action_train",
        ))

    # Medium priority
    if conditions.no_predictions and not conditions.no_models:
        actions.append(Action(
            title="Generate Predictions",
            reason=(
                f"{conditions.model_count} model(s) available but no recent "
                f"predictions. Generate predictions to see where stocks "
                f"are headed and enable ML-based backtesting."
            ),
            priority="medium",
            cli_cmd="gefion ml predict --model-name quantile --model-version v1 "
                    "--prediction-date today --exchange NASDAQ",
            process_key="action_predict",
        ))

    if conditions.predictions_aging and not conditions.no_predictions:
        actions.append(Action(
            title="Refresh Predictions",
            reason=(
                f"Latest predictions are from {conditions.latest_prediction_date}. "
                f"Predictions should be regenerated regularly to reflect "
                f"the most recent price and feature data."
            ),
            priority="medium",
            cli_cmd="gefion ml predict --model-name quantile --model-version v1 "
                    "--prediction-date today --exchange NASDAQ",
            process_key="action_predict",
        ))

    if conditions.needs_calibration:
        actions.append(Action(
            title="Calibrate Models",
            reason=(
                f"{conditions.calibration_info}. Miscalibrated quantiles mean "
                f"prediction intervals are unreliable — conformal calibration "
                f"corrects this."
            ),
            priority="medium",
            cli_cmd="gefion ml calibrate --model-name quantile --model-version v1",
            process_key="action_calibrate",
        ))

    if conditions.no_eval and conditions.model_count > 0:
        actions.append(Action(
            title="Evaluate Model Performance",
            reason=(
                "No evaluation results found. Running eval calculates "
                "calibration metrics (q10/q50/q90 coverage, pinball loss) "
                "so you know whether predictions are trustworthy."
            ),
            priority="medium",
            cli_cmd="gefion ml eval --model-name quantile --model-version v1 "
                    "--start-date 2025-01-01 --end-date 2025-12-31",
            process_key="action_eval",
        ))

    # Low priority
    if conditions.features_stale:
        actions.append(Action(
            title="Compute Missing Features",
            reason=(
                f"{conditions.feature_gap} feature definition(s) have no "
                f"computed data. Features feed into ML models — missing "
                f"features mean the model has blind spots."
            ),
            priority="low",
            cli_cmd="gefion feat-compute --all-features",
            process_key="action_features",
        ))

    # --- Proactive suggestions (fill to at least 4) ---
    proactive = []

    if conditions.model_count > 0 and not conditions.no_predictions:
        proactive.append(Action(
            title="Run Strategy Backtest",
            reason=(
                "Proactive: test how trading strategies perform on historical "
                "data. Compare momentum, mean reversion, and ML-signal "
                "strategies to find the best approach."
            ),
            priority="low",
            cli_cmd="gefion backtest compare --strategies momentum,mean_reversion,"
                    "ma_crossover --exchange NASDAQ --start-date 2025-01-01 "
                    "--end-date 2025-12-31",
            process_key="action_backtest",
        ))

    if conditions.stock_count > 0:
        proactive.append(Action(
            title="Check System Health",
            reason=(
                "Proactive: run a full system status check to verify "
                "infrastructure health, data coverage, and identify any "
                "gaps or issues."
            ),
            priority="low",
            cli_cmd="gefion health",
            process_key="action_status",
        ))

    if conditions.model_count > 0:
        proactive.append(Action(
            title="Analyze Feature Importance",
            reason=(
                "Proactive: understand which features drive model predictions. "
                "SHAP-based importance reveals whether the model relies on "
                "meaningful signals or noise."
            ),
            priority="low",
            cli_cmd="gefion ml feature-importance --model-name quantile "
                    "--model-version v1 --horizon 7",
            process_key="action_importance",
        ))

    if conditions.stock_count > 0 and not conditions.data_stale:
        proactive.append(Action(
            title="Recompute All Features",
            reason=(
                "Proactive: refresh all technical indicators and "
                "cross-sectional features for the latest price data. "
                "Ensures ML inputs are up to date."
            ),
            priority="low",
            cli_cmd="gefion feat-compute --all-features --update-existing",
            process_key="action_recompute_features",
        ))

    # Add proactive suggestions until we have at least 4 actions
    for p in proactive:
        if len(actions) >= 4:
            break
        # Avoid duplicating process keys
        if not any(a.process_key == p.process_key for a in actions):
            actions.append(p)

    return actions


def render_action_card(action: Action):
    """Render a single action card with reasoning, CLI preview, and Run button."""
    with st.container(border=True):
        badge = {"high": "🔴", "medium": "🟡", "low": "🔵"}[action.priority]
        st.markdown(f"{badge} **{action.title}**")
        st.markdown(action.reason)
        st.code(action.cli_cmd, language="bash")

        state = get_process_state(action.process_key)
        if state.is_running or state.completed:
            render_process_status(action.process_key, action.title)
        elif st.button("▶ Run", key=f"run_{action.process_key}", type="primary"):
            parts = action.cli_cmd.strip().split()
            if parts and parts[0] in ("g2", "gefion"):
                parts = parts[1:]
            cmd = [sys.executable, "-m", "gefion.cli"] + parts + ["--json"]
            env = os.environ.copy()
            env["OTEL_ENABLED"] = "false"
            start_background_process(action.process_key, cmd, env)
            st.rerun()


def render_freeform_output(key: str, mode: str):
    """Render output for freeform prompt/command (plain text, no data-update metrics)."""
    state = get_process_state(key)
    if not state.is_running and not state.completed:
        return

    if state.is_running:
        label = "Thinking..." if mode == "ai" else "Running..."
        st_state = "running"
    elif state.success:
        label = "Response" if mode == "ai" else "Completed"
        st_state = "complete"
    else:
        label = "Failed"
        st_state = "error"

    with st.status(label, expanded=True, state=st_state):
        # Show the prompt that triggered this response
        prompt_text = st.session_state.get("freeform_prompt", "")
        if prompt_text:
            st.caption(f"**{prompt_text}**")

        # For AI mode, parse stream-json events to show response
        if mode == "ai":
            # Try stderr (work_events) first, fall back to stdout (output_lines)
            all_events = getattr(state, 'work_events', []) or getattr(state, 'output_lines', [])
            response_text = ""
            tool_calls = []
            for evt_line in all_events:
                evt = parse_stream_event(evt_line)
                if evt and evt["type"] == "result":
                    response_text = evt.get("result", "")
                elif evt and evt["type"] == "text" and not response_text:
                    response_text += evt.get("text", "")
                elif evt and evt["type"] in ("tool_use", "tool_result"):
                    tool_calls.append(evt)
            if response_text:
                st.markdown(response_text)
            elif state.is_running:
                st.markdown("*Waiting for response...*")

            # Work section — nested inside the status block
            # Group consecutive same-tool calls together
            if tool_calls or state.is_running:
                tool_use_count = sum(1 for tc in tool_calls if tc["type"] == "tool_use")
                work_label = f"Work ({tool_use_count} tool calls)" if tool_use_count else "Work"
                with st.expander(work_label, expanded=state.is_running):
                    if not tool_calls and state.is_running:
                        st.markdown("*Waiting for tool calls...*")
                    # Group tool_use events by consecutive runs of the same tool
                    groups = []
                    for tc in tool_calls:
                        if tc["type"] == "tool_use":
                            tool_name = tc["tool"]
                            if tool_name.startswith("mcp__gefion__"):
                                tool_name = tool_name[len("mcp__gefion__"):]
                            if groups and groups[-1]["tool"] == tool_name:
                                groups[-1]["calls"].append(tc)
                            else:
                                groups.append({"tool": tool_name, "calls": [tc]})
                        elif tc["type"] == "tool_result" and groups:
                            groups[-1].setdefault("results", []).append(tc)
                    for group in groups:
                        count = len(group["calls"])
                        if count > 1:
                            st.markdown(f":material/build: **{group['tool']}** ({count} calls)")
                        else:
                            st.markdown(f":material/build: **{group['tool']}**")
                        for call in group["calls"]:
                            if call["input"] and call["input"] != "{}":
                                st.code(call["input"], language="json")
                        st.markdown("---")
        else:
            output_lines = getattr(state, 'output_lines', [])
            if output_lines:
                st.markdown("\n".join(output_lines))

        if state.error_message:
            st.error(state.error_message)

    # Control buttons
    col1, col2 = st.columns(2)
    if state.is_running:
        if col1.button("Stop", key=f"stop_{key}"):
            stop_process(key)
            st.rerun()
    if state.completed:
        if col1.button("Clear", key=f"clear_{key}"):
            clear_process_state(key)
            st.rerun()

    # Auto-refresh while running so output streams to the user
    if state.is_running:
        time.sleep(1.5)
        st.rerun()


def render_assistant():
    """Render the AI Actions page."""
    st.title("AI Actions")
    st.markdown("Run commands, ask questions, and explore your data.")

    # --- Session errors indicator ---
    session_errors = read_session_errors()
    if session_errors:
        with st.expander(f"Errors ({len(session_errors)})", expanded=False):
            if st.button("Clear Errors", key="clear_errors_btn"):
                from gefion.ui.errors import clear_errors
                clear_errors()
                st.rerun()
            for err in session_errors:
                ts = err.get("timestamp", "")[:19]
                source = err.get("source", "unknown")
                msg = err.get("message", "")
                st.error(f"**[{ts}] {source}**: {msg[:300]}")

    # --- Section 1: Prompt entry + conversation history ---
    st.subheader("Ask AI / Run Command")
    st.caption(
        "Type a natural language request (routed to Claude with Gefion MCP tools) "
        "or a direct command (e.g. `g2 data-update --exchange NASDAQ`)."
    )

    history = read_exchanges()

    with st.form("freeform_form", clear_on_submit=True):
        cmd_input = st.text_input(
            "Prompt or command",
            placeholder="Which stocks had the biggest moves this week?" if not history else "",
            key="freeform_cmd",
        )
        # Hide submit button, reduce form padding, sharpen input border
        st.markdown(
            "<style>"
            "[data-testid='stFormSubmitButton'] {display: none;}"
            "[data-testid='stForm'] {border: none; padding: 0;}"
            "[data-testid='stTextInput'] input {"
            "  border: 1px solid #ccc; border-radius: 4px;"
            "  background-color: #fff;"
            "}"
            "[data-testid='stTextInput'] input:focus {"
            "  border-color: #4a7cf7; box-shadow: 0 0 0 1px #4a7cf7;"
            "}"
            "</style>",
            unsafe_allow_html=True,
        )
        submitted = st.form_submit_button("Run")

    cmd_args: List[str] = []
    display_cmd = ""
    mode = ""
    if cmd_input:
        try:
            cmd_args, display_cmd, mode = parse_command_input(cmd_input)
        except ValueError as e:
            st.error(f"Could not parse input: {e}")
            cmd_args, display_cmd, mode = [], "", ""

        if mode == "ai":
            st.caption("Will send to Claude with Gefion MCP tools (operator mode)")
        elif display_cmd:
            st.code(display_cmd, language="bash")

    freeform_state = get_process_state("freeform_cmd")
    if freeform_state.is_running or freeform_state.completed:
        freeform_mode = st.session_state.get("freeform_mode", "cli")
        render_freeform_output("freeform_cmd", freeform_mode)

    # Save completed exchange to history (once per completion)
    if freeform_state.completed and not st.session_state.get("freeform_saved"):
        prompt_text = st.session_state.get("freeform_prompt", "")
        freeform_mode = st.session_state.get("freeform_mode", "cli")
        # For AI mode, extract response from stream-json events
        if freeform_mode == "ai":
            all_events = (
                getattr(freeform_state, "work_events", [])
                or getattr(freeform_state, "output_lines", [])
            )
            response_text = ""
            for evt_line in all_events:
                evt = parse_stream_event(evt_line)
                if evt and evt["type"] == "result":
                    response_text = evt.get("result", "")
        else:
            output_lines = getattr(freeform_state, "output_lines", [])
            response_text = "\n".join(output_lines) if output_lines else ""
        append_exchange(
            prompt=prompt_text,
            mode=freeform_mode,
            response=response_text,
            success=freeform_state.success,
            duration_sec=0.0,
        )
        st.session_state["freeform_saved"] = True
        # Mark AI session as active so subsequent prompts use --continue
        if freeform_mode == "ai" and freeform_state.success:
            st.session_state["ai_session_active"] = True

    if submitted and cmd_args and not freeform_state.is_running:
        clear_process_state("freeform_cmd")
        st.session_state["freeform_mode"] = mode
        st.session_state["freeform_prompt"] = cmd_input
        st.session_state["freeform_saved"] = False
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        # Remove CLAUDECODE so claude -p can run (it refuses nested sessions)
        env.pop("CLAUDECODE", None)
        start_background_process("freeform_cmd", cmd_args, env)
        st.rerun()

    # Render conversation history (nested: History expander > individual exchanges)
    if history:
        with st.expander(f"History ({len(history)} exchanges)", expanded=False):
            if st.button("Clear History", key="clear_history_btn"):
                clear_history()
                st.session_state["ai_session_active"] = False
                st.rerun()
            for i, ex in enumerate(reversed(history)):
                status = "✓" if ex.get("success", True) else "✗"
                prompt_preview = ex["prompt"][:80]
                with st.expander(f"{status} {prompt_preview}", expanded=(i == 0)):
                    st.markdown(f"**Prompt:** {ex['prompt']}")
                    if ex.get("success", True):
                        st.markdown(ex.get("response", ""))
                    else:
                        st.error(ex.get("response", "Command failed"))

    # MCP tool reference
    with st.expander("Available MCP Tools"):
        for tool_name, (_, desc) in sorted(MCP_TOOL_MAP.items()):
            st.markdown(f"- `{tool_name}` — {desc}")

    st.markdown("---")

    # --- Section 2: System conditions -> Action cards ---
    st.subheader("Suggested Actions")
    conditions = check_conditions()

    if conditions:
        actions = build_actions(conditions)

        if actions:
            for action in actions:
                render_action_card(action)
        else:
            st.success("All systems healthy — no actions needed.")
    else:
        st.warning("Could not check system conditions. Is the database running?")

    st.markdown("---")

    # --- Section 3: Quick stats ---
    st.subheader("System Overview")
    from gefion.ui.components.status import get_system_stats

    stats = get_system_stats()
    if stats:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stocks", stats.active_stocks)
        c2.metric("Prices", f"{stats.ohlcv_rows:,}")
        c3.metric("Models", stats.model_count)
        c4.metric("Predictions", f"{stats.prediction_count:,}")
