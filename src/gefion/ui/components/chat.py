"""Shared contextual chat component — floating bar + command routing.

Extracted from assistant.py so every page can embed AI chat.
"""
import json
import shlex
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st

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

UI_OPERATOR_PROMPT = (
    "You are responding to a request from the Gefion web UI. "
    "You are an OPERATOR — use Gefion MCP tools to answer questions and "
    "execute operations. Do NOT modify source code, create files, or "
    "perform developer operations. Focus on data analysis, ML workflows, "
    "and system operations using the available MCP tools. "
    "Keep responses concise and actionable."
)


def _param_to_flag(param: str) -> str:
    """Convert MCP parameter name to CLI flag."""
    return f"--{param.replace('_', '-')}"


def parse_stream_event(line: str) -> Optional[dict]:
    """Parse a stream-json event line from claude -p stderr.

    Returns a dict with 'type' and relevant fields, or None if not parseable.
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
                input_summary = json.dumps(tool_input)
                if len(input_summary) > 150:
                    input_summary = input_summary[:150] + "..."
                return {"type": "tool_use", "tool": tool_name, "input": input_summary}
            elif ct == "text":
                return {"type": "text", "text": content.get("text", "")}
        return None

    if event_type == "tool_result":
        return {"type": "tool_result", "content": json.dumps(data.get("content", ""))[:200]}

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
    if first in ("g2", "gefion"):
        return True
    if first in MCP_TOOL_MAP:
        return True
    if first.startswith("--"):
        return True
    if "-" in first and not first.startswith("-"):
        return True
    return False


def parse_command_input(
    text: str,
    context_prompt: Optional[str] = None,
    session_key: str = "ai_session_active",
) -> Tuple[List[str], str, str]:
    """Parse input as natural language, MCP tool call, or CLI command.

    Args:
        text: User input text
        context_prompt: Optional page context to prepend to AI prompts
        session_key: Session state key for multi-turn AI sessions

    Returns (cmd_args, display_cmd, mode) where:
      - cmd_args: subprocess args list
      - display_cmd: human-readable display string
      - mode: "ai", "cli", or "mcp"
    """
    text = text.strip()
    if not text:
        return [], "", ""

    if not _is_command(text):
        prompt = text
        if context_prompt:
            prompt = f"{context_prompt}\n\nUser question: {text}"
        system_prompt = UI_OPERATOR_PROMPT
        cmd = [
            "claude",
            "-p", prompt,
            "--append-system-prompt", system_prompt,
            "--allowedTools", "mcp__gefion__*",
            "--output-format", "stream-json",
            "--verbose",
        ]
        if st.session_state.get(session_key):
            cmd.append("--continue")
        return cmd, text, "ai"

    parts = shlex.split(text)
    first = parts[0]

    if first in ("g2", "gefion") and len(parts) > 1:
        first = parts[1]
        rest = parts[2:]
    else:
        rest = parts[1:]

    if first in MCP_TOOL_MAP:
        cli_prefix = MCP_TOOL_MAP[first][0]
        cli_args = []
        for arg in rest:
            if "=" in arg and not arg.startswith("--"):
                key, val = arg.split("=", 1)
                cli_args.append(_param_to_flag(key))
                cli_args.append(val)
            else:
                cli_args.append(arg)
        cli_parts = cli_prefix.split() + cli_args
        display = f"gefion {' '.join(cli_parts)}"
        cmd = [sys.executable, "-m", "gefion.cli"] + cli_parts + ["--json"]
        return cmd, display, "mcp"

    if parts[0] in ("g2", "gefion"):
        cli_parts = parts[1:]
    else:
        cli_parts = parts
    display = f"gefion {' '.join(cli_parts)}"
    cmd = [sys.executable, "-m", "gefion.cli"] + cli_parts + ["--json"]
    return cmd, display, "cli"


def _build_context_prompt(page_context: Dict[str, Any]) -> Optional[str]:
    """Build a context prompt string from page context dict."""
    if not page_context:
        return None

    parts = [f"The user is on the '{page_context.get('page_name', 'unknown')}' page."]

    summary = page_context.get("summary")
    if summary:
        parts.append(summary)

    filters = page_context.get("filters")
    if filters:
        filter_str = ", ".join(f"{k}={v}" for k, v in filters.items() if v)
        if filter_str:
            parts.append(f"Active filters: {filter_str}")

    stats = page_context.get("data_stats")
    if stats:
        stat_str = ", ".join(f"{k}: {v}" for k, v in stats.items())
        if stat_str:
            parts.append(f"Data on screen: {stat_str}")

    empty = page_context.get("empty_states")
    if empty:
        parts.append(f"Empty/missing: {'; '.join(empty)}")

    errors = page_context.get("errors")
    if errors:
        parts.append(f"Errors shown: {'; '.join(errors)}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Chat widget CSS + rendering
# ---------------------------------------------------------------------------

_CHAT_BAR_CSS = """
<style>
/* Pin the last stForm (the chat form) to viewport bottom */
.main [data-testid="stForm"]:last-of-type {
    position: fixed !important;
    bottom: 0 !important;
    left: var(--sidebar-width, 245px) !important;
    right: 0 !important;
    z-index: 999 !important;
    background: #ffffff !important;
    border-top: 1px solid #e0e0e0 !important;
    padding: 10px 24px !important;
    margin: 0 !important;
    box-shadow: 0 -2px 8px rgba(0,0,0,0.08) !important;
}
/* Slim down the form internals */
.main [data-testid="stForm"]:last-of-type [data-testid="stFormSubmitButton"] {
    display: none !important;
}
.main [data-testid="stForm"]:last-of-type [data-baseweb="input"] {
    background: #f8f9fa;
}
/* Prevent page content from hiding behind the fixed bar */
.main .block-container {
    padding-bottom: 70px !important;
}
</style>
"""


def render_chat_widget(page_context: Optional[Dict[str, Any]] = None) -> None:
    """Render the floating chat bar at the bottom of the page.

    Args:
        page_context: Optional dict from the page's get_page_context() function.
            Keys: page_name, summary, filters, data_stats, empty_states, errors, suggestions
    """
    # Inject fixed-position CSS
    st.markdown(_CHAT_BAR_CSS, unsafe_allow_html=True)

    page_name = (page_context or {}).get("page_name", "page")
    msg_key = f"_chat_{page_name}_messages"
    pending_key = f"_chat_{page_name}_pending"

    if msg_key not in st.session_state:
        st.session_state[msg_key] = []

    # Show suggestions as hints if chat is empty
    suggestions = (page_context or {}).get("suggestions", [])

    # Chat panel — expandable conversation history
    messages = st.session_state[msg_key]
    if messages:
        with st.expander(f"Page Chat ({len(messages)} messages)", expanded=len(messages) <= 4):
            for msg in messages[-10:]:
                ts = msg.get("timestamp", "")[-8:]  # HH:MM:SS
                if msg["role"] == "user":
                    st.markdown(f"> **You** ({ts}): {msg['content']}")
                else:
                    content = msg["content"]
                    # Long responses get their own expander
                    if len(content) > 300:
                        st.markdown(f"**AI** ({ts}):")
                        with st.expander("Show full response", expanded=False):
                            st.markdown(content)
                    else:
                        st.markdown(f"**AI** ({ts}): {content}")
                st.markdown("")  # spacing
            if len(messages) > 1:
                if st.button("Clear chat", key=f"_chat_clear_{page_name}"):
                    st.session_state[msg_key] = []
                    st.rerun()

    # Chat input
    with st.container():
        placeholder = "Ask about this page..."
        if suggestions and not messages:
            placeholder = suggestions[0]

        with st.form(f"chat_form_{page_name}", clear_on_submit=True):
            chat_input = st.text_input(
                "Ask",
                placeholder=placeholder,
                key=f"_chat_input_{page_name}",
                label_visibility="collapsed",
            )
            # Hide the submit button visually
            st.markdown(
                "<style>[data-testid='stFormSubmitButton'] {display: none;}</style>",
                unsafe_allow_html=True,
            )
            submitted = st.form_submit_button("Send")

        if submitted and chat_input:
            # Add user message
            st.session_state[msg_key].append({
                "role": "user",
                "content": chat_input,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })

            # Build context prompt and route command
            context_prompt = _build_context_prompt(page_context)
            cmd_args, display_cmd, mode = parse_command_input(
                chat_input,
                context_prompt=context_prompt,
                session_key=f"_chat_{page_name}_ai_active",
            )

            if mode == "ai":
                # Execute AI command and capture response
                import subprocess
                try:
                    result = subprocess.run(
                        cmd_args,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env={
                            **__import__("os").environ,
                            "OTEL_ENABLED": "false",
                        },
                    )
                    # Parse stream-json output for the final response
                    response_text = ""
                    for line in (result.stderr or "").splitlines():
                        event = parse_stream_event(line)
                        if event and event["type"] == "result":
                            response_text = event.get("result", "")
                            break
                        elif event and event["type"] == "text":
                            response_text += event.get("text", "")

                    if not response_text:
                        response_text = result.stdout.strip() or "No response received."

                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": response_text,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                except subprocess.TimeoutExpired:
                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": "Request timed out after 60 seconds.",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                except Exception as e:
                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": f"Error: {e}",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
            elif mode in ("cli", "mcp"):
                # Execute CLI command
                import subprocess
                try:
                    result = subprocess.run(
                        cmd_args,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env={
                            **__import__("os").environ,
                            "OTEL_ENABLED": "false",
                        },
                    )
                    output = result.stdout.strip() or result.stderr.strip() or "Command completed."
                    # Truncate long output
                    if len(output) > 2000:
                        output = output[:2000] + "\n... (truncated)"
                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": f"```\n{output}\n```",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                except subprocess.TimeoutExpired:
                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": "Command timed out.",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                except Exception as e:
                    st.session_state[msg_key].append({
                        "role": "assistant",
                        "content": f"Error running command: {e}",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })

            st.rerun()
