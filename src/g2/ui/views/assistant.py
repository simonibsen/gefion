"""AI Assistant page - Claude Code reference."""

import streamlit as st


def render_assistant():
    """Render the AI Assistant reference page."""
    st.title("🤖 AI Assistant")
    st.markdown("Reference for using Claude Code with g2.")

    st.info("""
    💡 Use Claude Code in your terminal to analyze g2 data.
    Claude Code has full access to the database via MCP tools.
    """)

    st.markdown("---")

    # Quick prompts section
    st.subheader("Example Prompts")

    prompts = [
        ("📊 Market Analysis", "Analyze the current market conditions for my portfolio."),
        ("🔍 Stock Deep Dive", "Give me a detailed analysis of NVDA."),
        ("⚖️ Compare Stocks", "Compare NVDA and AMD performance over the last year."),
        ("🎯 Strategy Test", "Which trading strategy would perform best on my data?"),
        ("⚠️ Risk Assessment", "Analyze the volatility and drawdowns in my portfolio."),
        ("🧠 ML Predictions", "What predictions do we have for my top holdings?"),
    ]

    for title, prompt in prompts:
        with st.expander(title):
            st.code(prompt, language=None)

    st.markdown("---")

    # Data context
    st.subheader("Current Data")

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                stock_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM stock_ohlcv")
                price_count = cur.fetchone()[0]

                cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                date_range = cur.fetchone()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Active Stocks", stock_count)
        with col2:
            st.metric("Price Records", f"{price_count:,}")

        if date_range[0]:
            st.caption(f"Data range: {date_range[0]} to {date_range[1]}")

    except Exception as e:
        st.error(f"Could not load data: {e}")

    st.markdown("---")

    # MCP Tools
    st.subheader("Available MCP Tools")

    tools = [
        ("query_database", "Run SQL queries"),
        ("data_update", "Fetch latest prices"),
        ("ml_train", "Train models"),
        ("ml_predict", "Generate predictions"),
        ("backtest_run", "Run backtests"),
        ("system_status", "Check system health"),
    ]

    cols = st.columns(2)
    for i, (tool, desc) in enumerate(tools):
        with cols[i % 2]:
            st.markdown(f"**{tool}** - {desc}")
