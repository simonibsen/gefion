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

        tab_list, tab_new, tab_interaction, tab_discovery = st.tabs(
            ["Defined regimes", "New regime", "Interaction test", "Discovery"])

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
                    _render_regime_chart(chosen.name)

        with tab_new:
            _render_new_regime_form()

        with tab_interaction:
            _render_interaction_panel()

        with tab_discovery:
            _render_discovery_tab()

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


def _render_regime_chart(name: str):
    """Chart a symbol's price with this regime's episode bands (mirrors `gefion chart regime`)."""
    import streamlit.components.v1 as components

    st.markdown("**Chart** — price with regime-episode bands")
    with st.form(f"regime_chart_{name}"):
        symbol = st.text_input("Symbol to overlay", value="SPY")
        submitted = st.form_submit_button("Chart regime")
    if submitted:
        from gefion.ui.components.database import get_connection
        from gefion.charts.queries import fetch_regime_chart_data
        from gefion.charts.d3.renderers import create_regime_chart
        try:
            with create_span("ui.regimes.chart", regime=name, symbol=symbol):
                with get_connection() as conn:
                    payload = fetch_regime_chart_data(conn, name, symbol.strip().upper())
                html = create_regime_chart(payload["price"], payload["episodes"],
                                           regime_name=name, symbol=symbol.strip().upper())
                components.html(html, height=650, scrolling=False)
        except LookupError as exc:
            st.error(f"Cannot chart regime: {exc}")


def _render_discovery_tab():
    """Agentic regime discovery (spec 006): runs table, run detail, new-run form.

    Honesty rules (contracts/ui.md): refusals and invalid runs are shown with
    reasons, never hidden; an unadmitted candidate is never styled as a finding.
    """
    from gefion.ui.components.database import get_connection
    from gefion.regimes.discovery import ledger

    st.markdown(
        "The agent proposes candidate regimes from a **pre-registered, bounded** "
        "search space and tests conditional edges on an outer holdout discovery "
        "never sees. Every candidate — including the losers — is ledgered. "
        "**Expect mostly rejections**; a loop that admits often is broken."
    )
    try:
        with create_span("ui.regimes.discovery_tab"):
            with get_connection() as conn:
                runs = ledger.list_runs(conn)
    except Exception as exc:  # pragma: no cover - defensive UI path
        st.error(f"Could not load discovery runs: {exc}")
        return

    if not runs:
        st.info("No discovery runs yet. Start one below or via "
                "`gefion regime discover start`.")
    else:
        st.dataframe(
            [{"id": r["id"], "name": r["name"], "status": r["status"],
              "family size": r["family_size"], "dataset": r["dataset_version"],
              "created": str(r["created_at"])[:19]} for r in runs],
            width="stretch",
        )
        options = [f"{r['id']} · {r['name']}" for r in runs]
        chosen = st.selectbox("Inspect a run", options)
        run = runs[options.index(chosen)]
        _render_discovery_run_detail(run)

    with st.expander("New discovery run"):
        _render_discovery_start()


def _render_discovery_run_detail(run: dict):
    """Pre-registration (immutable), segregation boundaries, ledger, verdicts."""
    if run["status"] == "invalid":
        st.error("Run invalid — segregation unproven or execution aborted; "
                 "no verdicts were produced (fail-closed).")
    col1, col2, col3 = st.columns(3)
    col1.metric("Status", run["status"])
    col2.metric("Family size", run["family_size"] if run["family_size"] is not None else "—")
    col3.metric("Seed", run["seed"])
    st.markdown("**Pre-registration** (search_space — declared before evaluation)")
    st.json(run["search_space"])
    st.markdown("**Segregation** (discovery never saw the holdout)")
    st.json(run["segregation"])
    _render_discovery_verdicts(run)
    _render_discovery_ledger(run)


