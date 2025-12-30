"""AI Assistant page - Claude Code integration."""

import streamlit as st


def render_assistant():
    """Render the AI Assistant page."""
    st.title("🤖 AI Assistant")
    st.markdown("Use Claude Code for AI-powered analysis of your trading data.")

    st.info("""
    💡 **Claude Code Integration**

    This UI works alongside your Claude Code session. Ask Claude Code questions
    about your g2 data directly in your terminal - it has full access to:
    - Database queries via MCP tools
    - Chart generation
    - Backtesting
    - ML predictions
    """)

    st.markdown("---")

    # Quick prompts section
    st.subheader("📋 Quick Prompts")
    st.markdown("Copy these prompts to use with Claude Code:")

    prompts = [
        {
            "title": "Market Analysis",
            "prompt": "Analyze the current market conditions for my portfolio. Look at recent price movements, volatility, and any notable patterns.",
            "icon": "📊"
        },
        {
            "title": "Stock Deep Dive",
            "prompt": "Give me a detailed analysis of [SYMBOL] including recent price action, technical indicators, and any predictions we have.",
            "icon": "🔍"
        },
        {
            "title": "Compare Stocks",
            "prompt": "Compare the performance of [SYMBOL1] and [SYMBOL2] over the last year. Show me a comparison chart.",
            "icon": "⚖️"
        },
        {
            "title": "Strategy Recommendation",
            "prompt": "Based on my data, which trading strategy (momentum, mean_reversion, ma_crossover, breakout) would have performed best recently?",
            "icon": "🎯"
        },
        {
            "title": "Risk Assessment",
            "prompt": "Analyze the risk profile of my portfolio. Look at volatility, drawdowns, and correlation between holdings.",
            "icon": "⚠️"
        },
        {
            "title": "ML Predictions",
            "prompt": "What are the current ML predictions for my top holdings? Show me the q10/q50/q90 ranges.",
            "icon": "🧠"
        },
    ]

    cols = st.columns(2)
    for i, p in enumerate(prompts):
        with cols[i % 2]:
            with st.expander(f"{p['icon']} {p['title']}"):
                st.code(p["prompt"], language=None)

    st.markdown("---")

    # Database context section
    st.subheader("📊 Current Data Context")
    st.markdown("Information Claude Code can access about your g2 setup:")

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get counts
                cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                stock_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM stock_ohlcv")
                price_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE active = true")
                feature_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM ml_models")
                model_count = cur.fetchone()[0]

                # Get date range
                cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                date_range = cur.fetchone()

                # Get symbols
                cur.execute("""
                    SELECT symbol FROM stocks
                    WHERE status = 'Active'
                    ORDER BY symbol LIMIT 20
                """)
                symbols = [row[0] for row in cur.fetchall()]

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Active Stocks", stock_count)
        with col2:
            st.metric("Price Records", f"{price_count:,}")
        with col3:
            st.metric("Features", feature_count)
        with col4:
            st.metric("ML Models", model_count)

        if date_range[0] and date_range[1]:
            st.caption(f"Data range: {date_range[0]} to {date_range[1]}")

        if symbols:
            st.markdown("**Available Symbols:**")
            st.code(" ".join(symbols) + ("..." if stock_count > 20 else ""))

    except Exception as e:
        st.error(f"Could not load context: {e}")

    st.markdown("---")

    # MCP Tools reference
    st.subheader("🔧 Available MCP Tools")
    st.markdown("Claude Code can use these g2 tools directly:")

    tools = [
        ("query_database", "Run SQL queries on g2 data"),
        ("data_update", "Fetch latest prices from AlphaVantage"),
        ("features_list", "List available technical indicators"),
        ("ml_train", "Train quantile regression models"),
        ("ml_predict", "Generate price predictions"),
        ("ml_eval", "Evaluate model performance"),
        ("backtest_run", "Run strategy backtests"),
        ("backtest_compare", "Compare multiple strategies"),
        ("system_status", "Check system health"),
    ]

    for tool, desc in tools:
        st.markdown(f"- `{tool}` - {desc}")

    st.markdown("---")

    # Example conversation
    with st.expander("💬 Example Claude Code Conversation"):
        st.markdown("""
```
You: What's the current state of my g2 data?

Claude: Let me check the system status...
[Uses system_status tool]

You have 50 active stocks with price data from 2020-01-01 to today.
NVDA and AMD have the most recent updates. 3 ML models are trained
and ready for predictions.

You: Generate predictions for NVDA

Claude: I'll generate predictions using the latest model...
[Uses ml_predict tool]

NVDA 7-day prediction:
- Q10 (bearish): $130.50
- Q50 (median): $138.20
- Q90 (bullish): $145.80

The wide range suggests moderate uncertainty.

You: Show me a volatility chart for NVDA

Claude: Creating volatility analysis chart...
[Uses chart command]

[Opens chart in browser showing Bollinger Bands, ATR, and
historical volatility for NVDA]
```
        """)

    st.markdown("---")
    st.caption("💡 Tip: Keep Claude Code running in your terminal while using this UI for the best experience.")
