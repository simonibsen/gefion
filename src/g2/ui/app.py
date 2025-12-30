"""
g2 Trading Analysis UI - Main Streamlit Application.

A comprehensive interface for stock analysis, ML predictions, and backtesting.
"""

import streamlit as st

# Page config must be first Streamlit command
st.set_page_config(
    page_title="g2 Trading Analysis",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for better styling
st.markdown("""
<style>
    /* Improve sidebar styling */
    .css-1d391kg { padding-top: 1rem; }

    /* Card-like containers */
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
    }

    /* Tooltip styling */
    .tooltip {
        position: relative;
        display: inline-block;
        cursor: help;
    }

    /* Better button styling */
    .stButton > button {
        width: 100%;
    }

    /* Chat message styling */
    .chat-message {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
    .chat-message.user {
        background-color: #e3f2fd;
    }
    .chat-message.assistant {
        background-color: #f5f5f5;
    }
</style>
""", unsafe_allow_html=True)


def main():
    """Main application entry point."""
    # Page options
    pages = [
        "🏠 Dashboard",
        "📊 Charts",
        "🤖 AI Assistant",
        "📁 Data Management",
        "🧠 ML Pipeline",
        "📈 Backtesting",
        "⚙️ Settings",
    ]

    # Initialize session state for navigation
    if "page" not in st.session_state:
        st.session_state.page = pages[0]

    # Ensure page is valid
    if st.session_state.page not in pages:
        st.session_state.page = pages[0]

    # Sidebar navigation
    with st.sidebar:
        st.title("📈 g2 Trading")
        st.markdown("---")

        # Navigation - use session state for current selection
        page = st.radio(
            "Navigation",
            pages,
            index=pages.index(st.session_state.page),
            label_visibility="collapsed",
            key="nav_radio",
        )

        # Update session state when radio changes
        if page != st.session_state.page:
            st.session_state.page = page

        st.markdown("---")

        # Quick status
        st.markdown("### System Status")
        try:
            from g2.ui.components.status import render_quick_status
            render_quick_status()
        except Exception as e:
            st.error(f"Status unavailable: {e}")

        st.markdown("---")
        st.caption("g2 Trading Analysis v1.0")

    # Main content area based on selected page
    current_page = st.session_state.page

    if current_page == "🏠 Dashboard":
        from g2.ui.pages.dashboard import render_dashboard
        render_dashboard()
    elif current_page == "📊 Charts":
        from g2.ui.pages.charts import render_charts
        render_charts()
    elif current_page == "🤖 AI Assistant":
        from g2.ui.pages.assistant import render_assistant
        render_assistant()
    elif current_page == "📁 Data Management":
        from g2.ui.pages.data import render_data
        render_data()
    elif current_page == "🧠 ML Pipeline":
        from g2.ui.pages.ml import render_ml
        render_ml()
    elif current_page == "📈 Backtesting":
        from g2.ui.pages.backtest import render_backtest
        render_backtest()
    elif current_page == "⚙️ Settings":
        from g2.ui.pages.settings import render_settings
        render_settings()


if __name__ == "__main__":
    main()