def _render_discovery_verdicts(run: dict):
    """Admitted edges highlighted — never without the family size beside them."""
    from gefion.ui.components.database import get_connection
    from gefion.regimes.discovery import ledger
    try:
        with get_connection() as conn:
            cands = ledger.list_candidates(conn, run["id"])
    except Exception:  # pragma: no cover - defensive
        st.caption("Verdicts unavailable.")
        return
    admitted = [c for c in cands if c["verdict"] == "admitted"]
    st.markdown(
        f"**Verdicts** — {len(admitted)} admitted of {len(cands)} candidates "
        f"(FDR family size **{run['family_size']}** — every test counted, losers included)")
    if not admitted:
        st.caption("No survivors. Most honest runs admit nothing — that is the loop "
                   "working, not failing.")
        return
    for c in admitted:
        surviving = [t for t in (c["results"] or {}).get("tests", []) if t.get("survived")]
        st.success(f"Admitted `{c['candidate_hash'][:24]}` — surviving tests: "
                   + ", ".join(f"{t['signal']}"
                               + (f"×{t['bucket']}" if t.get("bucket") else "")
                               + f" (p={t['pvalue']:.2e})" for t in surviving))


def _render_discovery_ledger(run: dict):
    """The full candidate ledger, filterable by verdict; losers visible."""
    from gefion.ui.components.database import get_connection
    from gefion.regimes.discovery import ledger
    st.markdown("**Candidate ledger** (the losers are part of the story)")
    verdict = st.selectbox(
        "Filter by verdict",
        ["all", "admitted", "rejected", "refused_low_power",
         "refused_degenerate", "refused_unstable"],
        key=f"ledger_verdict_{run['id']}")
    try:
        with get_connection() as conn:
            cands = ledger.list_candidates(
                conn, run["id"], verdict=None if verdict == "all" else verdict)
    except Exception:  # pragma: no cover - defensive
        st.caption("Ledger unavailable.")
        return
    if not cands:
        st.caption("No candidates under this filter.")
        return
    st.dataframe(
        [{"id": c["id"], "tier": c["tier"], "hash": c["candidate_hash"][:24],
          "verdict": c["verdict"], "counted in family": c["counted_in_family"],
          "tests": len((c["results"] or {}).get("tests", []))} for c in cands],
        width="stretch",
    )


def _render_discovery_start():
    """New-run form: atoms + caps + the three declared seams."""
    with st.form("discovery_start"):
        name = st.text_input("Run name (kebab-case)", placeholder="first-hunt")
        atoms_txt = st.text_area(
            "Atom library (JSON)",
            value=json.dumps({"atoms": [
                {"feature": "realized_vol_20", "form": "tercile"}]}, indent=2),
            help="The pre-registered primitive library the search may compose.",
        )
        col1, col2, col3 = st.columns(3)
        depth = col1.number_input("Depth K", min_value=1, max_value=2, value=1)
        budget = col2.number_input("Budget", min_value=1, value=50)
        seed = col3.number_input("Seed", min_value=0, value=42)
        tiers = st.multiselect("Tiers", ["interaction", "grammar", "expressive"],
                               default=["interaction"])
        # the three pluggable seams — declared, never hidden defaults
        signal_source = st.selectbox("signal_source", ["features"])
        grading_scheme = st.selectbox("grading_scheme", ["walk_forward"])
        universe_filter = st.selectbox(
            "universe_filter", ["test_tickers,asset_type:common", "passthrough"],
            help="'passthrough' is a deliberate, recorded choice — never a silent fallback.")
        fresh_holdout = st.text_input(
            "Fresh-holdout reserve (START:END)", value="",
            help="Required when the expressive tier is enabled.")
        submitted = st.form_submit_button("Pre-register & run")
    if submitted:
        import subprocess
        import sys
        import tempfile
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write(atoms_txt)
                atoms_path = fh.name
            cmd = [sys.executable, "-m", "gefion.cli", "regime", "discover", "start",
                   "--name", name, "--atoms", atoms_path,
                   "--depth", str(int(depth)), "--budget", str(int(budget)),
                   "--seed", str(int(seed)),
                   "--signal-source", signal_source,
                   "--grading-scheme", grading_scheme,
                   "--universe-filter", universe_filter, "--json"]
            for tier in tiers:
                cmd.extend(["--tier", tier])
            if fresh_holdout.strip():
                cmd.extend(["--fresh-holdout", fresh_holdout.strip()])
            with st.spinner("Running discovery (pre-register → enumerate → freeze → evaluate)…"):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if proc.returncode == 0:
                st.success("Run complete — see the runs table above (rerun to refresh).")
                st.code(proc.stdout[-2000:] or "(no output)")
            else:
                st.error("Discovery refused or failed (fail-closed):")
                st.code((proc.stdout + proc.stderr)[-2000:])
        except Exception as exc:
            st.error(f"Could not start discovery: {exc}")


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
