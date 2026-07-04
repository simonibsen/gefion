"""Regimes page — define, compute, and inspect market/sector/asset regimes (spec 005)."""

import json

import streamlit as st

from gefion.observability import create_span, set_attributes
from gefion.ui.components.chat import render_chat_widget


def get_page_context():
    """Return compact context dict for the Regimes page (for Ask Gefion)."""
    context = {"page_name": "Regimes",
               "summary": "Define and compute market/sector/asset regimes; slice results by them."}
    try:
        from gefion.ui.components.database import get_connection
        from gefion.regimes.definitions import list_definitions
        with create_span("ui.regimes.get_page_context"):
            with get_connection() as conn:
                defs = list_definitions(conn)
        context["data_stats"] = {"regimes": len(defs)}
        if not defs:
            context["suggestions"] = ["Define a regime: gefion regime define --name ... --scope market"]
    except Exception:
        pass
    return context


def _load_definitions():
    from gefion.ui.components.database import get_connection
    from gefion.regimes.definitions import list_definitions
    with get_connection() as conn:
        return list_definitions(conn)


def render_regimes():
    """Render the Regimes page."""
    with create_span("ui.regimes.render") as span:
        st.title(":material/insights: Regimes")
        st.caption(
            "Describe the state of the market/sector/asset and evaluate signals "
            "*conditionally* against it. A regime is causal, persistent, and testable."
        )

        try:
            defs = _load_definitions()
        except Exception as exc:  # pragma: no cover - defensive UI path
            st.error(f"Could not load regimes: {exc}")
            render_chat_widget(get_page_context())
            return

        set_attributes(span, regime_count=len(defs))

        tab_list, tab_new, tab_interaction = st.tabs(
            ["Defined regimes", "New regime", "Interaction test"])

        with tab_list:
            if not defs:
                st.info("No regimes defined yet. Use the **New regime** tab or the CLI: "
                        "`gefion regime define`.")
            else:
                st.dataframe(
                    [{"name": d.name, "scope": d.scope, "status": d.status, "origin": d.origin}
                     for d in defs],
                    width="stretch",
                )
                names = [d.name for d in defs]
                selected = st.selectbox("Inspect a regime", names)
                chosen = next((d for d in defs if d.name == selected), None)
                if chosen is not None:
                    st.subheader(f"{chosen.name} · {chosen.scope}")
                    st.json({
                        "expression": chosen.expression,
                        "bucketing": chosen.bucketing,
                        "persistence": chosen.persistence,
                        "descriptive_metadata": chosen.descriptive_metadata,
                    })
                    _render_labels_summary(chosen.name)

        with tab_new:
            _render_new_regime_form()

        with tab_interaction:
            _render_interaction_panel()

        render_chat_widget(get_page_context())


def _render_interaction_panel():
    """Continuous-interaction test: does a signal's edge scale with a conditioning variable?"""
    st.markdown("Test whether a signal's edge varies **continuously** with a conditioning "
                "variable (one interaction coefficient + p-value).")
    with st.form("interaction"):
        signal = st.text_input("Signal feature", placeholder="momentum")
        by = st.text_input("Conditioning variable", placeholder="realized_vol_20")
        horizon = st.number_input("Horizon (days)", min_value=1, value=7)
        submitted = st.form_submit_button("Run interaction test")
    if submitted:
        from gefion.ui.components.database import get_connection
        from gefion.regimes.interaction import (
            continuous_interaction, load_market_interaction_data)
        try:
            with get_connection() as conn:
                s, c, r = load_market_interaction_data(conn, signal, by, int(horizon))
            result = continuous_interaction(s, c, r)
            col1, col2, col3 = st.columns(3)
            col1.metric("Interaction coef", f"{result['interaction_coef']:.4f}")
            col2.metric("p-value", f"{result['interaction_pvalue']:.4f}")
            col3.metric("n", result["n"])
            if result["interaction_pvalue"] < 0.05:
                st.success("Significant interaction — the edge scales with the conditioning variable.")
            else:
                st.info("No significant gradient (edge does not vary with the conditioning variable).")
        except (LookupError, ValueError) as exc:
            st.error(f"Cannot run interaction test: {exc}")


def _render_labels_summary(name: str):
    """Show bucket frequencies / episodes for a computed regime."""
    from gefion.ui.components.database import get_connection
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rl.label, count(*) FROM regime_labels rl
                       JOIN regime_definitions rd ON rd.id = rl.regime_id
                       WHERE rd.name = %s GROUP BY rl.label ORDER BY rl.label""",
                    (name,),
                )
                freqs = {lab: cnt for lab, cnt in cur.fetchall()}
        if freqs:
            st.markdown("**Bucket coverage** (episodes/labels)")
            st.bar_chart(freqs)
        else:
            st.caption("Not computed yet — run `gefion regime compute " + name + "`.")
    except Exception:  # pragma: no cover - defensive
        st.caption("Label summary unavailable.")


def _render_new_regime_form():
    """A minimal define form; the full AST builder is a follow-up."""
    st.markdown("Define a regime by pasting its expression AST and bucketing JSON.")
    with st.form("new_regime"):
        name = st.text_input("Name (kebab-case)", placeholder="vol-regime")
        scope = st.selectbox("Scope", ["market", "sector", "industry", "asset"])
        expr_txt = st.text_area(
            "Expression (RegimeExpression AST, JSON)",
            value=json.dumps({"leaf": "comparison", "feature": "realized_vol_20",
                              "cmp": "quantile", "value": "tercile", "scope": "market"}, indent=2),
        )
        buckets_txt = st.text_area(
            "Bucketing (JSON)",
            value=json.dumps({"labels": ["calm", "normal", "stressed"], "method": "tercile"}),
        )
        submitted = st.form_submit_button("Define regime")
    if submitted:
        from gefion.ui.components.database import get_connection
        from gefion.regimes.definitions import RegimeDefinition, RegimeExpressionError, store_definition
        try:
            defn = RegimeDefinition(name=name, scope=scope,
                                    expression=json.loads(expr_txt),
                                    bucketing=json.loads(buckets_txt))
            with get_connection() as conn:
                store_definition(conn, defn)
            st.success(f"Defined regime '{name}'.")
            st.rerun()
        except (RegimeExpressionError, ValueError, json.JSONDecodeError) as exc:
            st.error(f"Invalid regime: {exc}")
