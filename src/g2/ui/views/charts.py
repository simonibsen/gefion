"""Charts page - Interactive visualizations."""

import streamlit as st
from datetime import datetime, timedelta, date
from typing import Optional


def render_charts():
    """Render the charts page."""
    st.markdown("# :material/bar_chart: Charts")

    # Chart type selection
    chart_type = st.selectbox(
        "Chart Type",
        [
            "Price (Candlestick)",
            "Compare Symbols",
            "Correlation Matrix",
            "Volatility Analysis",
            "Drawdown Analysis",
            "Rolling Returns",
            "Sector Heatmap",
        ],
        help="Select the type of chart to generate",
    )

    st.markdown("---")

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
        from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_candlestick_chart
                from g2.charts.analysis import compute_price_insights

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data found for {symbol}")
                    return

                # Compute insights
                insights = compute_price_insights(ohlcv, {})

                # Create chart
                fig = create_candlestick_chart(ohlcv, symbol, insights=insights)

                # Display chart
                st.plotly_chart(fig, use_container_width=True)

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
        from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_comparison_chart

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

                fig = create_comparison_chart(symbol_data)
                st.plotly_chart(fig, use_container_width=True)

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

    from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_correlation_matrix

                start, end = get_period_dates(period)
                symbol_data = {}

                with get_connection() as conn:
                    for symbol in selected:
                        ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                        if ohlcv:
                            symbol_data[symbol] = ohlcv

                fig = create_correlation_matrix(symbol_data)
                st.plotly_chart(fig, use_container_width=True)

            except Exception as e:
                st.error(f"Error: {e}")


def render_volatility_chart():
    """Render volatility analysis chart."""
    st.subheader("📉 Volatility Analysis")

    st.info("💡 Volatility measures price fluctuations. Higher volatility = more risk but also more opportunity.")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_volatility_chart

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data for {symbol}")
                    return

                fig = create_volatility_chart(ohlcv, symbol, window=window)
                st.plotly_chart(fig, use_container_width=True)

            except Exception as e:
                st.error(f"Error: {e}")


def render_drawdown_chart():
    """Render drawdown analysis chart."""
    st.subheader("📉 Drawdown Analysis")

    st.info("💡 Drawdown shows peak-to-trough decline. Important for understanding downside risk.")

    col1, col2 = st.columns([2, 1])

    with col1:
        from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_drawdown_chart

                start, end = get_period_dates(period)

                with get_connection() as conn:
                    ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)

                if not ohlcv:
                    st.error(f"No data for {symbol}")
                    return

                fig = create_drawdown_chart(ohlcv, symbol)
                st.plotly_chart(fig, use_container_width=True)

            except Exception as e:
                st.error(f"Error: {e}")


def render_rolling_chart():
    """Render rolling returns chart."""
    st.subheader("Rolling Returns")

    st.info("💡 Rolling returns show performance over moving time windows. Useful for comparing consistency.")

    col1, col2 = st.columns([2, 1])

    with col1:
        from g2.ui.components.database import get_symbols
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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_rolling_returns_chart

                start, end = get_period_dates(period)
                symbol_data = {}

                with get_connection() as conn:
                    for symbol in selected:
                        ohlcv = fetch_ohlcv_for_chart(conn, symbol, start, end)
                        if ohlcv:
                            symbol_data[symbol] = ohlcv

                fig = create_rolling_returns_chart(symbol_data, windows=windows)
                st.plotly_chart(fig, use_container_width=True)

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
                from g2.ui.components.database import get_connection
                from g2.charts.queries import fetch_ohlcv_for_chart
                from g2.charts.renderers import create_sector_heatmap

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

                fig = create_sector_heatmap(sector_data)
                st.plotly_chart(fig, use_container_width=True)

            except Exception as e:
                st.error(f"Error: {e}")
