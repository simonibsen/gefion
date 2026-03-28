"""Charts page - Interactive visualizations."""

import logging
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from gefion.ui.components.chat import render_chat_widget
from gefion.charts.d3.suggestions import suggest_visualization
from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


def get_page_context() -> Dict[str, Any]:
    """Return compact context dict for the Charts page."""
    context: Dict[str, Any] = {
        "page_name": "Charts",
        "summary": "Price charts, predictions, and technical analysis visualizations.",
    }
    try:
        from gefion.ui.components.database import get_connection
        with create_span("ui.charts.get_page_context"):
          with get_connection() as conn:
            with conn.cursor() as cur:
                # Data age
                cur.execute("SELECT MAX(date) FROM stock_ohlcv")
                row = cur.fetchone()
                if row and row[0]:
                    data_age = (date.today() - row[0]).days
                    context["data_age_days"] = data_age
                    context["latest_data_date"] = str(row[0])

                # Top movers (biggest absolute % change on latest date)
                cur.execute("""
                    SELECT s.symbol,
                           ROUND(((o.close - o.open) / NULLIF(o.open, 0)) * 100, 2) AS pct_change
                    FROM stock_ohlcv o
                    JOIN stocks s ON o.data_id = s.id
                    WHERE o.date = (SELECT MAX(date) FROM stock_ohlcv)
                    ORDER BY ABS((o.close - o.open) / NULLIF(o.open, 0)) DESC
                    LIMIT 5
                """)
                movers = cur.fetchall()
                if movers:
                    context["top_movers"] = [
                        {"symbol": sym, "pct_change": float(pct)} for sym, pct in movers if pct is not None
                    ]

                # Active model info
                cur.execute("SELECT name, version FROM ml_models WHERE active = true ORDER BY name LIMIT 3")
                models = cur.fetchall()
                if models:
                    context["active_models"] = [{"name": n, "version": v} for n, v in models]

                # Prediction count
                cur.execute("SELECT COUNT(*) FROM predictions WHERE date >= CURRENT_DATE - INTERVAL '7 days'")
                row = cur.fetchone()
                if row:
                    context["recent_prediction_count"] = row[0]
    except Exception:
        pass
    return context


