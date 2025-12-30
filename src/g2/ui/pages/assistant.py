"""AI Assistant page - Claude integration for analysis."""

import streamlit as st
import os
from datetime import datetime


def get_system_context() -> str:
    """Build system context with current market data."""
    context_parts = ["You are a trading analysis assistant integrated with g2."]

    try:
        from g2.ui.components.database import get_connection, get_symbols

        symbols = get_symbols()
        context_parts.append(f"\nAvailable symbols: {len(symbols)} stocks in database.")

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get latest prices for context
                cur.execute("""
                    SELECT s.symbol, o.close, o.date
                    FROM stock_ohlcv o
                    JOIN stocks s ON o.data_id = s.id
                    WHERE o.date = (SELECT MAX(date) FROM stock_ohlcv)
                    ORDER BY s.symbol
                    LIMIT 20
                """)
                prices = cur.fetchall()

                if prices:
                    price_str = ", ".join([f"{s}: ${p:.2f}" for s, p, _ in prices[:10]])
                    context_parts.append(f"\nRecent prices: {price_str}")
                    context_parts.append(f"Data as of: {prices[0][2]}")

    except Exception:
        pass

    context_parts.append("""

You can help with:
- Stock analysis and insights
- Technical analysis interpretation
- Strategy recommendations
- Explaining chart patterns
- Portfolio suggestions

Always be helpful, accurate, and educational. When discussing specific stocks,
remind users that this is not financial advice.""")

    return "\n".join(context_parts)


def call_claude(messages: list, system: str) -> str:
    """Call Claude API with messages."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=system,
        messages=messages,
    )

    return response.content[0].text


def render_assistant():
    """Render the AI assistant page."""
    st.title("🤖 AI Assistant")
    st.markdown("Chat with Claude about stocks, strategies, and market analysis.")

    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.warning("⚠️ ANTHROPIC_API_KEY not set. Please set it in your environment or Settings page.")

        with st.expander("How to set API key"):
            st.markdown("""
            **Option 1: Environment variable**
            ```bash
            export ANTHROPIC_API_KEY=your-key-here
            ```

            **Option 2: Settings page**
            Go to Settings and enter your API key there.
            """)

        # Allow setting key in UI
        key_input = st.text_input("Enter API Key", type="password")
        if key_input:
            os.environ["ANTHROPIC_API_KEY"] = key_input
            st.success("API key set for this session")
            st.rerun()
        return

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "system_context" not in st.session_state:
        st.session_state.system_context = get_system_context()

    # Quick prompts
    st.markdown("### Quick Prompts")
    quick_prompts = st.columns(4)

    with quick_prompts[0]:
        if st.button("📊 Analyze AAPL", use_container_width=True):
            st.session_state.quick_prompt = "Analyze Apple (AAPL) stock. What are the key factors to consider?"

    with quick_prompts[1]:
        if st.button("📈 Bull vs Bear", use_container_width=True):
            st.session_state.quick_prompt = "What's the bull case vs bear case for the tech sector right now?"

    with quick_prompts[2]:
        if st.button("🎯 Strategy Tips", use_container_width=True):
            st.session_state.quick_prompt = "What are some effective momentum trading strategies?"

    with quick_prompts[3]:
        if st.button("📉 Risk Mgmt", use_container_width=True):
            st.session_state.quick_prompt = "Explain position sizing and risk management best practices."

    st.markdown("---")

    # Chat container
    chat_container = st.container()

    # Display chat history
    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    # Handle quick prompts
    if "quick_prompt" in st.session_state:
        prompt = st.session_state.quick_prompt
        del st.session_state.quick_prompt

        st.session_state.messages.append({"role": "user", "content": prompt})

        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        response = call_claude(
                            st.session_state.messages,
                            st.session_state.system_context
                        )
                        st.markdown(response)
                        st.session_state.messages.append({"role": "assistant", "content": response})
                    except Exception as e:
                        st.error(f"Error: {e}")

    # Chat input
    if prompt := st.chat_input("Ask about stocks, strategies, or analysis..."):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        response = call_claude(
                            st.session_state.messages,
                            st.session_state.system_context
                        )
                        st.markdown(response)
                        st.session_state.messages.append({"role": "assistant", "content": response})
                    except Exception as e:
                        st.error(f"Error calling Claude: {e}")

    # Sidebar controls
    with st.sidebar:
        st.markdown("### Chat Controls")

        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        if st.button("🔄 Refresh Context", use_container_width=True):
            st.session_state.system_context = get_system_context()
            st.success("Context refreshed with latest data")

        with st.expander("📋 System Context"):
            st.text(st.session_state.system_context[:500] + "...")


def render_stock_context(symbol: str) -> str:
    """Get detailed context for a specific stock."""
    try:
        from g2.ui.components.database import get_connection
        from g2.charts.analysis import compute_price_insights

        with get_connection() as conn:
            from g2.charts.queries import fetch_ohlcv_for_chart
            from datetime import timedelta, date

            end = date.today()
            start = end - timedelta(days=365)
            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

            if ohlcv:
                insights = compute_price_insights(ohlcv, {})
                return f"""
Stock: {symbol}
Current Price: ${insights.get('current_price', 0):.2f}
1-Day Change: {insights.get('change_1d_pct', 0):+.2f}%
1-Month Change: {insights.get('change_1m_pct', 0):+.2f}%
Period High: ${insights.get('period_high', 0):.2f}
Period Low: ${insights.get('period_low', 0):.2f}
Volatility: {insights.get('volatility', 0):.1f}%

Insights:
{chr(10).join('- ' + i for i in insights.get('insights', [])[:5])}
"""
    except Exception:
        pass
    return f"Stock: {symbol} (detailed data not available)"
