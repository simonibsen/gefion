"""Documentation page - Guides, tutorials, and whitepaper."""

import streamlit as st
from pathlib import Path
import re
from typing import List, Tuple


# Document registry for search
DOCS = {
    "Whitepaper": "WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md",
    "User Guide": "USER_GUIDE.md",
    "ML Pipeline": "ML_QUICKSTART.md",
    "Strategies": "STRATEGIES.md",
    "Backtesting": "BACKTESTING.md",
    "Experiments": "EXPERIMENTS.md",
    "Troubleshooting": "TROUBLESHOOTING.md",
}


@st.cache_data
def load_doc(filename: str) -> str:
    """Load markdown doc from docs/ directory."""
    # Navigate from views/ up to src/g2/, then to project root, then docs/
    docs_dir = Path(__file__).parent.parent.parent.parent.parent / "docs"
    doc_path = docs_dir / filename
    if doc_path.exists():
        return doc_path.read_text()
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
def search_docs(query: str) -> List[Tuple[str, str, str, str]]:
    """
    Search all documents for query string.

    Returns list of (doc_name, section, matched_line, context) tuples.
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
            lines = section_content.split('\n')
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    # Get context (surrounding lines)
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    context = '\n'.join(lines[start:end])

                    # Highlight the match
                    highlighted = re.sub(
                        f'({re.escape(query)})',
                        r'**\1**',
                        context,
                        flags=re.IGNORECASE
                    )

                    results.append((doc_name, section_name, line.strip()[:100], highlighted))

                    # Limit results per section
                    if len([r for r in results if r[0] == doc_name and r[1] == section_name]) >= 3:
                        break

    return results[:50]  # Limit total results


def render_docs():
    """Render the documentation page."""
    st.title("📚 Documentation")
    st.markdown("Guides and theory for g2 quantitative trading platform.")

    # Search box
    search_query = st.text_input(
        "🔍 Search documentation",
        placeholder="Type to search across all docs...",
        key="doc_search"
    )

    # Show search results if query entered
    if search_query and len(search_query) >= 2:
        results = search_docs(search_query)

        if results:
            st.markdown(f"### Found {len(results)} result(s) for '{search_query}'")

            # Group by document
            current_doc = None
            for doc_name, section, matched_line, context in results:
                if doc_name != current_doc:
                    current_doc = doc_name
                    st.markdown(f"#### 📄 {doc_name}")

                with st.expander(f"**{section}**: {matched_line[:60]}..."):
                    st.markdown(context)

            st.markdown("---")
        else:
            st.info(f"No results found for '{search_query}'")
            st.markdown("---")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📜 Whitepaper",
        "🚀 Quick Start",
        "🧠 ML Pipeline",
        "⚔️ Strategies",
        "📈 Backtesting",
        "🧪 Experiments",
        "🔧 Troubleshooting",
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
        "The g2 Platform: A Practical Implementation",
        "Case Study: Quantile Regression for Risk-Aware Prediction",
        "Multi-Model Integration",
        "Conclusion",
        "References",
        "Appendix: Getting Started with g2",
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
    st.subheader("Getting Started with g2")

    content = load_doc("USER_GUIDE.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    # Show setup section first
    st.markdown("""
    ### Prerequisites

    1. **Database**: TimescaleDB running via Docker
    2. **API Key**: AlphaVantage API key in `.env`
    3. **Python**: Virtual environment with g2 installed

    ```bash
    # Start database
    docker compose up -d postgres

    # Activate environment
    source .venv/bin/activate

    # Verify installation
    g2 --help
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
    g2 predicts **return distributions** (q10/q50/q90) instead of single values.
    This enables risk-aware position sizing and portfolio construction.
    """)

    content = load_doc("ML_QUICKSTART.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Key ML sections
    key_sections = [
        "What is g2's ML Pipeline?",
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
        g2 ml dataset-build \\
          --name demo --version v1 \\
          --exchange NASDAQ --limit 50 \\
          --horizons 7,30 --export
        ```
        """)

    with col2:
        st.markdown("""
        **Train Model**
        ```bash
        g2 ml train \\
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
    g2 provides 9 built-in trading strategies: 7 rule-based and 2 ML-integrated.
    Strategies are Python classes; configs are parameterized instances for comparison.
    """)

    content = load_doc("STRATEGIES.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Architecture overview
    if "Architecture Overview" in sections:
        with st.expander("**Architecture Overview**", expanded=True):
            st.markdown(sections["Architecture Overview"])

    # Rule-based strategies
    st.markdown("---")
    st.markdown("### Rule-Based Strategies")

    rule_strategies = [
        "Momentum",
        "Mean Reversion",
        "Moving Average Crossover",
        "Breakout",
        "Pairs Trading",
        "RSI Divergence",
        "Volatility Contraction",
    ]

    for strat in rule_strategies:
        if strat in sections:
            with st.expander(f"**{strat}**"):
                st.markdown(sections[strat])

    # ML strategies
    st.markdown("---")
    st.markdown("### ML-Integrated Strategies")

    # ML overview sections
    ml_overview = [
        "Understanding ML Model Types",
        "Look-Ahead Bias Prevention",
    ]
    for section in ml_overview:
        if section in sections:
            with st.expander(f"**{section}**", expanded=True):
                st.markdown(sections[section])

    ml_strategies = ["ML Signal", "ML Filter", "ML Strategy Comparison"]
    for strat in ml_strategies:
        if strat in sections:
            expanded = strat == "ML Strategy Comparison"
            with st.expander(f"**{strat}**", expanded=expanded):
                st.markdown(sections[strat])

    # Working with configs
    st.markdown("---")
    st.markdown("### Configuration & Extension")

    config_sections = [
        "Working with Strategy Configs",
        "Creating New Strategies",
        "Best Practices",
    ]
    for section in config_sections:
        if section in sections:
            with st.expander(f"**{section}**"):
                st.markdown(sections[section])


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
    The experiments module enables **autonomous experimentation** with trading
    strategy parameters, ML hyperparameters, and feature selection. AI proposes
    experiments, users approve them.
    """)

    content = load_doc("EXPERIMENTS.md")

    if content.startswith("*Document not found"):
        st.error(content)
        return

    sections = extract_sections(content)

    # Key experiment sections
    key_sections = [
        "Overview",
        "Core Concepts",
        "CLI Commands",
        "Search Space Format",
        "Experiment Chaining",
        "MCP Tools",
        "Database Schema",
        "Python API",
        "Example Workflow",
        "Best Practices",
    ]

    for section in key_sections:
        if section in sections:
            expanded = section == "Overview" or section == "CLI Commands"
            with st.expander(f"**{section}**", expanded=expanded):
                st.markdown(sections[section])

    # Quick reference
    st.markdown("---")
    st.markdown("### Quick Reference")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        **Propose Experiment**
        ```bash
        g2 experiment propose \\
          --name "momentum_opt" \\
          --strategy momentum \\
          --search-space '{"lookback_days": \\
            {"type": "int", "low": 5, "high": 30}}' \\
          --symbols AAPL,MSFT \\
          --search-method bayesian
        ```
        """)

    with col2:
        st.markdown("""
        **Approve & Run**
        ```bash
        # List pending
        g2 experiment list --status proposed

        # Approve
        g2 experiment approve --id 1

        # Run
        g2 experiment run --id 1

        # View results
        g2 experiment results --id 1
        ```
        """)

    st.markdown("### Search Strategies")
    strategies = {
        "grid": "Exhaustive - all parameter combinations",
        "random": "Random sampling - quick exploration",
        "bayesian": "Adaptive optimization (Optuna TPE) - efficient",
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
        g2 data-update --exchange NASDAQ --limit 50
        ```

        **ML Model Not Found**
        ```bash
        # List available models
        g2 ml model-list

        # Check model artifacts exist
        ls -la models/
        ```

        **Feature Computation Errors**
        ```bash
        # Check feature definitions
        g2 feat-def-list

        # Recompute features
        g2 feat-compute --exchange NASDAQ --local --refresh-existing
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
    - **CLI Help**: Run `g2 --help` or `g2 <command> --help`
    - **Logs**: Check Docker logs with `docker compose logs postgres`
    - **AI Assistant**: Use the AI Assistant page for example queries
    """)
