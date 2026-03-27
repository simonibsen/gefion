"""Settings page - Configuration and preferences."""

import streamlit as st
import os
from gefion.ui.components.chat import render_chat_widget


def render_settings():
    """Render the settings page."""
    st.markdown("# :material/settings: Settings")
    render_chat_widget({"page_name": "Settings"})
    st.markdown("Configure Gefion settings and preferences.")

    tab1, tab2 = st.tabs([":material/database: Database", ":material/info: About"])

    with tab1:
        render_database_settings()

    with tab2:
        render_about()


def render_database_settings():
    """Render database configuration."""
    st.subheader("Database Configuration")

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://gefion:gefionpass@localhost:6432/gefion"
    )

    st.markdown("### Connection String")
    st.code(db_url.replace(":gefionpass@", ":****@"))  # Mask password

    # Test connection
    if st.button("Test Connection"):
        try:
            from gefion.ui.components.database import get_connection

            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
                    version = cur.fetchone()[0]

            st.success(f"Connected!")
            st.caption(f"PostgreSQL: {version[:50]}...")
        except Exception as e:
            st.error(f"Connection failed: {e}")

    st.markdown("---")

    st.markdown("### Database Statistics")

    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Get table sizes
                cur.execute("""
                    SELECT
                        relname as table_name,
                        pg_size_pretty(pg_total_relation_size(relid)) as total_size,
                        pg_size_pretty(pg_relation_size(relid)) as table_size,
                        n_live_tup as row_count
                    FROM pg_stat_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC
                    LIMIT 10
                """)
                tables = cur.fetchall()

                if tables:
                    import pandas as pd
                    df = pd.DataFrame(
                        tables,
                        columns=["Table", "Total Size", "Table Size", "Rows"]
                    )
                    st.dataframe(df, use_container_width=True)

    except Exception as e:
        st.error(f"Error: {e}")

    st.markdown("---")

    # New connection string
    st.markdown("### Update Connection")
    new_db_url = st.text_input(
        "Database URL",
        placeholder="postgresql://user:pass@host:port/dbname",
        help="Update database connection string",
    )

    if st.button("Update Connection") and new_db_url:
        os.environ["DATABASE_URL"] = new_db_url
        st.success("Connection string updated for this session")
        st.warning("Restart the app to use new connection")


def render_about():
    """Render about section."""
    st.subheader("About Gefion")

    st.markdown("""
    ## Gefion Trading Analysis Platform

    **Gefion** is a comprehensive trading analysis platform that combines:

    - :material/bar_chart: **Charts** - Professional Plotly visualizations
    - :material/bolt: **AI Actions** - Example queries for Claude Code
    - :material/model_training: **ML Pipeline** - Quantile regression & classification models
    - :material/history: **Backtesting** - Strategy testing with realistic execution

    ### Features

    | Feature | Description |
    |---------|-------------|
    | Price Charts | Candlestick, volume, moving averages |
    | Comparison | Multi-symbol normalized comparison |
    | Correlation | Return correlation heatmaps |
    | Volatility | Bollinger Bands, ATR, historical vol |
    | Drawdown | Peak-to-trough analysis |
    | ML Predictions | q10/q50/q90 price forecasts |
    | Backtesting | 4 built-in strategies + custom |

    ### Tech Stack

    - **Backend**: Python, PostgreSQL, TimescaleDB
    - **ML**: scikit-learn, XGBoost, LightGBM
    - **Charts**: Plotly
    - **UI**: Streamlit
    - **AI**: Claude (Anthropic)

    ### Links

    - [Documentation](https://github.com/simonibsen/gefion)
    - [Report Issues](https://github.com/simonibsen/gefion/issues)

    ### Version

    Gefion v1.0

    ---

    Made with ❤️ for quantitative analysis
    """)

    # System info
    with st.expander("System Information"):
        import sys
        import platform

        st.markdown(f"""
        - **Python**: {sys.version}
        - **Platform**: {platform.platform()}
        - **Streamlit**: {st.__version__}
        """)

        try:
            import plotly
            st.markdown(f"- **Plotly**: {plotly.__version__}")
        except ImportError:
            st.markdown("- **Plotly**: Not installed")
