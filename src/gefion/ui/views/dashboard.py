"""Dashboard page - Overview and quick access."""

import streamlit as st
from datetime import datetime, timedelta
from gefion.ui.components.chat import render_chat_widget
from dataclasses import dataclass, field
from typing import Optional


def get_page_context():
    """Return compact context dict for the Dashboard page."""
    context = {"page_name": "Dashboard", "summary": "Market overview with movers, system stats, and prediction insights."}
    try:
        from gefion.ui.components.database import get_connection
        from datetime import date as date_type
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stocks")
                stock_count = cur.fetchone()[0]
                cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                latest = cur.fetchone()[0]

                # Top movers summary
                if latest:
                    cur.execute("""
                        SELECT s.symbol,
                               ROUND(((o2.close - o1.close) / NULLIF(o1.close, 0) * 100)::numeric, 2) as pct
                        FROM stock_ohlcv o2
                        JOIN stock_ohlcv o1 ON o1.data_id = o2.data_id AND o1.date = o2.date - 1
                        JOIN stocks s ON s.id = o2.data_id
                        WHERE o2.date = %s
                        ORDER BY pct DESC LIMIT 3
                    """, (latest,))
                    top_gainers = [f"{r[0]} ({r[1]:+.1f}%)" for r in cur.fetchall()]

                    cur.execute("""
                        SELECT s.symbol,
                               ROUND(((o2.close - o1.close) / NULLIF(o1.close, 0) * 100)::numeric, 2) as pct
                        FROM stock_ohlcv o2
                        JOIN stock_ohlcv o1 ON o1.data_id = o2.data_id AND o1.date = o2.date - 1
                        JOIN stocks s ON s.id = o2.data_id
                        WHERE o2.date = %s
                        ORDER BY pct ASC LIMIT 3
                    """, (latest,))
                    top_losers = [f"{r[0]} ({r[1]:+.1f}%)" for r in cur.fetchall()]
                else:
                    top_gainers, top_losers = [], []

        data_age = (date_type.today() - latest).days if latest else None
        context["data_stats"] = {
            "stocks": stock_count,
            "latest_data": str(latest) if latest else "none",
            "data_age_days": data_age,
            "top_gainers": top_gainers,
            "top_losers": top_losers,
        }
        empty = []
        suggestions = []
        if data_age and data_age > 3:
            empty.append(f"price data is {data_age} days old")
            suggestions.append("Update prices: gefion data-update")
        context["empty_states"] = empty
        context["suggestions"] = suggestions
    except Exception:
        pass
    return context


@dataclass
class MarketMovers:
    """Cached market movers data."""
    gainers: list = field(default_factory=list)  # [(symbol, close, prev, pct), ...]
    losers: list = field(default_factory=list)


@dataclass
class GefionInsights:
    """Cached Gefion insights data."""
    pred_count: int = 0
    pred_date: Optional[str] = None
    bullish: list = field(default_factory=list)  # [(sym, q50, q90, horizon), ...]
    models: list = field(default_factory=list)  # [(name, horizon, q50_calib, loss), ...]
    features_computed: int = 0
    features_defined: int = 0
    symbols_covered: int = 0
    latest_feature_date: Optional[str] = None


@st.cache_data(ttl=60)
def get_market_movers() -> Optional[MarketMovers]:
    """Get top gainers and losers with 60-second cache."""
    try:
        from gefion.ui.components.database import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Fast query: only look at last 2 trading days
                cur.execute("""
                    WITH last_dates AS (
                        SELECT DISTINCT date FROM stock_ohlcv
                        ORDER BY date DESC LIMIT 2
                    ),
                    price_changes AS (
                        SELECT
                            s.symbol,
                            MAX(CASE WHEN o.date = (SELECT MAX(date) FROM last_dates) THEN o.close END) as current_close,
                            MAX(CASE WHEN o.date = (SELECT MIN(date) FROM last_dates) THEN o.close END) as prev_close
                        FROM stock_ohlcv o
                        JOIN stocks s ON o.data_id = s.id
                        WHERE s.status = 'Active'
                          AND o.date IN (SELECT date FROM last_dates)
                        GROUP BY s.symbol
                        HAVING MAX(CASE WHEN o.date = (SELECT MIN(date) FROM last_dates) THEN o.close END) > 0
                    )
                    SELECT
                        symbol,
                        current_close,
                        prev_close,
                        ((current_close / prev_close) - 1) * 100 as pct_change
                    FROM price_changes
                    WHERE current_close IS NOT NULL AND prev_close IS NOT NULL
                    ORDER BY pct_change DESC
                    LIMIT 5
                """)
                gainers = cur.fetchall()

                cur.execute("""
                    WITH last_dates AS (
                        SELECT DISTINCT date FROM stock_ohlcv
                        ORDER BY date DESC LIMIT 2
                    ),
                    price_changes AS (
                        SELECT
                            s.symbol,
                            MAX(CASE WHEN o.date = (SELECT MAX(date) FROM last_dates) THEN o.close END) as current_close,
                            MAX(CASE WHEN o.date = (SELECT MIN(date) FROM last_dates) THEN o.close END) as prev_close
                        FROM stock_ohlcv o
                        JOIN stocks s ON o.data_id = s.id
                        WHERE s.status = 'Active'
                          AND o.date IN (SELECT date FROM last_dates)
                        GROUP BY s.symbol
                        HAVING MAX(CASE WHEN o.date = (SELECT MIN(date) FROM last_dates) THEN o.close END) > 0
                    )
                    SELECT
                        symbol,
                        current_close,
                        prev_close,
                        ((current_close / prev_close) - 1) * 100 as pct_change
                    FROM price_changes
                    WHERE current_close IS NOT NULL AND prev_close IS NOT NULL
                    ORDER BY pct_change ASC
                    LIMIT 5
                """)
                losers = cur.fetchall()

        return MarketMovers(gainers=list(gainers), losers=list(losers))
    except Exception:
        return None


