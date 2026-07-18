"""Candidates page — generated market-function review queue (spec 014).

Read-only by design: the queue and the full review packet render here, but
approve/reject are deliberate CLI/MCP acts — the page links the exact
commands rather than offering a one-click button on generated code.
"""

import streamlit as st

from gefion.observability import create_span, set_attributes


def get_page_context():
    """Return compact context dict for the Candidates page (for Ask Gefion)."""
    context = {"page_name": "Candidates",
               "summary": "Generated market-function candidates awaiting the owner gate."}
    try:
        from gefion.ui.components.database import get_connection
        from gefion.macro.candidates import list_candidates
        with create_span("ui.candidates.get_page_context"):
            with get_connection() as conn:
                pending = list_candidates(conn, state="pending")
        context["data_stats"] = {"pending_candidates": len(pending)}
        if pending:
            context["suggestions"] = [
                f"Review candidate #{pending[0]['id']}: "
                f"gefion macro candidate show --id {pending[0]['id']}"]
    except Exception:
        pass
    return context


def _load(state):
    from gefion.ui.components.database import get_connection
    from gefion.macro.candidates import list_candidates
    with get_connection() as conn:
        return list_candidates(conn, state=None if state == "all" else state)


def render_candidates():
    """Render the candidate queue + review packet."""
    with create_span("ui.candidates.render") as span:
        st.title(":material/gavel: Candidates")
        st.caption(
            "Machine-proposed market-level series behind the owner gate — "
            "the machine proposes, a human owns the gate. A candidate never "
            "computes a stored value until approved.")

        state = st.selectbox("Queue", ["pending", "approved", "rejected", "all"])
        try:
            rows = _load(state)
        except Exception as exc:
            st.error(f"Could not load candidates: {exc}")
            return
        set_attributes(span, state=state, n_candidates=len(rows))

        if not rows:
            st.info(f"No {state} candidates. Propose one with "
                    "`gefion macro propose --principle <id>`.")
            return

        for c in rows:
            dry = c.get("dry_run") or {}
            badge = "✅ dry-run OK" if dry.get("ok") else "❌ dry-run failed/missing"
            title = (f"#{c['id']} {c['name']} v{c['version']} [{c['kind']}] "
                     f"— {c['review_state']} — {badge}")
            with st.expander(title):
                st.markdown(
                    f"**Origin**: {c['origin']} · **Principle**: "
                    f"{c.get('principle_id') or '—'} · **Generator**: "
                    f"{c.get('generator') or '—'} · **Created**: {c['created_at']}")
                if c.get("description"):
                    st.markdown(c["description"])
                if c.get("inputs"):
                    st.json(c["inputs"])
                if dry:
                    ok = dry.get("ok")
                    st.markdown(f"**Dry-run** (seed {dry.get('seed')}): "
                                f"{'OK' if ok else 'FAILED — ' + str(dry.get('error'))}")
                    if dry.get("sample"):
                        st.table(dry["sample"])
                st.code(c["function_body"], language="python")
                if c["review_state"] == "pending":
                    st.markdown(
                        "Decide (deliberate act, CLI/MCP only):\n"
                        f"```\ngefion macro candidate approve --id {c['id']}\n"
                        f"gefion macro candidate reject --id {c['id']} "
                        "--reason '...'\n```")
                elif c["review_state"] == "rejected":
                    st.markdown(f"**Rejected**: {c.get('review_reason')} "
                                f"(by {c.get('reviewed_by') or '—'} at "
                                f"{c.get('reviewed_at')})")
                else:
                    st.markdown(f"**Approved** by {c.get('reviewed_by') or '—'} "
                                f"at {c.get('reviewed_at')} — function id "
                                f"{c.get('promoted_function_id')}")
