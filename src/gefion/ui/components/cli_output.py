"""Unified CLI process output rendering (issue #88, Constitution V).

One component renders any CLI process the UI launches: running/complete/error
state, structured display for `--json` output (phase events with progress
hints, a final summary), plain-text fallback, and the process-state metric
rows when the launcher populated them. The parser is pure so it is testable
without Streamlit — and a new CLI command gets proper UI rendering for free.
"""
import json
from typing import Any, Dict, List, Optional

import streamlit as st

from gefion.observability import create_span  # noqa: F401  (UI spans opt-in)

# JSON lines with these keys are progress EVENTS; a dict that looks like a
# result (no phase, or an explicit terminal phase) is the FINAL summary.
_EVENT_KEYS = ("phase", "step")
_TERMINAL_PHASES = ("complete", "done", "finished")


def parse_output_lines(lines: List[str]) -> Dict[str, Any]:
    """Split raw output lines into progress events, a final summary dict,
    plain-text lines, and an optional progress fraction (from step/total
    display hints). Pure — no Streamlit, no session state."""
    events: List[Dict[str, Any]] = []
    final: Optional[Dict[str, Any]] = None
    plain: List[str] = []
    progress: Optional[float] = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            plain.append(line)
            continue
        if not isinstance(data, dict):
            plain.append(line)
            continue
        phase = data.get("phase")
        if phase in _TERMINAL_PHASES or "phase" not in data:
            final = data
        else:
            events.append(data)
        step, total = data.get("step"), data.get("total_steps")
        if isinstance(step, (int, float)) and isinstance(total, (int, float)) \
                and total:
            progress = min(1.0, float(step) / float(total))
    return {"events": events, "final": final, "plain": plain,
            "progress": progress}


def _metric_rows(state) -> None:
    """The launcher-populated process metrics (progress, rate, ETA, workers),
    shown only when present — preserves the data-update dashboard."""
    if getattr(state, "total", 0):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Progress", f"{state.done}/{state.total}",
                    f"{state.progress:.0f}%")
        col2.metric("Inserted", f"{getattr(state, 'inserted', 0):,}")
        col3.metric("Errors", str(getattr(state, "errors", 0)))
        eta = getattr(state, "eta_seconds", 0.0)
        if eta > 0:
            eta_str = (f"{eta:.0f}s" if eta < 60
                       else f"{eta / 60:.1f}m" if eta < 3600
                       else f"{eta / 3600:.1f}h")
            col4.metric("ETA", eta_str)
        elif getattr(state, "workers", None):
            col4.metric("Workers", str(state.workers))
        rate = getattr(state, "rate_per_sec", 0.0)
        if rate > 0:
            st.caption(f"Rate: {rate:.1f}/s"
                       + (f" · mode {state.mode}" if getattr(state, "mode", None)
                          else ""))
    if getattr(state, "progress", 0) > 0 and not getattr(state, "total", 0):
        st.progress(min(1.0, state.progress / 100.0))


def _structured(parsed: Dict[str, Any], is_running: bool) -> None:
    """Structured display for --json output: event list with progress, then
    the final summary (scalars as metrics, lists as tables, rest as JSON)."""
    if parsed["progress"] is not None and is_running:
        st.progress(parsed["progress"])
    for evt in parsed["events"]:
        phase = evt.get("phase", "")
        detail = {k: v for k, v in evt.items()
                  if k not in ("phase", "step", "total_steps")}
        bits = ", ".join(f"{k}={v:,}" if isinstance(v, int) else f"{k}={v}"
                         for k, v in detail.items())
        st.markdown(f"- **{phase}**: {bits}" if bits else f"- **{phase}**")
    final = parsed["final"]
    if final:
        scalars = {k: v for k, v in final.items()
                   if isinstance(v, (int, float, str, bool))}
        if scalars and len(scalars) <= 8:
            cols = st.columns(min(4, len(scalars)))
            for i, (k, v) in enumerate(scalars.items()):
                cols[i % len(cols)].metric(k, f"{v:,}" if isinstance(v, int)
                                           else str(v))
        tables = {k: v for k, v in final.items()
                  if isinstance(v, list) and v and isinstance(v[0], dict)}
        for k, rows in tables.items():
            st.caption(k)
            st.dataframe(rows, use_container_width=True)
        rest = {k: v for k, v in final.items()
                if k not in scalars and k not in tables}
        if rest:
            st.json(rest)
    if parsed["plain"]:
        st.code("\n".join(parsed["plain"][-50:]), language="text")
    if not parsed["events"] and not parsed["final"] and not parsed["plain"] \
            and not is_running:
        st.caption("No output captured.")


# Public alias: views that manage their own status shell (e.g. the assistant's
# chat container) can still delegate the structured-output body.
def render_structured(parsed, is_running):
    _structured(parsed, is_running)


def render_cli_state(state, title: str, *, expanded: bool = True) -> bool:
    """Render one CLI process from its ProcessState. Returns False when the
    process never ran (nothing rendered)."""
    if not state.is_running and not getattr(state, "completed", False):
        return False
    if state.is_running:
        label, st_state = f"Running: {title}", "running"
    elif getattr(state, "success", False):
        label, st_state = f"Completed: {title}", "complete"
    else:
        label, st_state = f"Failed: {title}", "error"
    with st.status(label, expanded=expanded, state=st_state):
        phase = getattr(state, "phase", None)
        if phase:
            st.write(f"▸ Phase: **{str(phase).title()}**")
        msg = getattr(state, "status_message", None)
        if msg:
            st.caption(msg)
        _metric_rows(state)
        parsed = parse_output_lines(getattr(state, "output_lines", []) or [])
        _structured(parsed, state.is_running)
        last_ok = getattr(state, "last_ok", None)
        if last_ok:
            st.caption(f"Last processed: **{last_ok}**")
        if getattr(state, "error_message", None):
            st.error(state.error_message)
    return True


def render_cli_output(key: str, title: str, *, expanded: bool = True) -> bool:
    """Key-based door: look up the process state and render it."""
    from gefion.ui.views.data import get_process_state
    return render_cli_state(get_process_state(key), title, expanded=expanded)
