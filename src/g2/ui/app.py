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
    /* Card-like containers */
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
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

# Page options
PAGES = [
    "🏠 Dashboard",
    "📊 Charts",
    "🤖 AI Assistant",
    "📁 Data Management",
    "🧠 ML Pipeline",
    "📈 Backtesting",
    "⚙️ Settings",
]


def navigate_to(page_name: str):
    """Navigate to a specific page."""
    st.session_state.current_page = page_name


def main():
    """Main application entry point."""
    # Initialize session state for navigation
    if "current_page" not in st.session_state:
        st.session_state.current_page = PAGES[0]

    # Sidebar navigation
    with st.sidebar:
        st.title("📈 g2 Trading")
        st.markdown("---")

        # Navigation buttons instead of radio for reliable navigation
        for page in PAGES:
            is_current = st.session_state.current_page == page
            if st.button(
                page,
                key=f"nav_{page}",
                use_container_width=True,
                type="primary" if is_current else "secondary",
            ):
                st.session_state.current_page = page
                st.rerun()

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
    current_page = st.session_state.current_page

    if current_page == "🏠 Dashboard":
        from g2.ui.views.dashboard import render_dashboard
        render_dashboard()
    elif current_page == "📊 Charts":
        from g2.ui.views.charts import render_charts
        render_charts()
    elif current_page == "🤖 AI Assistant":
        from g2.ui.views.assistant import render_assistant
        render_assistant()
    elif current_page == "📁 Data Management":
        from g2.ui.views.data import render_data
        render_data()
    elif current_page == "🧠 ML Pipeline":
        from g2.ui.views.ml import render_ml
        render_ml()
    elif current_page == "📈 Backtesting":
        from g2.ui.views.backtest import render_backtest
        render_backtest()
    elif current_page == "⚙️ Settings":
        from g2.ui.views.settings import render_settings
        render_settings()


if __name__ == "__main__":
    main()
