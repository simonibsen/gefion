"""AI Prompts page - Example prompts for Claude Code."""

import streamlit as st


def render_assistant():
    """Render the AI Prompts reference page."""
    st.title("🤖 AI Prompts")
    st.markdown("Example prompts for using Claude Code with g2.")

    st.info("""
    💡 Use Claude Code in your terminal to analyze g2 data.
    Claude Code has full access to the database via MCP tools.
    """)

    st.markdown("---")

    # Example prompts by category
    st.subheader("Example Prompts")

    # Market Analysis
    with st.expander("📊 Market Analysis", expanded=True):
        st.markdown("**Overview and trends across your portfolio**")
        prompts = [
            "Give me an overview of my current market data - how many stocks, date range, and any gaps in coverage.",
            "Which stocks in my portfolio have had the biggest moves in the last week?",
            "Show me the top 5 gainers and top 5 losers from yesterday.",
            "What's the average daily volume across my NASDAQ stocks?",
            "Are there any stocks with unusual volume in the last 3 days?",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Stock Analysis
    with st.expander("🔍 Individual Stock Analysis"):
        st.markdown("**Deep dive into specific symbols**")
        prompts = [
            "Analyze NVDA - show me price chart, key metrics, and recent performance.",
            "What's the 52-week high and low for AAPL? How close are we to each?",
            "Show me MSFT's volatility over the past 6 months using Bollinger Bands.",
            "Calculate the RSI for GOOGL and tell me if it's overbought or oversold.",
            "What's the average true range (ATR) for TSLA? Is it more or less volatile than usual?",
            "Show me the MACD crossovers for AMD in the last 3 months.",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Comparisons
    with st.expander("⚖️ Stock Comparisons"):
        st.markdown("**Compare performance across multiple symbols**")
        prompts = [
            "Compare NVDA and AMD performance over the last year. Which outperformed?",
            "Create a comparison chart of AAPL, MSFT, and GOOGL normalized to 100.",
            "Which of my tech stocks has the best risk-adjusted returns (Sharpe ratio)?",
            "Compare the volatility of TSLA vs the average of my other holdings.",
            "Show correlation matrix for my top 10 stocks by market cap.",
            "Which pairs of stocks in my portfolio move together most closely?",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Technical Analysis
    with st.expander("📈 Technical Analysis"):
        st.markdown("**Indicators and chart patterns**")
        prompts = [
            "Which stocks are currently showing RSI below 30 (oversold)?",
            "Find stocks where the 50-day MA just crossed above the 200-day MA (golden cross).",
            "Show me stocks trading near their Bollinger Band lower band.",
            "Which stocks have MACD histogram turning positive this week?",
            "Find stocks with price breaking above 20-day high on increased volume.",
            "What percentage of my stocks are above their 200-day moving average?",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Backtesting
    with st.expander("🎯 Backtesting & Strategies"):
        st.markdown("**Test and compare trading strategies**")
        prompts = [
            "Run a momentum strategy backtest on my NASDAQ stocks for the past year.",
            "Compare all 4 strategies (momentum, mean_reversion, ma_crossover, breakout) on NVDA.",
            "What would my returns be if I bought oversold stocks (RSI < 30) and sold at RSI > 70?",
            "Backtest a simple 50/200 MA crossover on my top 10 holdings.",
            "Which strategy had the lowest maximum drawdown over the past 2 years?",
            "Run momentum strategy with different lookback periods (10, 20, 30 days) and compare.",
        ]
        for p in prompts:
            st.code(p, language=None)

    # ML Predictions
    with st.expander("🧠 ML Predictions"):
        st.markdown("**Machine learning models and forecasts**")
        prompts = [
            "What ML models do I have trained? Show me their performance metrics.",
            "Generate 7-day predictions for my top 5 holdings.",
            "Which stocks have the widest prediction intervals (most uncertainty)?",
            "Show me stocks where the q90 prediction suggests >10% upside.",
            "How accurate have our q50 predictions been over the last month?",
            "Train a new quantile model on the latest NASDAQ data.",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Risk & Portfolio
    with st.expander("⚠️ Risk & Portfolio Analysis"):
        st.markdown("**Volatility, drawdowns, and risk metrics**")
        prompts = [
            "What's the maximum drawdown for each of my stocks over the past year?",
            "Show me a drawdown chart for NVDA - when were the worst periods?",
            "Calculate the rolling 30-day volatility for my portfolio.",
            "Which stocks contribute most to my portfolio's overall risk?",
            "If I equal-weight my top 10 stocks, what would the portfolio Sharpe ratio be?",
            "Show me the correlation between my stocks and identify diversification opportunities.",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Data Management
    with st.expander("📁 Data Management"):
        st.markdown("**Update and maintain your data**")
        prompts = [
            "Update prices for all my NASDAQ stocks.",
            "Which stocks haven't been updated in the last week?",
            "Add PLTR to my watchlist and fetch its full price history.",
            "How much data do I have for each stock? Show me coverage stats.",
            "Check system status - is the database healthy?",
            "Recompute all technical indicators for stocks updated today.",
        ]
        for p in prompts:
            st.code(p, language=None)

    # Charts
    with st.expander("📉 Chart Generation"):
        st.markdown("**Create visualizations**")
        prompts = [
            "Create a candlestick chart for NVDA with volume.",
            "Show me a volatility chart for AAPL with Bollinger Bands and ATR.",
            "Generate a correlation heatmap for my tech stocks.",
            "Create a drawdown chart for my portfolio over the past year.",
            "Show rolling 30-day returns for MSFT, GOOGL, and AMZN.",
            "Make a sector performance heatmap for my holdings.",
        ]
        for p in prompts:
            st.code(p, language=None)

    st.markdown("---")

    # Current data context
    st.subheader("Your Current Data")

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stocks WHERE status = 'Active'")
                stock_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM stock_ohlcv")
                price_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM ml_models")
                model_count = cur.fetchone()[0]

                cur.execute("SELECT MIN(date), MAX(date) FROM stock_ohlcv")
                date_range = cur.fetchone()

                cur.execute("""
                    SELECT symbol FROM stocks
                    WHERE status = 'Active'
                    ORDER BY symbol LIMIT 20
                """)
                symbols = [r[0] for r in cur.fetchall()]

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Active Stocks", stock_count)
        with col2:
            st.metric("Price Records", f"{price_count:,}")
        with col3:
            st.metric("ML Models", model_count)

        if date_range[0]:
            st.caption(f"Data range: {date_range[0]} to {date_range[1]}")

        if symbols:
            st.markdown("**Your symbols:** " + ", ".join(symbols) + ("..." if stock_count > 20 else ""))

    except Exception as e:
        st.error(f"Could not load data: {e}")

    st.markdown("---")

    # MCP Tools reference
    st.subheader("Available MCP Tools")
    st.markdown("Claude Code can use these tools directly:")

    tools = [
        ("query_database", "Run read-only SQL queries on g2 data"),
        ("data_update", "Fetch latest prices and compute features"),
        ("features_list", "List all technical indicators"),
        ("ml_dataset_build", "Create training dataset"),
        ("ml_train", "Train quantile regression models"),
        ("ml_predict", "Generate price predictions"),
        ("ml_eval", "Evaluate model performance"),
        ("backtest_run", "Run strategy backtest"),
        ("backtest_compare", "Compare multiple strategies"),
        ("system_status", "Check database and system health"),
    ]

    for tool, desc in tools:
        st.markdown(f"- **{tool}** - {desc}")
