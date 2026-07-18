"""
Gefion UI - Main Streamlit Application.

A comprehensive interface for stock analysis, ML predictions, and backtesting.
"""

import streamlit as st

# Page config must be first Streamlit command
st.set_page_config(
    page_title="Gefion",
    page_icon=":material/trending_up:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for better styling
st.markdown("""
<style>
    /* Hide deploy button */
    .stDeployButton,
    [data-testid="stAppDeployButton"],
    button[kind="header"] {
        display: none !important;
    }

    /* Card-like containers */
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
    }

    /* Center sidebar title */
    [data-testid="stSidebar"] h1 {
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# Page definitions: (label, material_icon)
PAGES = [
    ("Dashboard", ":material/grid_view:"),
    ("System Operations", ":material/bolt:"),
    ("Data Management", ":material/storage:"),
    ("Features", ":material/tune:"),
    ("ML Pipeline", ":material/model_training:"),
    ("Backtesting", ":material/history:"),
    ("Experiments", ":material/science:"),
    ("Regimes", ":material/insights:"),
    ("Candidates", ":material/gavel:"),
    ("Charts", ":material/bar_chart:"),
    ("Documentation", ":material/menu_book:"),
    ("Settings", ":material/settings:"),
]


def navigate_to(page_name: str):
    """Navigate to a specific page."""
    st.session_state.current_page = page_name


def main():
    """Main application entry point."""
    # Initialize session state for navigation
    if "current_page" not in st.session_state:
        st.session_state.current_page = PAGES[0][0]

    # Sidebar navigation
    with st.sidebar:
        st.markdown("# :material/model_training: Gefion :material/stars_2:")
        st.markdown("---")

        # Navigation buttons
        for label, icon in PAGES:
            is_current = st.session_state.current_page == label
            if st.button(
                label,
                key=f"nav_{label}",
                type="primary" if is_current else "secondary",
                width="stretch",
                icon=icon,
            ):
                st.session_state.current_page = label
                st.rerun()

        st.markdown("---")
        st.caption("Gefion v0.1.0")

    # Main content area based on selected page
    current_page = st.session_state.current_page

    try:
        if current_page == "Dashboard":
            from gefion.ui.views.dashboard import render_dashboard
            render_dashboard()
        elif current_page == "Charts":
            from gefion.ui.views.charts import render_charts
            render_charts()
        elif current_page == "System Operations":
            from gefion.ui.views.assistant import render_assistant
            render_assistant()
        elif current_page == "Data Management":
            from gefion.ui.views.data import render_data
            render_data()
        elif current_page == "Features":
            from gefion.ui.views.features import render_features
            render_features()
        elif current_page == "ML Pipeline":
            from gefion.ui.views.ml import render_ml
            render_ml()
        elif current_page == "Backtesting":
            from gefion.ui.views.backtest import render_backtest
            render_backtest()
        elif current_page == "Experiments":
            from gefion.ui.views.experiments import render_experiments
            render_experiments()
        elif current_page == "Regimes":
            from gefion.ui.views.regimes import render_regimes
            render_regimes()
        elif current_page == "Candidates":
            from gefion.ui.views.candidates import render_candidates
            render_candidates()
        elif current_page == "Documentation":
            from gefion.ui.views.documentation import render_docs
            render_docs()
        elif current_page == "Settings":
            from gefion.ui.views.settings import render_settings
            render_settings()
    except Exception as e:
        import traceback
        from gefion.ui.errors import log_ui_error
        tb = traceback.format_exc()
        log_ui_error(
            source="render",
            message=f"{type(e).__name__}: {e}",
            context={"page": current_page, "traceback": tb},
        )
        st.error(f"Error rendering {current_page}: {e}")
        with st.expander("Details"):
            st.code(tb)





if __name__ == "__main__":
    main()
