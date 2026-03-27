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


def _get_system_context() -> str:
    """Shared system context available on every page."""
    parts = []
    try:
        import streamlit as _st
        from gefion.ui.components.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stocks")
                parts.append(f"{cur.fetchone()[0]} stocks tracked")

                cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                latest = cur.fetchone()[0]
                if latest:
                    parts.append(f"latest price data: {latest}")

                cur.execute("SELECT name, version, algorithm FROM ml_models WHERE active = true ORDER BY name")
                models = cur.fetchall()
                if models:
                    model_strs = [f"{n} {v} ({a})" if a else f"{n} {v}" for n, v, a in models]
                    parts.append(f"active models: {', '.join(model_strs)}")

                cur.execute("SELECT prediction_type, COUNT(*) FROM predictions GROUP BY prediction_type")
                preds = {r[0]: r[1] for r in cur.fetchall()}
                if preds:
                    pred_str = ", ".join(f"{k}: {v}" for k, v in preds.items())
                    parts.append(f"predictions: {pred_str}")

                cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE active = true")
                parts.append(f"{cur.fetchone()[0]} active features")
    except Exception:
        pass
    return "System state: " + "; ".join(parts) if parts else ""


def _build_context_prompt(page_context: Dict[str, Any]) -> Optional[str]:
    """Build a context prompt string from page context dict + system context."""
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

    # Add shared system context so AI knows cross-page state
    sys_ctx = _get_system_context()
    if sys_ctx:
        parts.append(sys_ctx)

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Chat widget CSS + rendering
# ---------------------------------------------------------------------------

_CHAT_BAR_HIDE_BUTTON_JS = """
<script>
(function() {
    const doc = window.parent.document;
    function hideAskButton() {
        const inputs = doc.querySelectorAll('input[aria-label="Ask"]');
        inputs.forEach(input => {
            const form = input.closest('[data-testid="stForm"]');
            if (form) {
                form.style.border = 'none';
                form.style.padding = '0';
                const btn = form.querySelector('[data-testid="stFormSubmitButton"]');
                if (btn) {
                    btn.style.position = 'absolute';
                    btn.style.width = '1px';
                    btn.style.height = '1px';
                    btn.style.overflow = 'hidden';
                    btn.style.clip = 'rect(0,0,0,0)';
                }
            }
        });
    }
    setTimeout(hideAskButton, 100);
    setTimeout(hideAskButton, 500);
    new MutationObserver(hideAskButton).observe(doc.body, {childList: true, subtree: true});
})();
</script>
"""


