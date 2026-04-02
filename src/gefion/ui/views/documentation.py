"""Documentation page - Guides, tutorials, and whitepaper."""

import streamlit as st
from pathlib import Path
import re
from gefion.ui.components.chat import render_chat_widget
from typing import List, Tuple


# Document registry for search
DOCS = {
    "Whitepaper": "WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md",
    "User Guide": "USER_GUIDE.md",
    "ML Pipeline": "ML_QUICKSTART.md",
    "Strategies": "STRATEGIES.md",
    "Backtesting": "BACKTESTING.md",
    "Experiments": ".specify/specs/experiments-framework.md",
    "Troubleshooting": "TROUBLESHOOTING.md",
}

# Map document names to tab names for navigation
DOC_TO_TAB = {
    "Whitepaper": ":material/article: Whitepaper",
    "User Guide": ":material/rocket_launch: Quick Start",
    "ML Pipeline": ":material/model_training: ML Pipeline",
    "Strategies": ":material/compare_arrows: Strategies",
    "Backtesting": ":material/history: Backtesting",
    "Experiments": ":material/science: Experiments",
    "Troubleshooting": ":material/build: Troubleshooting",
}


@st.cache_data
def load_doc(filename: str) -> str:
    """Load markdown doc from docs/ directory or project root."""
    project_root = Path(__file__).parent.parent.parent.parent.parent
    docs_dir = project_root / "docs"
    # Check docs/ first, then project root (for .specify/ paths etc.)
    doc_path = docs_dir / filename
    if doc_path.exists():
        return doc_path.read_text()
    root_path = project_root / filename
    if root_path.exists():
        return root_path.read_text()
    return f"*Document not found: {filename}*"


def extract_sections(content: str) -> dict[str, str]:
    """Extract H2 sections from markdown content."""
    sections = {}
    current_section = "Introduction"
    current_content = []

    for line in content.split('\n'):
        if line.startswith('## '):
            # Save previous section
            if current_content:
                sections[current_section] = '\n'.join(current_content)
            current_section = line[3:].strip()
            current_content = []
        else:
            current_content.append(line)

    # Save last section
    if current_content:
        sections[current_section] = '\n'.join(current_content)

    return sections


@st.cache_data
def search_docs(query: str) -> List[Tuple[str, str, str, str, str, int]]:
    """
    Search all documents for query string.

    Returns list of (doc_name, section, matched_line, context, filename, score) tuples,
    ranked by relevance score (highest first).
    """
    if not query or len(query) < 2:
        return []

    results = []
    query_lower = query.lower()

    for doc_name, filename in DOCS.items():
        content = load_doc(filename)
        if content.startswith("*Document not found"):
            continue

        sections = extract_sections(content)

        for section_name, section_content in sections.items():
            # Calculate relevance score for this section
            section_lower = section_content.lower()
            match_count = section_lower.count(query_lower)

            if match_count == 0:
                continue

            # Score factors:
            # - Match in section title: +50
            # - Number of matches: +10 each
            # - Exact case match: +20
            # - Match in first 200 chars: +30
            score = match_count * 10

            if query_lower in section_name.lower():
                score += 50

            if query in section_content:  # Exact case match
                score += 20

            if query_lower in section_lower[:200]:
                score += 30

            # Find best matching line for display
            lines = section_content.split('\n')
            best_line = ""
            best_context = ""

            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    context = '\n'.join(lines[start:end])

                    # Highlight the match
                    highlighted = re.sub(
                        f'({re.escape(query)})',
                        r'**\\1**',
                        context,
                        flags=re.IGNORECASE
                    )

                    best_line = line.strip()[:100]
                    best_context = highlighted
                    break

            results.append((doc_name, section_name, best_line, best_context, filename, score))

    # Sort by score (highest first), then by doc name
    results.sort(key=lambda x: (-x[5], x[0]))

    return results[:50]  # Limit total results




