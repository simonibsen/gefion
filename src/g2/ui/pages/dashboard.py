"""Dashboard page - Overview and quick access."""

import streamlit as st
from datetime import datetime, timedelta


def render_dashboard():
    """Render the main dashboard."""
    st.title("📈 g2 Trading Dashboard")
    st.markdown("Welcome to g2 - your AI-powered trading analysis platform.")

    # System status section
    st.header("System Status", help="Current state of the g2 system")

    from g2.ui.components.status import render_system_status
    render_system_status()

    st.markdown("---")

    # Quick actions
    st.header("Quick Actions")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("### 📊 Charts")
        st.markdown("Analyze price movements with interactive charts.")
        if st.button("Open Charts", key="quick_charts", use_container_width=True):
            st.session_state.page = "📊 Charts"
            st.rerun()

    with col2:
        st.markdown("### 🤖 AI Assistant")
        st.markdown("Ask Claude about stocks, strategies, and analysis.")
        if st.button("Chat with Claude", key="quick_ai", use_container_width=True):
            st.session_state.page = "🤖 AI Assistant"
            st.rerun()

    with col3:
        st.markdown("### 📈 Backtest")
        st.markdown("Test trading strategies on historical data.")
        if st.button("Run Backtest", key="quick_backtest", use_container_width=True):
            st.session_state.page = "📈 Backtesting"
            st.rerun()

    with col4:
        st.markdown("### 🧠 ML Predict")
        st.markdown("Generate price predictions using trained models.")
        if st.button("Get Predictions", key="quick_ml", use_container_width=True):
            st.session_state.page = "🧠 ML Pipeline"
            st.rerun()

    st.markdown("---")

    # Recent activity / top movers
    st.header("Market Overview")

    try:
        from g2.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get top gainers/losers from recent data
                cur.execute("""
                    WITH recent AS (
                        SELECT DISTINCT ON (s.symbol)
                            s.symbol,
                            o.close as current_close,
                            o.date
                        FROM stock_ohlcv o
                        JOIN stocks s ON o.data_id = s.id
                        WHERE s.status = 'Active'
                        ORDER BY s.symbol, o.date DESC
                    ),
                    prev AS (
                        SELECT DISTINCT ON (s.symbol)
                            s.symbol,
                            o.close as prev_close
                        FROM stock_ohlcv o
                        JOIN stocks s ON o.data_id = s.id
                        WHERE s.status = 'Active'
                          AND o.date < (SELECT MAX(date) FROM stock_ohlcv)
                        ORDER BY s.symbol, o.date DESC
                    )
                    SELECT
                        r.symbol,
                        r.current_close,
                        p.prev_close,
                        ((r.current_close / p.prev_close) - 1) * 100 as pct_change
                    FROM recent r
                    JOIN prev p ON r.symbol = p.symbol
                    WHERE p.prev_close > 0
                    ORDER BY pct_change DESC
                    LIMIT 10
                """)
                movers = cur.fetchall()

        if movers:
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("📈 Top Gainers")
                for symbol, close, prev, pct in movers[:5]:
                    if pct > 0:
                        st.metric(
                            symbol,
                            f"${close:.2f}",
                            f"{pct:+.2f}%",
                            delta_color="normal"
                        )

            with col2:
                st.subheader("📉 Top Losers")
                for symbol, close, prev, pct in reversed(movers[-5:]):
                    if pct < 0:
                        st.metric(
                            symbol,
                            f"${close:.2f}",
                            f"{pct:+.2f}%",
                            delta_color="normal"
                        )
        else:
            st.info("No recent market data available. Update data to see market overview.")

    except Exception as e:
        st.warning(f"Could not load market overview: {e}")

    st.markdown("---")

    # Help section
    with st.expander("ℹ️ Getting Started", expanded=False):
        st.markdown("""
        ### Welcome to g2!

        **g2** is a comprehensive trading analysis platform that combines:
        - 📊 **Interactive Charts** - Candlesticks, comparisons, volatility analysis
        - 🤖 **AI Assistant** - Claude-powered analysis and recommendations
        - 🧠 **ML Predictions** - Quantile regression and trend classification
        - 📈 **Backtesting** - Test strategies with realistic execution modeling

        ### Quick Start

        1. **Update Data**: Go to Data Management → Update to fetch latest prices
        2. **View Charts**: Select a symbol and explore different chart types
        3. **Ask Claude**: Use the AI Assistant for analysis and insights
        4. **Run Backtests**: Test strategies on your portfolio

        ### Keyboard Shortcuts

        - `Ctrl+Enter` - Submit forms
        - `Esc` - Close modals
        """)