@st.cache_data(ttl=300)
def _check_mcp_status() -> Dict[str, Any]:
    """Check if the gefion MCP server is available to Claude CLI. Cached for 5 min."""
    import subprocess
    import os
    try:
        result = subprocess.run(
            ["claude", "-p", "ping", "--output-format", "stream-json", "--max-turns", "1"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "OTEL_ENABLED": "false"},
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        for line in output.splitlines():
            if '"mcp_servers"' in line:
                import json
                try:
                    data = json.loads(line.strip())
                    servers = data.get("mcp_servers", [])
                    for s in servers:
                        if s.get("name") in ("gefion", "g2"):
                            return {"available": s.get("status") == "connected", "name": s.get("name"), "status": s.get("status")}
                    return {"available": False, "name": None, "status": "not configured"}
                except json.JSONDecodeError:
                    pass
        return {"available": False, "name": None, "status": "no init event"}
    except Exception as e:
        return {"available": False, "name": None, "status": str(e)}


def render_chat_widget(page_context: Optional[Dict[str, Any]] = None) -> None:
    """Render the chat widget as an expander below the page title.

    Args:
        page_context: Optional dict from the page's get_page_context() function.
            Keys: page_name, summary, filters, data_stats, empty_states, errors, suggestions
    """
    page_name = (page_context or {}).get("page_name", "page")
    msg_key = f"_chat_{page_name}_messages"

    if msg_key not in st.session_state:
        st.session_state[msg_key] = []

    suggestions = (page_context or {}).get("suggestions", [])
    messages = st.session_state[msg_key]
    n_convos = len(messages) // 2

    # --- Label with MCP status indicator ---
    mcp = _check_mcp_status()
    if mcp["available"]:
        status_dot = ""
    else:
        status_dot = " (offline)"

    if n_convos == 0:
        label = f"Ask AI{status_dot}"
    elif n_convos == 1:
        label = f"Ask AI (1 conversation){status_dot}"
    else:
        label = f"Ask AI ({n_convos} conversations){status_dot}"

    with st.expander(label, expanded=False):
        # Show MCP warning if not connected
        if not mcp["available"]:
            st.warning(
                f"MCP server **{mcp.get('name') or 'gefion'}** is {mcp['status']}. "
                "AI questions may time out or fail. "
                "Run `gefion mcp-setup --force` to fix, then restart Claude Code.",
                icon="!",
            )
        # Input
        placeholder = "Ask about this page..."
        if suggestions and not messages:
            placeholder = suggestions[0]

        with st.form(f"chat_form_{page_name}", clear_on_submit=True, border=False):
            chat_input = st.text_input(
                "Ask",
                placeholder=placeholder,
                key=f"_chat_input_{page_name}",
                label_visibility="collapsed",
            )
            # Hidden submit button — Enter key still submits the form
            st.markdown(
                '<style>div[data-testid="stExpander"] [data-testid="stFormSubmitButton"] '
                "{display:none !important;}</style>",
                unsafe_allow_html=True,
            )
            submitted = st.form_submit_button("Ask")

        # Conversation history — each Q&A as its own expander
        if messages:
            pairs = []
            i = 0
            while i < len(messages):
                if messages[i]["role"] == "user":
                    q = messages[i]
                    a = messages[i + 1] if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant" else None
                    pairs.append((q, a))
                    i += 2 if a else 1
                else:
                    pairs.append((None, messages[i]))
                    i += 1

            # Each pair gets its own expander, newest first, most recent expanded
            for idx, (q, a) in enumerate(reversed(pairs)):
                question = q["content"] if q else "..."
                has_answer = a is not None
                prefix = "+" if has_answer else "..."
                is_latest = idx == 0

                with st.expander(
                    f"{prefix} {question}",
                    expanded=is_latest,
                ):
                    if q:
                        st.markdown(f"**Prompt:** {q['content']}")
                    if a:
                        # Show work done (tool calls) if present
                        work = a.get("work", [])
                        if work:
                            with st.expander(f"Work ({len(work)} tool calls)", expanded=False):
                                for event in work:
                                    if event["type"] == "tool_use":
                                        tool = event.get("tool", "unknown")
                                        inp = event.get("input", "")
                                        st.markdown(f"**{tool}**")
                                        if inp:
                                            st.code(inp, language="json")
                                    elif event["type"] == "tool_result":
                                        content = event.get("content", "")
                                        if content:
                                            st.caption(content[:200])

                        st.markdown(a["content"])

                        # Show duration/cost if available
                        meta_parts = []
                        if a.get("duration_ms"):
                            meta_parts.append(f"{a['duration_ms'] / 1000:.1f}s")
                        if a.get("cost_usd"):
                            meta_parts.append(f"${a['cost_usd']:.4f}")
                        if meta_parts:
                            st.caption(" | ".join(meta_parts))
                    elif q:
                        st.info("Waiting for response...")

            if st.button("Clear History", key=f"_chat_clear_{page_name}"):
                st.session_state[msg_key] = []
                st.rerun()

    # --- Handle submission ---
    if submitted and chat_input:
        st.session_state[msg_key].append({
            "role": "user", "content": chat_input,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

        context_prompt = _build_context_prompt(page_context)
        cmd_args, display_cmd, mode = parse_command_input(
            chat_input, context_prompt=context_prompt,
            session_key=f"_chat_{page_name}_ai_active",
        )

        result = _execute_chat_command(cmd_args, mode)
        st.session_state[msg_key].append({
            "role": "assistant",
            "content": result["content"],
            "work": result.get("work", []),
            "duration_ms": result.get("duration_ms"),
            "cost_usd": result.get("cost_usd"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        st.rerun()


def _execute_chat_command(cmd_args: List[str], mode: str) -> Dict[str, Any]:
    """Execute a command and return response with work events.

    Returns dict with: content (str), work (list of tool events), duration_ms, cost_usd.
    """
    import subprocess
    import os

    env = {**os.environ, "OTEL_ENABLED": "false"}
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            cmd_args, capture_output=True, text=True,
            timeout=120, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"content": "Request timed out."}
    except Exception as e:
        return {"content": f"Error: {e}"}

    if mode == "ai":
        all_output = (result.stdout or "") + "\n" + (result.stderr or "")
        text_parts = []
        final_result = ""
        work_events = []
        duration_ms = 0
        cost_usd = 0.0

        for line in all_output.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            event = parse_stream_event(line)
            if not event:
                continue
            if event["type"] == "result":
                final_result = event.get("result", "")
                duration_ms = event.get("duration_ms", 0)
                cost_usd = event.get("cost_usd", 0.0)
            elif event["type"] == "text":
                text_parts.append(event.get("text", ""))
            elif event["type"] == "tool_use":
                work_events.append(event)
            elif event["type"] == "tool_result":
                work_events.append(event)

        content = final_result or "".join(text_parts) or "No response received."
        return {
            "content": content,
            "work": work_events,
            "duration_ms": duration_ms,
            "cost_usd": cost_usd,
        }
    else:
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip() or "Command completed."
        if len(output) > 2000:
            output = output[:2000] + "\n... (truncated)"
        return {"content": f"```\n{output}\n```"}