def render_docs():
    """Render the documentation page."""
    st.markdown("# :material/menu_book: Documentation")
    render_chat_widget({"page_name": "Documentation"})
    st.markdown("Guides and theory for Gefion quantitative trading platform.")

    # Search box
    search_query = st.text_input(
        "Search documentation",
        placeholder="Type to search across all docs...",
        key="doc_search"
    )

    # Show search results if query entered
    if search_query and len(search_query) >= 2:
        results = search_docs(search_query)

        if results:
            st.markdown(f"### Found {len(results)} result(s) for '{search_query}'")

            # Show ranked results with full section content
            for idx, (doc_name, section, matched_line, context, filename, score) in enumerate(results):
                # Load full section content
                doc_content = load_doc(filename)
                doc_sections = extract_sections(doc_content)
                full_section_content = doc_sections.get(section, context)

                # Show full section in expander (ranked by relevance)
                tab_name = DOC_TO_TAB.get(doc_name, doc_name)
                preview = matched_line[:50] + "..." if len(matched_line) > 50 else matched_line
                with st.expander(f"**{section}** — {preview}"):
                    st.caption(f"{tab_name}")
                    st.markdown(full_section_content)

            st.markdown("---")
        else:
            st.info(f"No results found for '{search_query}'")
            st.markdown("---")

    # Tabs for document navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        ":material/article: Whitepaper",
        ":material/rocket_launch: Quick Start",
        ":material/model_training: ML Pipeline",
        ":material/compare_arrows: Strategies",
        ":material/history: Backtesting",
        ":material/science: Experiments",
        ":material/build: Troubleshooting",
    ])

    with tab1:
        render_whitepaper()

    with tab2:
        render_quickstart()

    with tab3:
        render_ml_docs()

    with tab4:
        render_strategies_docs()

    with tab5:
        render_backtest_docs()

    with tab6:
        render_experiments_docs()

    with tab7:
        render_troubleshooting()


