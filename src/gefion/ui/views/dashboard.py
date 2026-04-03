"""Dashboard page - Overview and quick access."""

import streamlit as st
from datetime import datetime, timedelta
from gefion.ui.components.chat import render_chat_widget
from dataclasses import dataclass, field
from typing import Optional
from gefion.observability import create_span, set_attributes


@st.cache_data(ttl=300)
def _get_dashboard_context_data():
    """Cached dashboard context data — avoids repeated slow queries."""
    data = {}
    try:
        from gefion.ui.components.database import get_connection
        from datetime import date as date_type
        with create_span("ui.dashboard._get_dashboard_context_data"):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM stocks")
                    data["stocks"] = cur.fetchone()[0]
                    cur.execute("SELECT date FROM stock_ohlcv ORDER BY date DESC LIMIT 1")
                    row = cur.fetchone()
                    latest = row[0] if row else None
                    if latest:
                        data["latest_data"] = str(latest)
                        data["data_age_days"] = (date_type.today() - latest).days
                        # Top movers — use fast direct join
                        cur.execute("SELECT date FROM stock_ohlcv WHERE date < %s ORDER BY date DESC LIMIT 1", (latest,))
                        prev = cur.fetchone()[0]
                        if prev:
                            cur.execute("""
                                SELECT s.symbol,
                                       ROUND(((o2.close / NULLIF(o1.close, 0)) - 1) * 100, 2)
                                FROM stock_ohlcv o2
                                JOIN stock_ohlcv o1 ON o1.data_id = o2.data_id AND o1.date = %s
                                JOIN stocks s ON s.id = o2.data_id
                                WHERE o2.date = %s AND o1.close > 0
                                ORDER BY ABS((o2.close / NULLIF(o1.close, 0)) - 1) DESC
                                LIMIT 5
                            """, (prev, latest))
                            data["top_movers"] = [
                                {"symbol": r[0], "pct": float(r[1])} for r in cur.fetchall() if r[1]
                            ]
    except Exception:
        pass
    return data


def get_page_context():
    """Return compact context dict for the Dashboard page. Uses cached data."""
    context = {"page_name": "Dashboard", "summary": "Market overview with movers, system stats, and prediction insights."}
    cached = _get_dashboard_context_data()
    context["data_stats"] = cached
    data_age = cached.get("data_age_days")
    if data_age and data_age > 3:
        context["empty_states"] = [f"price data is {data_age} days old"]
        context["suggestions"] = ["Update prices: gefion data-update"]
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


@st.cache_data(ttl=300)
def get_market_movers() -> Optional[MarketMovers]:
    """Get top gainers and losers with 60-second cache."""
    try:
        from gefion.ui.components.database import get_connection

        with create_span("ui.dashboard.get_market_movers"):
          with get_connection() as conn:
            with conn.cursor() as cur:
                # Fast: get the 2 most recent dates first, then join
                cur.execute("SELECT date FROM stock_ohlcv ORDER BY date DESC LIMIT 1")
                row = cur.fetchone()
                latest = row[0] if row else None
                if not latest:
                    return None
                cur.execute("SELECT date FROM stock_ohlcv WHERE date < %s ORDER BY date DESC LIMIT 1", (latest,))
                prev = cur.fetchone()[0]
                if not prev:
                    return None

                cur.execute("""
                    SELECT
                        s.symbol,
                        o2.close as current_close,
                        o1.close as prev_close,
                        ((o2.close / NULLIF(o1.close, 0)) - 1) * 100 as pct_change
                    FROM stock_ohlcv o2
                    JOIN stock_ohlcv o1 ON o1.data_id = o2.data_id AND o1.date = %s
                    JOIN stocks s ON s.id = o2.data_id
                    WHERE o2.date = %s AND o1.close > 0
                    ORDER BY pct_change DESC
                    LIMIT 5
                """, (prev, latest))
                gainers = cur.fetchall()

                cur.execute("""
                    SELECT
                        s.symbol,
                        o2.close as current_close,
                        o1.close as prev_close,
                        ((o2.close / NULLIF(o1.close, 0)) - 1) * 100 as pct_change
                    FROM stock_ohlcv o2
                    JOIN stock_ohlcv o1 ON o1.data_id = o2.data_id AND o1.date = %s
                    JOIN stocks s ON s.id = o2.data_id
                    WHERE o2.date = %s AND o1.close > 0
                    ORDER BY pct_change ASC
                    LIMIT 5
                """, (prev, latest))
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

        with create_span("ui.dashboard.get_gefion_insights"):
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

                # Feature coverage — fast queries only
                cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE active = true")
                insights.features_defined = cur.fetchone()[0] or 0

                cur.execute("SELECT date FROM computed_features ORDER BY date DESC LIMIT 1")
                row = cur.fetchone()
                latest_date = row[0] if row else None
                insights.latest_feature_date = str(latest_date) if latest_date else None

                # Use TimescaleDB chunk stats (parent n_live_tup is always 0)
                from gefion.ui.components.database import hypertable_approx_row_count
                approx_rows = hypertable_approx_row_count(cur, 'computed_features')

                # Approximate: features_computed = active definitions, symbols = stocks count
                insights.features_computed = insights.features_defined
                cur.execute("SELECT COUNT(*) FROM stocks")
                insights.symbols_covered = cur.fetchone()[0] or 0 if approx_rows > 0 else 0

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

    quick_actions = [
        (col1, "Charts", ":material/bar_chart:", "Price charts and analysis", "Open Charts", "quick_charts", "Charts"),
        (col2, "System", ":material/bolt:", "Health, actions, and history", "System Ops", "quick_ai", "System Operations"),
        (col3, "Backtest", ":material/history:", "Test strategies on history", "Run Backtest", "quick_backtest", "Backtesting"),
        (col4, "ML Predict", ":material/model_training:", "Generate price predictions", "Get Predictions", "quick_ml", "ML Pipeline"),
    ]

    for col, title, icon, desc, btn_label, btn_key, page in quick_actions:
        with col:
            st.markdown(f"#### {icon} {title}")
            st.caption(desc)
            if st.button(btn_label, key=btn_key):
                st.session_state.current_page = page
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