@st.cache_data(ttl=300)
def get_gefion_insights() -> Optional[GefionInsights]:
    """Get Gefion insights data with 60-second cache."""
    try:
        from gefion.ui.components.database import get_connection

        insights = GefionInsights()

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Predictions - table may not exist yet
                # Predictions - table may not exist yet
                try:
                    cur.execute("""
                        SELECT COUNT(*), MAX(prediction_date)
                        FROM predictions
                        WHERE prediction_type = 'quantile'
                          AND prediction_date >= CURRENT_DATE - INTERVAL '7 days'
                    """)
                    pred_count, pred_date = cur.fetchone()
                    insights.pred_count = pred_count or 0
                    insights.pred_date = str(pred_date) if pred_date else None

                    if insights.pred_count > 0:
                        cur.execute("""
                            SELECT s.symbol,
                                   (p.prediction_values->>'q50')::NUMERIC,
                                   (p.prediction_values->>'q90')::NUMERIC,
                                   p.horizon_days
                            FROM predictions p
                            JOIN stocks s ON p.data_id = s.id
                            WHERE p.prediction_type = 'quantile'
                              AND p.prediction_date = (
                                  SELECT MAX(prediction_date) FROM predictions
                                  WHERE prediction_type = 'quantile'
                              )
                              AND p.horizon_days = 7
                            ORDER BY (p.prediction_values->>'q50')::NUMERIC DESC
                            LIMIT 3
                        """)
                        insights.bullish = list(cur.fetchall())
                except Exception:
                    pass  # Table may not exist

                # Model performance - table may not exist yet
                try:
                    cur.execute("""
                        SELECT model_name, horizon_days,
                               q50_calibration, quantile_loss
                        FROM model_performance
                        ORDER BY updated_at DESC
                        LIMIT 3
                    """)
                    insights.models = list(cur.fetchall())
                except Exception:
                    pass  # Table may not exist

                # Feature coverage — use fast queries (avoid full hypertable scans)
                cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE active = true")
                insights.features_defined = cur.fetchone()[0] or 0

                cur.execute("SELECT MAX(date) FROM computed_features")
                latest_date = cur.fetchone()[0]
                insights.latest_feature_date = str(latest_date) if latest_date else None

                # Approximate feature coverage from recent data only (fast)
                cur.execute("""
                    SELECT COUNT(DISTINCT feature_id), COUNT(DISTINCT data_id)
                    FROM computed_features
                    WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                """)
                feat_computed, symbols_covered = cur.fetchone()
                insights.features_computed = feat_computed or 0
                insights.symbols_covered = symbols_covered or 0

        return insights
    except Exception:
        return None


