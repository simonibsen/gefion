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

# Map document names to tab names for navigation
DOC_TO_TAB = {
    "Whitepaper": "📜 Whitepaper",
    "User Guide": "🚀 Quick Start",
    "ML Pipeline": "🧠 ML Pipeline",
    "Strategies": "⚔️ Strategies",
    "Backtesting": "📈 Backtesting",
    "Experiments": "🧪 Experiments",
    "Troubleshooting": "🔧 Troubleshooting",
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
def search_docs(query: str) -> List[Tuple[str, str, str, str, str]]:
    """
    Search all documents for query string.

    Returns list of (doc_name, section, matched_line, context, filename) tuples.
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

                    results.append((doc_name, section_name, line.strip()[:100], highlighted, filename))

                    # Limit results per section
                    if len([r for r in results if r[0] == doc_name and r[1] == section_name]) >= 3:
                        break

    return results[:50]  # Limit total results


def get_target_from_query() -> Tuple[str, str]:
    """Get navigation target from URL query parameters."""
    params = st.query_params
    return (
        params.get("doc", ""),
        params.get("section", "")
    )


def set_query_params(doc_name: str, section: str):
    """Set URL query parameters for navigation."""
    st.query_params["doc"] = doc_name
    st.query_params["section"] = section


def clear_query_params():
    """Clear navigation query parameters."""
    if "doc" in st.query_params:
        del st.query_params["doc"]
    if "section" in st.query_params:
        del st.query_params["section"]


def make_doc_link(doc_name: str, section: str) -> str:
    """Create a markdown link to a documentation section."""
    # URL-encode the section name for the query parameter
    import urllib.parse
    encoded_section = urllib.parse.quote(section)
    return f"?doc={doc_name}&section={encoded_section}"


def is_target_section(doc_name: str, section: str) -> bool:
    """Check if this section is the navigation target."""
    target_doc, target_section = get_target_from_query()
    return target_doc == doc_name and target_section == section


def render_section_expander(
    doc_name: str,
    section: str,
    content: str,
    default_expanded: bool = False
):
    """Render a section expander with target highlighting."""
    is_target = is_target_section(doc_name, section)
    expanded = default_expanded or is_target

    # Add visual indicator for target section
    label = f"**{section}**"
    if is_target:
        label = f"📍 **{section}**"

    with st.expander(label, expanded=expanded):
        st.markdown(content)


def render_docs():
    """Render the documentation page."""
    st.title("📚 Documentation")
    st.markdown("Guides and theory for g2 quantitative trading platform.")

    # Document options for navigation
    doc_options = [
        "📜 Whitepaper",
        "🚀 Quick Start",
        "🧠 ML Pipeline",
        "⚔️ Strategies",
        "📈 Backtesting",
        "🧪 Experiments",
        "🔧 Troubleshooting",
    ]

    # Check for navigation target from URL query params
    target_doc, target_section = get_target_from_query()

    # Determine default selection based on navigation target
    default_index = 0
    if target_doc:
        tab_name = DOC_TO_TAB.get(target_doc, target_doc)
        if tab_name in doc_options:
            default_index = doc_options.index(tab_name)

    # Search box and document selector in columns
    col_search, col_select = st.columns([2, 1])

    with col_search:
        search_query = st.text_input(
            "🔍 Search documentation",
            placeholder="Type to search across all docs...",
            key="doc_search"
        )

    with col_select:
        selected_doc = st.selectbox(
            "📄 Document",
            doc_options,
            index=default_index,
            key="doc_selector"
        )

    # Show search results if query entered
    if search_query and len(search_query) >= 2:
        results = search_docs(search_query)

        if results:
            st.markdown(f"### Found {len(results)} result(s) for '{search_query}'")

            # Group by document
            current_doc = None
            for idx, (doc_name, section, matched_line, context, filename) in enumerate(results):
                if doc_name != current_doc:
                    current_doc = doc_name
                    st.markdown(f"#### 📄 {doc_name}")

                # Create clickable link
                link = make_doc_link(doc_name, section)
                tab_name = DOC_TO_TAB.get(doc_name, doc_name)

                with st.expander(f"**{section}**: {matched_line[:60]}..."):
                    st.markdown(context)
                    st.markdown(f"[→ Go to {tab_name} › {section}]({link})")

            st.markdown("---")
        else:
            st.info(f"No results found for '{search_query}'")
            st.markdown("---")

    # Show target section indicator if navigating
    if target_doc and target_section:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.success(f"📍 Showing **{target_section}** section (expanded below)")
        with col2:
            if st.button("✕ Clear", key="clear_nav"):
                clear_query_params()
                st.rerun()

    st.markdown("---")

    # Render selected document
    if selected_doc == "📜 Whitepaper":
        render_whitepaper()
    elif selected_doc == "🚀 Quick Start":
        render_quickstart()
    elif selected_doc == "🧠 ML Pipeline":
        render_ml_docs()
    elif selected_doc == "⚔️ Strategies":
        render_strategies_docs()
    elif selected_doc == "📈 Backtesting":
        render_backtest_docs()
    elif selected_doc == "🧪 Experiments":
        render_experiments_docs()
    elif selected_doc == "🔧 Troubleshooting":
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
            render_section_expander("Whitepaper", section, sections[section], section == "Introduction")


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
            render_section_expander("User Guide", section, sections[section], False)

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
            render_section_expander("ML Pipeline", section, sections[section], expanded)

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
            render_section_expander("Strategies", section_name, sections[section_name], expanded)


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
            render_section_expander("Backtesting", section, sections[section], expanded)

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
            render_section_expander("Experiments", section, sections[section], expanded)

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
        render_section_expander("Troubleshooting", section, section_content, False)

    # Additional help
    st.markdown("---")
    st.markdown("### Getting Help")
    st.markdown("""
    - **System Status**: Check the sidebar for quick status
    - **CLI Help**: Run `g2 --help` or `g2 <command> --help`
    - **Logs**: Check Docker logs with `docker compose logs postgres`
    - **AI Assistant**: Use the AI Assistant page for example queries
    """)
