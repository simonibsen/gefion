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


def _render_inline_charts(message: Dict[str, Any]) -> None:
    """Detect chart HTML files in a message and render them inline."""
    import re
    from pathlib import Path
    import streamlit.components.v1 as components

    # Search for chart file paths in the response text and tool results
    text_to_search = message.get("content", "")
    for event in message.get("work", []):
        if event.get("type") == "tool_result":
            text_to_search += " " + event.get("content", "")

    # Find .html file paths (e.g., /Users/.../charts/AAPL_price_20260328.html)
    html_paths = re.findall(r'(/[^\s"\']+\.html)', text_to_search)
    # Also check ~/.gefion/charts/ pattern
    html_paths += re.findall(r'(~/.+?\.html)', text_to_search)

    rendered = set()
    for path_str in html_paths:
        path_str = path_str.replace("~", str(Path.home()))
        path = Path(path_str)
        if path.exists() and path.suffix == ".html" and str(path) not in rendered:
            try:
                html = path.read_text()
                if "d3" in html.lower() or "plotly" in html.lower() or "<svg" in html.lower():
                    st.caption(f"Chart: {path.name}")
                    components.html(html, height=600, scrolling=True)
                    rendered.add(str(path))
            except Exception:
                pass


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


@st.cache_data(ttl=600)
def _check_mcp_status() -> Dict[str, Any]:
    """Lightweight check: verify claude CLI exists and MCP config is present."""
    import shutil
    import os
    from pathlib import Path

    # Check claude CLI exists
    if not shutil.which("claude"):
        return {"available": False, "status": "claude CLI not found"}

    # Check MCP config exists (don't spawn a process)
    config_paths = [
        Path.home() / ".claude.json",
        Path.home() / ".config" / "claude" / "config.json",
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                import json
                config = json.loads(config_path.read_text())
                servers = config.get("mcpServers", {})
                if "gefion" in servers:
                    return {"available": True, "status": "configured"}
                if "g2" in servers:
                    return {"available": True, "status": "configured (as g2)"}
            except Exception:
                pass

    # Fallback: assume available if claude exists (config might be elsewhere)
    return {"available": True, "status": "assumed"}


def render_chat_widget(page_context: Optional[Dict[str, Any]] = None) -> None:
    """Render the chat widget as an expander below the page title.

    Delegates to a @st.fragment so polling for AI responses doesn't
    trigger a full page re-render.
    """
    # Store context in session state so the fragment can access it
    st.session_state["_chat_page_context"] = page_context or {}
    _render_chat_fragment()


@st.fragment
def _render_chat_fragment() -> None:
    """Inner fragment — re-renders independently from the rest of the page."""
    page_context = st.session_state.get("_chat_page_context", {})
    page_name = page_context.get("page_name", "page")
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
        label = f"Ask Gefion{status_dot}"
    elif n_convos == 1:
        label = f"Ask Gefion (1 conversation){status_dot}"
    else:
        label = f"Ask Gefion ({n_convos} conversations){status_dot}"

    from gefion.ui.views.data import start_background_process, get_process_state, clear_process_state, stop_process

    process_key = f"chat_{page_name}"
    chat_state = get_process_state(process_key)
    is_busy = chat_state.is_running
    just_completed = chat_state.completed and not st.session_state.get(f"_chat_{page_name}_saved")

    # Keep expander open while busy, just completed, or just answered
    # Track when we last got a response so we keep it open
    just_answered = st.session_state.get(f"_chat_{page_name}_just_answered", False)
    should_expand = is_busy or just_completed or just_answered

    with st.expander(label, expanded=should_expand):
        # MCP warning
        if not mcp["available"]:
            st.warning(
                f"MCP server **{mcp.get('name') or 'gefion'}** is {mcp['status']}. "
                "AI questions may time out or fail. "
                "Run `gefion mcp-setup --force` to fix, then restart Claude Code.",
                icon="\u26a0\ufe0f",
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
                disabled=is_busy,
            )
            st.markdown(
                "<style>"
                'div[data-testid="stExpander"] [data-testid="stFormSubmitButton"] '
                "{display:none !important;} "
                'div[data-testid="stExpander"] [data-testid="stForm"] '
                "{padding-bottom:0 !important; margin-bottom:-1rem !important;}"
                "</style>",
                unsafe_allow_html=True,
            )
            submitted = st.form_submit_button("Ask")

        # --- Live status while running ---
        if is_busy:
            prompt_text = st.session_state.get(f"_chat_{page_name}_prompt", "")
            mode = st.session_state.get(f"_chat_{page_name}_mode", "ai")

            # Stream-json events go to stdout (output_lines), stderr has work_events
            # Check both — claude may write to either depending on version
            all_events = list(getattr(chat_state, 'output_lines', [])) + list(getattr(chat_state, 'work_events', []))
            tool_calls = []
            text_parts = []
            for evt_line in all_events:
                evt = parse_stream_event(evt_line)
                if not evt:
                    continue
                if evt["type"] in ("tool_use", "tool_result"):
                    tool_calls.append(evt)
                elif evt["type"] == "text":
                    text_parts.append(evt.get("text", ""))

            # Build status label with details
            if tool_calls:
                last_tool = tool_calls[-1]
                if last_tool["type"] == "tool_use":
                    tool_name = last_tool.get("tool", "")
                    if tool_name.startswith("mcp__gefion__"):
                        tool_name = tool_name[len("mcp__gefion__"):]
                    status_label = f"Using {tool_name}..."
                else:
                    status_label = f"Processing ({len(tool_calls)} tool calls)..."
            elif text_parts:
                status_label = "Writing response..."
            else:
                status_label = "Thinking..." if mode == "ai" else "Running..."

            with st.status(status_label, expanded=True, state="running"):
                if prompt_text:
                    st.caption(f"**{prompt_text}**")

                # Show all events in order — tool calls, results, and text
                has_output = False
                for evt_line in all_events:
                    evt = parse_stream_event(evt_line)
                    if not evt:
                        continue
                    if evt["type"] == "tool_use":
                        has_output = True
                        tool = evt.get("tool", "")
                        if tool.startswith("mcp__gefion__"):
                            tool = tool[len("mcp__gefion__"):]
                        st.markdown(f":material/build: **{tool}**")
                        if evt.get("input") and evt["input"] != "{}":
                            st.code(evt["input"], language="json")
                    elif evt["type"] == "tool_result":
                        has_output = True
                        content = evt.get("content", "")
                        if content:
                            # Show truncated result
                            preview = content[:300]
                            if len(content) > 300:
                                preview += "..."
                            st.caption(preview)
                    elif evt["type"] == "text":
                        has_output = True
                        st.markdown(evt.get("text", ""))

                if not has_output:
                    st.empty()  # spinner animation is sufficient

            # Stop button
            if st.button("Stop", key=f"_chat_stop_{page_name}"):
                stop_process(process_key)
                st.session_state[msg_key].append({
                    "role": "assistant",
                    "content": "Stopped by user.",
                    "work": tool_calls,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                st.session_state[f"_chat_{page_name}_saved"] = True
                clear_process_state(process_key)
                st.rerun()

            # Auto-refresh to poll for updates
            time.sleep(1.5)
            st.rerun()

        # --- Process completed response ---
        if just_completed:
            mode = st.session_state.get(f"_chat_{page_name}_mode", "ai")
            all_events = list(getattr(chat_state, 'output_lines', [])) + list(getattr(chat_state, 'work_events', []))

            if mode == "ai":
                response_text = ""
                work_events = []
                duration_ms = 0
                for evt_line in all_events:
                    evt = parse_stream_event(evt_line)
                    if not evt:
                        continue
                    if evt["type"] == "result":
                        response_text = evt.get("result", "")
                        duration_ms = evt.get("duration_ms", 0)
                    elif evt["type"] == "text" and not response_text:
                        response_text += evt.get("text", "")
                    elif evt["type"] in ("tool_use", "tool_result"):
                        work_events.append(evt)
                if not response_text:
                    output_lines = getattr(chat_state, 'output_lines', [])
                    response_text = "\n".join(output_lines) if output_lines else "No response received."
            else:
                output_lines = getattr(chat_state, 'output_lines', [])
                response_text = "\n".join(output_lines) if output_lines else "Command completed."
                work_events = []
                duration_ms = 0

            st.session_state[msg_key].append({
                "role": "assistant",
                "content": response_text,
                "work": work_events,
                "duration_ms": duration_ms,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            st.session_state[f"_chat_{page_name}_saved"] = True
            st.session_state[f"_chat_{page_name}_just_answered"] = True
            # Enable --continue for follow-up questions on this page
            if mode == "ai" and response_text and response_text != "No response received.":
                st.session_state[f"_chat_{page_name}_ai_active"] = True
            clear_process_state(process_key)
            st.rerun()

        # --- Conversation history ---
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

            for idx, (q, a) in enumerate(reversed(pairs)):
                question = q["content"] if q else "..."
                has_answer = a is not None
                prefix = "+" if has_answer else "..."
                is_latest = idx == 0

                with st.expander(f"{prefix} {question}", expanded=is_latest):
                    if q:
                        st.markdown(f"**Prompt:** {q['content']}")
                    if a:
                        work = a.get("work", [])
                        if work:
                            tool_use_count = sum(1 for w in work if w.get("type") == "tool_use")
                            with st.expander(f"Work ({tool_use_count} tool calls)", expanded=False):
                                for event in work:
                                    if event["type"] == "tool_use":
                                        tool = event.get("tool", "unknown")
                                        if tool.startswith("mcp__gefion__"):
                                            tool = tool[len("mcp__gefion__"):]
                                        inp = event.get("input", "")
                                        st.markdown(f":material/build: **{tool}**")
                                        if inp and inp != "{}":
                                            st.code(inp, language="json")

                        st.markdown(a["content"])

                        # Render any chart files referenced in the response or tool results
                        _render_inline_charts(a)

                        if a.get("duration_ms"):
                            st.caption(f"{a['duration_ms'] / 1000:.1f}s")

            if st.button("Clear History", key=f"_chat_clear_{page_name}"):
                st.session_state[msg_key] = []
                st.session_state[f"_chat_{page_name}_ai_active"] = False
                st.session_state[f"_chat_{page_name}_just_answered"] = False
                st.rerun()

    # --- Handle submission (outside expander) ---
    if submitted and chat_input and not is_busy:
        st.session_state[msg_key].append({
            "role": "user", "content": chat_input,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        st.session_state[f"_chat_{page_name}_prompt"] = chat_input
        st.session_state[f"_chat_{page_name}_saved"] = False
        st.session_state[f"_chat_{page_name}_just_answered"] = False

        context_prompt = _build_context_prompt(page_context)
        cmd_args, display_cmd, mode = parse_command_input(
            chat_input, context_prompt=context_prompt,
            session_key=f"_chat_{page_name}_ai_active",
        )
        st.session_state[f"_chat_{page_name}_mode"] = mode

        import os
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"
        env.pop("CLAUDECODE", None)
        start_background_process(process_key, cmd_args, env)
        st.rerun()