def render_dashboard():
    """Render the main dashboard."""
    st.markdown("# :material/grid_view: Dashboard")
    render_chat_widget(get_page_context())
    st.markdown("Welcome to Gefion — your AI-powered trading analysis platform.")

    # System status section
    st.header("System Status", help="Current state of the Gefion system")

    from gefion.ui.components.status import render_system_status
    render_system_status()

    st.markdown("---")

    # Quick actions
    st.header("Quick Actions")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("### :material/bar_chart: Charts")
        st.markdown("Analyze price movements with interactive charts.")
        if st.button("Open Charts", key="quick_charts", width="stretch"):
            st.session_state.current_page = "Charts"
            st.rerun()

    with col2:
        st.markdown("### :material/bolt: System Operations")
        st.markdown("System health, actions, and history.")
        if st.button("System Operations", key="quick_ai", width="stretch"):
            st.session_state.current_page = "System Operations"
            st.rerun()

    with col3:
        st.markdown("### :material/history: Backtest")
        st.markdown("Test trading strategies on historical data.")
        if st.button("Run Backtest", key="quick_backtest", width="stretch"):
            st.session_state.current_page = "Backtesting"
            st.rerun()

    with col4:
        st.markdown("### :material/model_training: ML Predict")
        st.markdown("Generate price predictions using trained models.")
        if st.button("Get Predictions", key="quick_ml", width="stretch"):
            st.session_state.current_page = "ML Pipeline"
            st.rerun()

    st.markdown("---")

    # Recent activity / top movers
    st.header("Market Overview")

    movers = get_market_movers()

    if movers is None:
        st.warning("Could not load market overview")
    elif movers.gainers or movers.losers:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Top Gainers")
            if movers.gainers:
                for symbol, close, prev, pct in movers.gainers:
                    st.metric(
                        symbol,
                        f"${close:.2f}",
                        f"{pct:+.2f}%",
                        delta_color="normal"
                    )
            else:
                st.caption("No gainers today")

        with col2:
            st.subheader("📉 Top Losers")
            if movers.losers:
                for symbol, close, prev, pct in movers.losers:
                    st.metric(
                        symbol,
                        f"${close:.2f}",
                        f"{pct:+.2f}%",
                        delta_color="normal"  # negative = red
                    )
            else:
                st.caption("No losers today")
    else:
        st.info("No recent market data available. Update data to see market overview.")

    st.markdown("---")

    # Gefion Insights section
    st.header("Gefion Insights")

    insights = get_gefion_insights()

    if insights is None:
        st.warning("Could not load Gefion insights")
    else:
        col1, col2, col3 = st.columns(3)

        # Recent predictions
        with col1:
            st.subheader("Recent Predictions")
            if insights.pred_count > 0:
                st.metric("Predictions (7d)", f"{insights.pred_count:,}")
                if insights.pred_date:
                    st.caption(f"Latest: {insights.pred_date}")

                if insights.bullish:
                    st.caption("**Most bullish (7d):**")
                    for sym, q50, q90, _ in insights.bullish:
                        st.caption(f"  {sym}: {q50:+.1%} (up to {q90:+.1%})")
            else:
                st.caption("No recent predictions")
                st.caption("Run ML Pipeline → Predict")

        # Model performance
        with col2:
            st.subheader("Model Performance")
            if insights.models:
                for name, horizon, q50_calib, loss in insights.models:
                    st.caption(f"**{name}** ({horizon}d)")
                    if q50_calib is not None:
                        st.caption(f"  Q50 calib: {float(q50_calib):.1f}%")
                    if loss is not None:
                        st.caption(f"  Loss: {float(loss):.4f}")
            else:
                st.caption("No model evaluations yet")
                st.caption("Run ML Pipeline → Evaluate")

        # Feature coverage
        with col3:
            st.subheader("Feature Coverage")
            if insights.features_computed:
                st.metric("Features Active", f"{insights.features_computed}/{insights.features_defined}")
                st.metric("Symbols Covered", insights.symbols_covered)
                if insights.latest_feature_date:
                    st.caption(f"Latest: {insights.latest_feature_date}")
            else:
                st.caption("No features computed")
                st.caption("Run Data Management → Update")

        st.caption("_Data cached for 60 seconds_")

    st.markdown("---")

    # Help section
    with st.expander(":material/info: Getting Started", expanded=False):
        st.markdown("""
        ### Welcome to Gefion!

        **Gefion** is a comprehensive trading analysis platform that combines:
        - :material/bar_chart: **Charts** - Candlesticks, comparisons, volatility analysis
        - :material/bolt: **System Operations** - Health monitoring and suggested actions
        - :material/model_training: **ML Predictions** - Quantile regression and trend classification
        - :material/history: **Backtesting** - Test strategies with realistic execution modeling

        ### Quick Start

        1. **Update Data**: Go to Data Management → Update to fetch latest prices
        2. **View Charts**: Select a symbol and explore different chart types
        3. **Use Claude Code**: Copy prompts from AI Prompts for analysis
        4. **Run Backtests**: Test strategies on your portfolio

        ### Keyboard Shortcuts

        - `Ctrl+Enter` - Submit forms
        - `Esc` - Close modals
        """)