def _build_suggestion_cards(ctx: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build a list of suggestion cards based on current data context.

    Each card has: title, reason, chart_key (used for session_state dispatch).
    """
    cards: List[Dict[str, str]] = []

    # Data freshness suggestion
    data_age = ctx.get("data_age_days")
    if data_age is not None and data_age >= 2:
        cards.append({
            "title": "Pipeline Health Dashboard",
            "reason": f"Your data is {data_age} days old -- check freshness.",
            "chart_key": "suggested_pipeline_health",
        })

    # Top movers suggestion
    movers = ctx.get("top_movers", [])
    if movers:
        top_names = ", ".join(m["symbol"] for m in movers[:3])
        top_pcts = ", ".join(f"{m['pct_change']:+.1f}%" for m in movers[:3])
        cards.append({
            "title": f"Compare {top_names}",
            "reason": f"Biggest movers today: {top_pcts}",
            "chart_key": "suggested_top_movers",
        })

    # Model calibration suggestion
    models = ctx.get("active_models", [])
    if models:
        model_name = models[0]["name"]
        cards.append({
            "title": f"{model_name} Calibration",
            "reason": f"Check calibration for your active model.",
            "chart_key": "suggested_calibration",
        })

    # Always include sector overview
    cards.append({
        "title": "Market Sector Overview",
        "reason": "Compare performance across market sectors.",
        "chart_key": "suggested_sector",
    })

    return cards[:4]


def _render_suggested_charts() -> None:
    """Render AI-suggested chart cards based on data context."""
    ctx = get_page_context()
    cards = _build_suggestion_cards(ctx)

    if not cards:
        st.info("No suggestions available -- load some data first.")
        return

    cols = st.columns(len(cards))
    for i, card in enumerate(cards):
        with cols[i]:
            st.markdown(f"**{card['title']}**")
            st.caption(card["reason"])
            if st.button("View", key=card["chart_key"], type="secondary"):
                st.session_state["_charts_active_suggestion"] = card["chart_key"]

    # Render the selected suggestion inline with close button
    active = st.session_state.get("_charts_active_suggestion")
    if active:
        if active == "suggested_pipeline_health":
            _render_quick_pipeline()
        elif active == "suggested_top_movers":
            _render_top_movers_chart(ctx.get("top_movers", []))
        elif active == "suggested_calibration":
            models = ctx.get("active_models", [])
            if models:
                _render_quick_calibration(models[0]["name"])
        elif active == "suggested_sector":
            _render_quick_sector()

        if st.button("Close", key="close_suggestion"):
            del st.session_state["_charts_active_suggestion"]
            st.rerun()


def _render_top_movers_chart(movers: List[Dict[str, Any]]) -> None:
    """Fetch and render a comparison chart for top movers."""
    symbols = [m["symbol"] for m in movers[:5]]
    if len(symbols) < 2:
        st.warning("Need at least 2 movers to compare.")
        return
    with st.spinner("Loading top movers..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_ohlcv_for_chart
            from gefion.charts.d3.renderers import create_comparison_chart

            with create_span("ui.charts._render_top_movers_chart", symbol_count=len(symbols)):
                start, end = get_period_dates("1 Month")
                symbol_data = {}
                with get_connection() as conn:
                    for sym in symbols:
                        ohlcv = fetch_ohlcv_for_chart(conn, sym, start, end)
                        if ohlcv:
                            symbol_data[sym] = ohlcv
                if len(symbol_data) < 2:
                    st.error("Not enough data for comparison.")
                    return
                html = create_comparison_chart(symbol_data)
                components.html(html, height=500, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_quick_calibration(model_name: str) -> None:
    """Render calibration chart for a specific model."""
    with st.spinner("Loading calibration..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_model_calibration
            from gefion.charts.d3.renderers import create_calibration_chart

            with create_span("ui.charts._render_quick_calibration", model_name=model_name):
                with get_connection() as conn:
                    data = fetch_model_calibration(conn, model_name)
            if not data:
                st.info("No calibration data available.")
                return
            html = create_calibration_chart(data, model_name)
            components.html(html, height=500, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_quick_sector() -> None:
    """Render a quick sector heatmap with default settings."""
    with st.spinner("Loading sector overview..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_ohlcv_for_chart
            from gefion.charts.d3.renderers import create_sector_heatmap

            with create_span("ui.charts._render_quick_sector"):
                start, end = get_period_dates("1 Month")
                sector_data: Dict[str, Dict[str, float]] = {}

                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT symbol, COALESCE(sector, 'Unknown') as sector
                            FROM stocks
                            WHERE status = 'Active' AND sector IS NOT NULL
                            ORDER BY sector, symbol
                        """)
                        rows = cur.fetchall()

                    sector_symbols: Dict[str, List[str]] = {}
                    for symbol, sector in rows:
                        if sector not in sector_symbols:
                            sector_symbols[sector] = []
                        if len(sector_symbols[sector]) < 20:
                            sector_symbols[sector].append(symbol)

                    for sector, symbols in sector_symbols.items():
                        sector_data[sector] = {}
                        for symbol in symbols:
                            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                            if ohlcv and len(ohlcv) >= 2:
                                start_price = ohlcv[0]["close"]
                                end_price = ohlcv[-1]["close"]
                                if start_price > 0:
                                    ret = ((end_price / start_price) - 1) * 100
                                    sector_data[sector][symbol] = ret

                    sector_data = {k: v for k, v in sector_data.items() if v}

                if not sector_data:
                    st.warning("No sector data available.")
                    return

                html = create_sector_heatmap(sector_data)
                components.html(html, height=500, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_quick_charts() -> None:
    """Render one-click quick chart buttons."""
    quick_charts = [
        ("Sector Heatmap", "quick_sector", ":material/grid_view:"),
        ("Top Movers", "quick_movers", ":material/trending_up:"),
        ("Volatility Leaders", "quick_volatility", ":material/show_chart:"),
        ("Pipeline Health", "quick_pipeline", ":material/monitor_heart:"),
    ]

    cols = st.columns(len(quick_charts))
    for i, (label, key, icon) in enumerate(quick_charts):
        with cols[i]:
            if st.button(f"{icon} {label}", key=key, use_container_width=True):
                st.session_state["_charts_quick_active"] = key

    # Render active quick chart with close button
    active = st.session_state.get("_charts_quick_active")
    if active:
        if active == "quick_sector":
            _render_quick_sector()
        elif active == "quick_pipeline":
            _render_quick_pipeline()
        elif active == "quick_movers":
            _render_quick_top_movers()
        elif active == "quick_volatility":
            _render_quick_volatility()

        if st.button("Close", key="close_quick"):
            del st.session_state["_charts_quick_active"]
            st.rerun()


def _render_quick_pipeline() -> None:
    """Render pipeline health chart directly (no Generate button)."""
    with st.spinner("Checking pipeline..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_pipeline_health
            from gefion.charts.d3.renderers import create_pipeline_health_chart

            with create_span("ui.charts._render_quick_pipeline"):
                with get_connection() as conn:
                    data = fetch_pipeline_health(conn)
                html = create_pipeline_health_chart(data)
                components.html(html, height=500, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_quick_top_movers() -> None:
    """Fetch top 5 movers and render comparison chart."""
    with st.spinner("Finding top movers..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_ohlcv_for_chart
            from gefion.charts.d3.renderers import create_comparison_chart

            with create_span("ui.charts._render_quick_top_movers"):
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT s.symbol
                            FROM stock_ohlcv o
                            JOIN stocks s ON o.data_id = s.id
                            WHERE o.date = (SELECT MAX(date) FROM stock_ohlcv)
                            ORDER BY ABS((o.close - o.open) / NULLIF(o.open, 0)) DESC
                            LIMIT 5
                        """)
                        symbols = [row[0] for row in cur.fetchall()]

                    if len(symbols) < 2:
                        st.warning("Not enough data for top movers chart.")
                        return

                    start, end = get_period_dates("1 Month")
                    symbol_data = {}
                    for sym in symbols:
                        ohlcv = fetch_ohlcv_for_chart(conn, sym, start, end)
                        if ohlcv:
                            symbol_data[sym] = ohlcv

                if len(symbol_data) < 2:
                    st.error("Not enough price history for comparison.")
                    return

                html = create_comparison_chart(symbol_data)
                components.html(html, height=500, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_quick_volatility() -> None:
    """Render volatility chart for the most volatile stock."""
    with st.spinner("Finding volatility leaders..."):
        try:
            from gefion.ui.components.database import get_connection
            from gefion.charts.queries import fetch_ohlcv_for_chart
            from gefion.charts.d3.renderers import create_volatility_chart

            with create_span("ui.charts._render_quick_volatility"):
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT s.symbol,
                                   STDDEV((o.close - o.open) / NULLIF(o.open, 0)) AS vol
                            FROM stock_ohlcv o
                            JOIN stocks s ON o.data_id = s.id
                            WHERE o.date >= CURRENT_DATE - INTERVAL '30 days'
                            GROUP BY s.symbol
                            HAVING COUNT(*) >= 5
                            ORDER BY vol DESC
                            LIMIT 1
                        """)
                        row = cur.fetchone()

                    if not row:
                        st.warning("No volatility data available.")
                        return

                    symbol = row[0]
                    start, end = get_period_dates("3 Months")
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data for {symbol}")
                    return

                st.caption(f"Most volatile: **{symbol}**")
                html = create_volatility_chart(ohlcv, symbol, window=20)
                components.html(html, height=650, scrolling=False)
        except Exception as e:
            st.error(f"Error: {e}")


def _render_custom_chart_selector() -> None:
    """Render the full category/type chart selector (original UI)."""
    categories = {
        "Price Analysis": [
            "Price (Candlestick)", "Compare Symbols", "Correlation Matrix",
            "Volatility Analysis", "Drawdown Analysis", "Rolling Returns", "Sector Heatmap",
        ],
        "Model Performance": [
            "Calibration Curve", "Predictions vs Actual", "Confusion Matrix",
            "Accuracy Over Time",
        ],
        "Pipeline Health": [
            "Pipeline Dashboard",
        ],
        "Portfolio": [
            "Portfolio Overview",
        ],
    }

    col1, col2 = st.columns([1, 2])
    with col1:
        category = st.selectbox("Category", list(categories.keys()))
    with col2:
        chart_type = st.selectbox("Chart Type", categories[category])

    st.markdown("---")

    # Price Analysis
    if chart_type == "Price (Candlestick)":
        render_price_chart()
    elif chart_type == "Compare Symbols":
        render_comparison_chart()
    elif chart_type == "Correlation Matrix":
        render_correlation_chart()
    elif chart_type == "Volatility Analysis":
        render_volatility_chart()
    elif chart_type == "Drawdown Analysis":
        render_drawdown_chart()
    elif chart_type == "Rolling Returns":
        render_rolling_chart()
    elif chart_type == "Sector Heatmap":
        render_sector_chart()
    # Model Performance
    elif chart_type == "Calibration Curve":
        render_calibration_chart()
    elif chart_type == "Predictions vs Actual":
        render_pred_vs_actual_chart()
    elif chart_type == "Confusion Matrix":
        render_confusion_matrix_chart()
    elif chart_type == "Accuracy Over Time":
        render_accuracy_chart()
    # Pipeline Health
    elif chart_type == "Pipeline Dashboard":
        render_pipeline_health_chart()
    # Portfolio
    elif chart_type == "Portfolio Overview":
        render_portfolio_chart()


def render_charts() -> None:
    """Render the charts page with three sections."""
    st.markdown("# :material/bar_chart: Charts")
    render_chat_widget(get_page_context())

    # Section 1: AI-Suggested Charts
    st.subheader("Suggested for You")
    _render_suggested_charts()

    st.markdown("---")

    # Section 2: Quick Charts
    st.subheader("Quick Charts")
    _render_quick_charts()

    st.markdown("---")

    # Section 3: Custom Chart (existing UI in expander)
    with st.expander("Custom Chart", expanded=False):
        _render_custom_chart_selector()


def get_period_dates(period: str) -> tuple:
    """Convert period string to start/end dates."""
    end = date.today()
    period_days = {
        "1 Week": 7,
        "1 Month": 30,
        "3 Months": 90,
        "6 Months": 180,
        "1 Year": 365,
        "2 Years": 730,
        "5 Years": 1825,
        "Max": 36500,
    }
    days = period_days.get(period, 365)
    start = end - timedelta(days=days)
    return start, end


def render_price_chart():
    """Render candlestick price chart."""
    st.subheader("Price Chart")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        from gefion.ui.components.database import get_symbols
        symbols = get_symbols()
        symbol = st.selectbox(
            "Symbol",
            symbols if symbols else ["AAPL", "MSFT", "GOOGL"],
            help="Select a stock symbol to chart",
        )

    with col2:
        period = st.selectbox(
            "Period",
            ["1 Month", "3 Months", "6 Months", "1 Year", "2 Years", "5 Years"],
            index=3,
            help="Time period for the chart",
        )

    with col3:
        show_indicators = st.multiselect(
            "Indicators",
            ["SMA 20", "SMA 50", "SMA 200", "Volume"],
            default=["Volume"],
            help="Technical indicators to overlay",
        )

    if st.button("Generate Chart", type="primary", width="stretch"):
        with st.spinner("Generating chart..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_candlestick_chart
                from gefion.charts.analysis import compute_price_insights

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data found for {symbol}")
                    return

                # Compute insights
                insights = compute_price_insights(ohlcv, {})

                # Create chart
                html = create_candlestick_chart(ohlcv, symbol, insights=insights)

                # Display chart
                components.html(html, height=650, scrolling=False)

                # Display insights
                with st.expander("Analysis", expanded=True):
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.metric(
                            "Current Price",
                            f"${insights.get('current_price', 0):.2f}",
                            f"{insights.get('change_1d_pct', 0):+.2f}%"
                        )
                    with col2:
                        st.metric(
                            "Period High",
                            f"${insights.get('period_high', 0):.2f}"
                        )
                    with col3:
                        st.metric(
                            "Period Low",
                            f"${insights.get('period_low', 0):.2f}"
                        )

                    if insights.get("insights"):
                        st.markdown("**Key Insights:**")
                        for insight in insights["insights"][:5]:
                            st.markdown(f"- {insight}")

            except Exception as e:
                st.error(f"Error generating chart: {e}")


def render_comparison_chart():
    """Render multi-symbol comparison chart."""
    st.subheader("Symbol Comparison")

    col1, col2 = st.columns([3, 1])

    with col1:
        from gefion.ui.components.database import get_symbols
        symbols = get_symbols()
        selected = st.multiselect(
            "Symbols to Compare",
            symbols if symbols else ["AAPL", "MSFT", "GOOGL", "AMZN"],
            default=["NVDA", "AMD"] if "NVDA" in symbols and "AMD" in symbols else symbols[:2] if len(symbols) >= 2 else [],
            max_selections=6,
            help="Select 2-6 symbols to compare (normalized to base 100)",
        )

    with col2:
        period = st.selectbox(
            "Period",
            ["1 Month", "3 Months", "6 Months", "1 Year", "2 Years"],
            index=3,
            key="compare_period",
        )

    if len(selected) < 2:
        st.warning("Please select at least 2 symbols to compare.")
        return

    if st.button("Compare", type="primary", width="stretch"):
        with st.spinner("Generating comparison..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_comparison_chart

                start, end = get_period_dates(period)
                symbol_data = {}

                with get_connection() as conn:
                    for symbol in selected:
                        ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                        if ohlcv:
                            symbol_data[symbol] = ohlcv

                if len(symbol_data) < 2:
                    st.error("Need at least 2 symbols with data")
                    return

                html = create_comparison_chart(symbol_data)
                components.html(html, height=500, scrolling=False)

                # Performance summary
                st.subheader("Performance Summary")
                cols = st.columns(len(symbol_data))
                for i, (sym, data) in enumerate(symbol_data.items()):
                    if data:
                        sorted_data = sorted(data, key=lambda x: x["date"])
                        start_price = sorted_data[0]["close"]
                        end_price = sorted_data[-1]["close"]
                        ret = ((end_price / start_price) - 1) * 100
                        with cols[i]:
                            st.metric(sym, f"${end_price:.2f}", f"{ret:+.1f}%")

            except Exception as e:
                st.error(f"Error: {e}")


def render_correlation_chart():
    """Render correlation matrix."""
    st.subheader("🔗 Correlation Matrix")

    st.info("💡 Correlation shows how stocks move together. Values near 1 = move together, near -1 = move opposite, near 0 = independent.")

    from gefion.ui.components.database import get_symbols
    symbols = get_symbols()

    selected = st.multiselect(
        "Symbols",
        symbols if symbols else [],
        default=symbols[:5] if len(symbols) >= 5 else symbols,
        help="Select symbols for correlation analysis",
    )

    period = st.selectbox(
        "Period",
        ["3 Months", "6 Months", "1 Year", "2 Years"],
        index=2,
        key="corr_period",
    )

    if len(selected) < 2:
        st.warning("Select at least 2 symbols.")
        return

    if st.button("Calculate Correlations", type="primary"):
        with st.spinner("Calculating..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_correlation_matrix

                start, end = get_period_dates(period)
                symbol_data = {}

                with get_connection() as conn:
                    for symbol in selected:
                        ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                        if ohlcv:
                            symbol_data[symbol] = ohlcv

                html = create_correlation_matrix(symbol_data)
                components.html(html, height=600, scrolling=False)

            except Exception as e:
                st.error(f"Error: {e}")


def render_volatility_chart():
    """Render volatility analysis chart."""
    st.subheader("📉 Volatility Analysis")

    st.info("💡 Volatility measures price fluctuations. Higher volatility = more risk but also more opportunity.")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        from gefion.ui.components.database import get_symbols
        symbols = get_symbols()
        symbol = st.selectbox(
            "Symbol",
            symbols if symbols else ["AAPL"],
            key="vol_symbol",
        )

    with col2:
        period = st.selectbox(
            "Period",
            ["3 Months", "6 Months", "1 Year", "2 Years"],
            index=2,
            key="vol_period",
        )

    with col3:
        window = st.number_input(
            "Window (days)",
            min_value=5,
            max_value=50,
            value=20,
            help="Lookback period for volatility calculations",
        )

    if st.button("Analyze Volatility", type="primary"):
        with st.spinner("Analyzing..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_volatility_chart

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data for {symbol}")
                    return

                html = create_volatility_chart(ohlcv, symbol, window=window)
                components.html(html, height=650, scrolling=False)

            except Exception as e:
                st.error(f"Error: {e}")


def render_drawdown_chart():
    """Render drawdown analysis chart."""
    st.subheader("📉 Drawdown Analysis")

    st.info("💡 Drawdown shows peak-to-trough decline. Important for understanding downside risk.")

    col1, col2 = st.columns([2, 1])

    with col1:
        from gefion.ui.components.database import get_symbols
        symbols = get_symbols()
        symbol = st.selectbox(
            "Symbol",
            symbols if symbols else ["AAPL"],
            key="dd_symbol",
        )

    with col2:
        period = st.selectbox(
            "Period",
            ["1 Year", "2 Years", "5 Years", "Max"],
            index=1,
            key="dd_period",
        )

    if st.button("Analyze Drawdowns", type="primary"):
        with st.spinner("Analyzing..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_drawdown_chart

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data for {symbol}")
                    return

                html = create_drawdown_chart(ohlcv, symbol)
                components.html(html, height=500, scrolling=False)

            except Exception as e:
                st.error(f"Error: {e}")


def render_rolling_chart():
    """Render rolling returns chart."""
    st.subheader("Rolling Returns")

    st.info("💡 Rolling returns show performance over moving time windows. Useful for comparing consistency.")

    col1, col2 = st.columns([2, 1])

    with col1:
        from gefion.ui.components.database import get_symbols
        symbols = get_symbols()
        selected = st.multiselect(
            "Symbols",
            symbols if symbols else [],
            default=symbols[:2] if len(symbols) >= 2 else [],
            max_selections=4,
            key="roll_symbols",
        )

    with col2:
        period = st.selectbox(
            "Period",
            ["6 Months", "1 Year", "2 Years"],
            index=1,
            key="roll_period",
        )

    windows = st.multiselect(
        "Rolling Windows (days)",
        [20, 30, 60, 90, 120],
        default=[30, 60, 90],
        help="Select time windows for rolling return calculation",
    )

    if not selected:
        st.warning("Select at least 1 symbol.")
        return

    if st.button("Calculate Rolling Returns", type="primary"):
        with st.spinner("Calculating..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_rolling_returns_chart

                start, end = get_period_dates(period)
                symbol_data = {}

                with get_connection() as conn:
                    for symbol in selected:
                        ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                        if ohlcv:
                            symbol_data[symbol] = ohlcv

                html = create_rolling_returns_chart(symbol_data, windows=windows)
                components.html(html, height=500, scrolling=False)

            except Exception as e:
                st.error(f"Error: {e}")


def render_sector_chart():
    """Render sector performance heatmap."""
    st.subheader("🏢 Sector Performance")

    st.info("💡 Sector heatmap shows relative performance across market sectors.")

    period = st.selectbox(
        "Period",
        ["1 Week", "1 Month", "3 Months", "6 Months", "1 Year"],
        index=1,
        key="sector_period",
    )

    limit = st.slider(
        "Max symbols per sector",
        min_value=5,
        max_value=50,
        value=20,
        help="Limit symbols shown per sector",
    )

    if st.button("Generate Heatmap", type="primary"):
        with st.spinner("Generating..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_ohlcv_for_chart
                from gefion.charts.d3.renderers import create_sector_heatmap

                start, end = get_period_dates(period)
                sector_data = {}

                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT symbol, COALESCE(sector, 'Unknown') as sector
                            FROM stocks
                            WHERE status = 'Active' AND sector IS NOT NULL
                            ORDER BY sector, symbol
                        """)
                        rows = cur.fetchall()

                    sector_symbols = {}
                    for symbol, sector in rows:
                        if sector not in sector_symbols:
                            sector_symbols[sector] = []
                        if len(sector_symbols[sector]) < limit:
                            sector_symbols[sector].append(symbol)

                    for sector, symbols in sector_symbols.items():
                        sector_data[sector] = {}
                        for symbol in symbols:
                            ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                            if ohlcv and len(ohlcv) >= 2:
                                start_price = ohlcv[0]["close"]
                                end_price = ohlcv[-1]["close"]
                                if start_price > 0:
                                    ret = ((end_price / start_price) - 1) * 100
                                    sector_data[sector][symbol] = ret

                    sector_data = {k: v for k, v in sector_data.items() if v}

                if not sector_data:
                    st.warning("No sector data available. Make sure stocks have sector information.")
                    return

                html = create_sector_heatmap(sector_data)
                components.html(html, height=500, scrolling=False)

            except Exception as e:
                st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Phase 3: New chart category renderers
# ---------------------------------------------------------------------------


def _get_model_selector():
    """Shared model selector for model performance charts."""
    from gefion.ui.components.database import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, version FROM ml_models WHERE active = true ORDER BY name")
            models = cur.fetchall()
    if not models:
        st.warning("No active models found. Train a model first.")
        return None
    model_opts = [f"{n} {v}" for n, v in models]
    selected = st.selectbox("Model", model_opts)
    return selected.split()[0] if selected else None


def render_calibration_chart():
    """Render model calibration curve."""
    st.subheader("Calibration Curve")
    st.caption("How well do predicted quantile levels match observed coverage?")
    model_name = _get_model_selector()
    if not model_name:
        return

    if st.button("Generate", type="primary", key="gen_calibration"):
        with st.spinner("Computing calibration..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_model_calibration
                from gefion.charts.d3.renderers import create_calibration_chart

                with get_connection() as conn:
                    data = fetch_model_calibration(conn, model_name)
                if not data:
                    st.info("No calibration data — need predictions with matching outcomes.")
                    return
                html = create_calibration_chart(data, model_name)
                components.html(html, height=500, scrolling=False)
            except Exception as e:
                st.error(f"Error: {e}")


def render_pred_vs_actual_chart():
    """Render predicted vs actual scatter plot."""
    st.subheader("Predictions vs Actual")
    st.caption("Scatter plot comparing predicted median returns to actual outcomes.")
    model_name = _get_model_selector()
    if not model_name:
        return

    if st.button("Generate", type="primary", key="gen_pred_actual"):
        with st.spinner("Fetching data..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_predictions_vs_actuals
                from gefion.charts.d3.renderers import create_pred_vs_actual_chart

                with get_connection() as conn:
                    data = fetch_predictions_vs_actuals(conn, model_name)
                if not data:
                    st.info("No prediction-outcome pairs found.")
                    return
                html = create_pred_vs_actual_chart(data, model_name)
                components.html(html, height=500, scrolling=False)
            except Exception as e:
                st.error(f"Error: {e}")


def render_confusion_matrix_chart():
    """Render confusion matrix for trend classifier."""
    st.subheader("Confusion Matrix")
    st.caption("How well does the trend classifier predict actual price movement?")
    model_name = _get_model_selector()
    if not model_name:
        return

    if st.button("Generate", type="primary", key="gen_confusion"):
        with st.spinner("Computing matrix..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_confusion_matrix
                from gefion.charts.d3.renderers import create_confusion_matrix_chart

                with get_connection() as conn:
                    data = fetch_confusion_matrix(conn, model_name)
                html = create_confusion_matrix_chart(data, model_name)
                components.html(html, height=550, scrolling=False)
            except Exception as e:
                st.error(f"Error: {e}")


def render_accuracy_chart():
    """Render accuracy over time chart."""
    st.subheader("Accuracy Over Time")
    st.info("Coming soon — requires accumulated prediction outcome history.")


def render_pipeline_health_chart():
    """Render pipeline health dashboard."""
    st.subheader("Pipeline Health Dashboard")
    st.caption("Data freshness, feature coverage, and prediction status at a glance.")

    if st.button("Generate", type="primary", key="gen_pipeline"):
        with st.spinner("Checking pipeline..."):
            try:
                from gefion.ui.components.database import get_connection
                from gefion.charts.queries import fetch_pipeline_health
                from gefion.charts.d3.renderers import create_pipeline_health_chart

                with get_connection() as conn:
                    data = fetch_pipeline_health(conn)
                html = create_pipeline_health_chart(data)
                components.html(html, height=500, scrolling=False)
            except Exception as e:
                st.error(f"Error: {e}")


def render_portfolio_chart():
    """Render portfolio overview."""
    st.subheader("Portfolio Overview")
    st.info("Coming soon — requires backtest equity curve data.")