def render_whitepaper():
    """Render the whitepaper with collapsible sections."""
    st.subheader("From Patterns to Probabilities")
    st.markdown("""
    A Modern Approach to Technical Analysis Through Machine Learning.

    This whitepaper explores the evolution of technical analysis from rule-based
    pattern recognition to probabilistic machine learning models.
    """)

    content = load_doc("WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Key sections to show
    section_order = [
        "Introduction",
        "The Theory of Technical Analysis",
        "Machine Learning: From Rules to Probabilities",
        "Bridging Technical Analysis and ML",
        "The Gefion Platform: A Practical Implementation",
        "Case Study: Quantile Regression for Risk-Aware Prediction",
        "Multi-Model Integration",
        "Conclusion",
        "References",
        "Appendix: Getting Started with Gefion",
    ]

    # Show table of contents
    st.markdown("### Contents")
    toc_items = [s for s in section_order if s in sections]
    for i, section in enumerate(toc_items, 1):
        st.markdown(f"{i}. {section}")

    st.markdown("---")

    # Render each section in an expander
    for section in section_order:
        if section in sections:
            with st.expander(f"**{section}**", expanded=(section == "Introduction")):
                st.markdown(sections[section])


def render_quickstart():
    """Render the quick start / user guide."""
    st.subheader("Getting Started with Gefion")

    content = load_doc("USER_GUIDE.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    # Show setup section first
    st.markdown("""
    ### Prerequisites

    1. **Database**: TimescaleDB running via Docker
    2. **API Key**: AlphaVantage API key in `.env`
    3. **Python**: Virtual environment with Gefion installed

    ```bash
    # Start database
    docker compose up -d postgres

    # Activate environment
    source .venv/bin/activate

    # Verify installation
    Gefion --help
    ```
    """)

    st.markdown("---")

    # Extract and show sections
    sections = extract_sections(content)

    # Key sections for quick start
    key_sections = [
        "Setup",
        "CLI Commands",
        "ML overview (conceptual)",
        "ML Workflow (End-to-End)",
        "Tips and Behaviors",
    ]

    for section in key_sections:
        if section in sections:
            with st.expander(f"**{section}**"):
                st.markdown(sections[section])

    # Show full guide option
    with st.expander("View Full User Guide"):
        st.markdown(content)


def render_ml_docs():
    """Render the ML quickstart guide."""
    st.subheader("Machine Learning Pipeline")

    st.info("""
    Gefion predicts **return distributions** (q10/q50/q90) instead of single values.
    This enables risk-aware position sizing and portfolio construction.
    """)

    content = load_doc("ML_QUICKSTART.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Key ML sections
    key_sections = [
        "What is Gefion's ML Pipeline?",
        "Prerequisites",
        "Quick Start (5 Minutes)",
        "Production Workflow",
        "Understanding the Output",
        "Algorithm Comparison",
        "Model Ensembles",
        "Trend Classification",
        "Troubleshooting",
    ]

    for section in key_sections:
        if section in sections:
            expanded = section == "Quick Start (5 Minutes)"
            with st.expander(f"**{section}**", expanded=expanded):
                st.markdown(sections[section])

    # Quick reference
    st.markdown("---")
    st.markdown("### Quick Reference")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        **Build Dataset**
        ```bash
        Gefion ml dataset-build \\
          --name demo --version v1 \\
          --exchange NASDAQ --limit 50 \\
          --horizons 7,30 --export
        ```
        """)

    with col2:
        st.markdown("""
        **Train Model**
        ```bash
        Gefion ml train \\
          --dataset-name demo \\
          --dataset-version v1 \\
          --model-name test \\
          --model-version $(date +%Y%m%d)
        ```
        """)


def render_strategies_docs():
    """Render the trading strategies guide."""
    st.subheader("Trading Strategies")

    st.info("""
    Gefion provides 9 built-in trading strategies: 7 rule-based and 2 ML-integrated.
    Strategies are Python classes; configs are parameterized instances for comparison.
    """)

    content = load_doc("STRATEGIES.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Show sections in logical order with appropriate expansion
    section_config = [
        ("Architecture Overview", True),   # Expanded - important context
        ("Built-in Strategies", True),     # Expanded - main content
        ("Working with Strategy Configs", False),
        ("Creating New Strategies", False),
        ("Best Practices", False),
        ("Strategy Comparison", False),
    ]

    for section_name, expanded in section_config:
        if section_name in sections:
            with st.expander(f"**{section_name}**", expanded=expanded):
                st.markdown(sections[section_name])


def render_backtest_docs():
    """Render the backtesting guide."""
    st.subheader("Strategy Backtesting")

    st.info("""
    Test trading strategies on historical data with point-in-time correctness
    (no look-ahead bias).
    """)

    content = load_doc("BACKTESTING.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Key backtest sections
    key_sections = [
        "Overview",
        "Quick Start",
        "CLI Parameters",
        "Performance Metrics",
        "Built-in Strategies",
        "ML Signal Strategy",
        "Data Requirements",
        "Programmatic Usage",
        "Best Practices",
        "Troubleshooting",
    ]

    for section in key_sections:
        if section in sections:
            expanded = section == "Quick Start" or section == "ML Signal Strategy"
            with st.expander(f"**{section}**", expanded=expanded):
                st.markdown(sections[section])

    # Strategy summary
    st.markdown("---")
    st.markdown("### Available Strategies")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Rule-Based")
        strategies = {
            "momentum": "Buy top performers over lookback period",
            "mean_reversion": "Buy oversold stocks (RSI-based)",
            "ma_crossover": "Follow moving average crossovers",
            "breakout": "Buy on price breakouts with volume",
        }
        for name, desc in strategies.items():
            st.markdown(f"- **{name}**: {desc}")

    with col2:
        st.markdown("#### ML-Integrated")
        ml_strategies = {
            "ml_signal": "Trade based on quantile/classifier predictions",
            "ml_filter": "Filter rule-based signals through ML",
        }
        for name, desc in ml_strategies.items():
            st.markdown(f"- **{name}**: {desc}")

        st.caption("Uses D-1 predictions to avoid look-ahead bias")


def render_experiments_docs():
    """Render the experiments documentation."""
    st.subheader("AI Experimentation Framework")

    st.info("""
    The Experiments page automates what you do manually across Features, ML Pipeline,
    and Backtesting. An autonomous agent explores parameter combinations, feature sets,
    model architectures, and trading strategies — then uses statistical testing to keep
    only genuine improvements.
    """)

    # How Experiments relates to other pages
    with st.expander("**How Experiments Relates to Other Pages**", expanded=True):
        st.markdown("""
        | Manual Page | What Experiments Automates |
        |---|---|
        | **Features** | Feature engineering creates and evaluates new features. Feature selection finds optimal subsets. |
        | **ML Pipeline** | Hyperparameter tuning optimizes model settings. Model comparison tries different algorithms. Label engineering tests different prediction targets. |
        | **Backtesting** | Strategy param optimization finds the best trading strategy parameters. |

        **The difference:**
        - **Manual pages** — you make one choice at a time, run it, inspect results, decide what to try next
        - **Experiments** — the agent explores systematically, tries many combinations, and uses FDR statistical testing to filter out false discoveries

        **When to use each:**
        - Use manual pages for **initial setup** (loading data, training a first model), **inspecting results** (charts, backtests), and **understanding** what the agent did
        - Use Experiments for **exploration** (finding better parameters, features, models) and **automation** (autonomous cycles)
        """)

    # Experiment types explained
    with st.expander("**Experiment Types**"):
        st.markdown("""
        | Type | What It Does | Objective | Example |
        |---|---|---|---|
        | **Strategy Params** | Optimizes trading strategy parameters via backtesting | Sharpe ratio | Find best momentum lookback period |
        | **Hyperparameter** | Tunes ML model settings with cross-validation | Quantile loss | Optimize learning rate, tree depth |
        | **Model Comparison** | Compares algorithms on identical data splits | Quantile loss | LightGBM vs XGBoost vs Linear |
        | **Feature Engineering** | Creates and tests new computed features | Quantile loss | Rolling z-score with different windows |
        | **Feature Selection** | Finds the best subset of features | Quantile loss | RSI + EMA vs Bollinger + MACD |
        | **Label Engineering** | Tests different prediction targets | Quantile loss | Raw returns vs log returns vs winsorized |
        | **Pipeline** | Chains feature + label + model stages | Quantile loss | End-to-end pipeline optimization |
        """)

    # Understanding results
    with st.expander("**Interpreting Results**"):
        st.markdown("""
        ### Key Metrics

        **For ML experiments (quantile models):**
        - **Quantile Loss** (lower is better) — how accurately the model predicts price ranges. Below 0.03 is good.
        - **Q50 Calibration** (target: 50%) — how often the actual return is below the median prediction. Closer to 50% = better calibrated.
        - **Q10/Q90 Calibration** (target: 10%/90%) — tail calibration. If Q10 calibration is 12% instead of 10%, the model slightly underestimates downside risk.
        - **Avg IQR** — average width of prediction intervals (Q90 - Q10). Narrower = more confident, but may sacrifice calibration.

        **For strategy experiments (backtesting):**
        - **Sharpe Ratio** (higher is better) — risk-adjusted return. Above 1.0 is decent, above 2.0 is strong.
        - **Total Return** — raw gain/loss percentage.
        - **Max Drawdown** (lower is better) — worst peak-to-trough loss. Above -20% is concerning.
        - **Win Rate** — percentage of profitable trades. Context-dependent (trend-following can be profitable at 40%).

        ### What "Best Score" Means
        The best score is the best value of the objective metric across all trials. For `quantile_loss` (minimize),
        lower is better. For `sharpe_ratio` (maximize), higher is better.

        ### FDR Survivors
        In an autonomous cycle, experiments compete against each other. FDR (False Discovery Rate) correction
        filters out experiments whose results could be due to chance. If 10 experiments run and 3 survive FDR,
        those 3 are statistically likely to be genuine improvements, not noise.
        """)

    # Autonomous cycles
    with st.expander("**Autonomous Cycles**"):
        st.markdown("""
        ### How an Autonomous Cycle Works

        1. **Discovery** — scans your principles catalog (62 principles from quantitative finance literature)
           against available data to find testable hypotheses
        2. **Theme Filtering** — only hypotheses from your selected research themes are used
        3. **Proposal** — creates experiments with appropriate search spaces and guardrails
        4. **Auto-Approval** — experiments are approved automatically (the guardrails are the safety layer)
        5. **Parallel Execution** — experiments run concurrently with resource checks before each
        6. **FDR Evaluation** — results are statistically tested; only genuine discoveries survive

        ### Guardrails
        You control what the agent explores:
        - **Research Themes** — which areas of quantitative finance to explore
        - **Allowed Algorithms** — which ML algorithms the agent can use
        - **Prediction Horizon** — how far ahead to predict
        - **Quantiles, CV Folds, Embargo** — each can be agent-decided or locked
        - **Max Experiments, Max Trials** — resource bounds
        - **FDR Rate** — statistical strictness

        ### Config Files
        Save and load cycle configs as JSON. This lets you create reusable experiment profiles
        (e.g., "conservative exploration", "wide-open search") and share them.
        """)

    # Spec doc if available
    content = load_doc(".specify/specs/experiments-framework.md")
    if not content.startswith("*Document not found"):
        sections = extract_sections(content)
        for section in ["CLI Commands", "Search Space Format", "Database Schema", "Python API"]:
            if section in sections:
                with st.expander(f"**{section}**"):
                    st.markdown(sections[section])

    # Quick reference
    st.markdown("---")
    st.markdown("### Quick Reference")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        **Autonomous Cycle (recommended)**
        ```bash
        # Create cycle from config
        gefion experiment cycle-start \\
          --config cycle_config.json

        # Run it
        gefion experiment cycle-run <cycle_id>
        ```

        **Manual Experiment**
        ```bash
        gefion experiment propose \\
          --name "tune-lgbm" \\
          --type hyperparameter \\
          --model-type lightgbm \\
          --dataset-uri datasets/baseline_v2/manifest.json \\
          --search-space '{"learning_rate": \\
            {"type":"float","low":0.01,"high":0.3,"log":true}}' \\
          --objective quantile_loss \\
          --objective-direction minimize \\
          --search-method bayesian
        ```
        """)

    with col2:
        st.markdown("""
        **Approve & Run**
        ```bash
        gefion experiment approve --id 1
        gefion experiment run --id 1
        gefion experiment results --id 1 --trials
        ```

        **Cycle Management**
        ```bash
        gefion experiment cycle-list
        gefion experiment cycle-status <id>
        ```

        **Discovery**
        ```bash
        gefion experiment discover --json
        ```
        """)

    st.markdown("### Search Strategies")
    strategies = {
        "bayesian": "Adaptive optimization (Optuna TPE) — most efficient, learns from results",
        "random": "Random sampling — quick exploration, good for high-dimensional spaces",
        "grid": "Exhaustive enumeration — tests all combinations, best for small spaces",
    }
    for name, desc in strategies.items():
        st.markdown(f"- **{name}**: {desc}")


def render_troubleshooting():
    """Render troubleshooting guide."""
    st.subheader("Troubleshooting")

    content = load_doc("TROUBLESHOOTING.md")

    if content.startswith("*Document not found"):
        # Show common issues inline if doc not found
        st.markdown("""
        ### Common Issues

        **Database Connection Failed**
        ```bash
        # Check if PostgreSQL is running
        docker compose ps

        # Restart if needed
        docker compose restart postgres
        ```

        **No Data Found**
        ```bash
        # Update data for your exchange
        Gefion data-update --exchange NASDAQ --limit 50
        ```

        **ML Model Not Found**
        ```bash
        # List available models
        Gefion ml model-list

        # Check model artifacts exist
        ls -la models/
        ```

        **Feature Computation Errors**
        ```bash
        # Check feature definitions
        Gefion feat-def-list

        # Recompute features
        Gefion feat-compute --exchange NASDAQ --local --refresh-existing
        ```
        """)
        return

    sections = extract_sections(content)

    for section, section_content in sections.items():
        with st.expander(f"**{section}**"):
            st.markdown(section_content)

    # Additional help
    st.markdown("---")
    st.markdown("### Getting Help")
    st.markdown("""
    - **System Status**: Check the sidebar for quick status
    - **CLI Help**: Run `gefion --help` or `gefion <command> --help`
    - **Logs**: Check Docker logs with `docker compose logs postgres`
    - **AI Assistant**: Use the AI Assistant page for example queries
    """)
