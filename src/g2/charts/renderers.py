"""
Plotly chart renderers.

Creates interactive Plotly figures for various chart types.
Professional styling with TradingView-inspired color scheme.
"""

from typing import Any, Dict, List, Optional

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None
    make_subplots = None


# Professional color scheme
COLORS = {
    "up": "#26a69a",  # Green for bullish
    "down": "#ef5350",  # Red for bearish
    "price_line": "#2962ff",  # Blue for price line
    "volume_up": "rgba(38, 166, 154, 0.5)",
    "volume_down": "rgba(239, 83, 80, 0.5)",
    "ma_20": "#ff9800",  # Orange for 20 MA
    "ma_50": "#9c27b0",  # Purple for 50 MA
    "ma_200": "#00bcd4",  # Cyan for 200 MA
    "prediction_fill": "rgba(41, 98, 255, 0.15)",
    "prediction_line": "#ff9800",
    "grid": "rgba(128, 128, 128, 0.1)",
    "text": "#333333",
    "background": "#ffffff",
}

# Chart layout defaults
LAYOUT_DEFAULTS = {
    "font": {"family": "Inter, -apple-system, BlinkMacSystemFont, sans-serif", "size": 12},
    "paper_bgcolor": COLORS["background"],
    "plot_bgcolor": COLORS["background"],
    "margin": {"l": 60, "r": 30, "t": 60, "b": 40},
    "hovermode": "x unified",
}


def _check_plotly():
    """Raise ImportError if plotly is not installed."""
    if not PLOTLY_AVAILABLE:
        raise ImportError(
            "plotly is required for charts. "
            "Install with: pip install 'g2[charts]'"
        )


def _compute_moving_averages(closes: List[float], periods: List[int] = [20, 50, 200]) -> Dict[int, List[Optional[float]]]:
    """Compute simple moving averages for given periods."""
    mas = {}
    for period in periods:
        if len(closes) >= period:
            ma = []
            for i in range(len(closes)):
                if i < period - 1:
                    ma.append(None)
                else:
                    ma.append(sum(closes[i - period + 1:i + 1]) / period)
            mas[period] = ma
    return mas


def _detect_areas_of_interest(ohlcv_data: List[Dict]) -> List[Dict[str, Any]]:
    """Detect areas of interest in the price/volume data."""
    if len(ohlcv_data) < 5:
        return []

    areas = []
    closes = [row["close"] for row in ohlcv_data]
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    volumes = [row["volume"] for row in ohlcv_data]
    dates = [row["date"] for row in ohlcv_data]

    avg_volume = sum(volumes) / len(volumes)

    # Period high/low
    max_idx = highs.index(max(highs))
    min_idx = lows.index(min(lows))

    areas.append({
        "type": "high",
        "date": dates[max_idx],
        "price": highs[max_idx],
        "text": f"Period High: ${highs[max_idx]:.2f}",
        "position": "above",
    })

    areas.append({
        "type": "low",
        "date": dates[min_idx],
        "price": lows[min_idx],
        "text": f"Period Low: ${lows[min_idx]:.2f}",
        "position": "below",
    })

    # Volume spikes (>2x average)
    for i, vol in enumerate(volumes):
        if vol > avg_volume * 2.0:
            daily_change = ((closes[i] - closes[i-1]) / closes[i-1] * 100) if i > 0 else 0
            direction = "up" if daily_change >= 0 else "down"
            areas.append({
                "type": "volume_spike",
                "date": dates[i],
                "price": highs[i] if daily_change >= 0 else lows[i],
                "volume": vol,
                "text": f"Volume Spike: {vol/1e6:.1f}M ({vol/avg_volume:.1f}x avg)\n{daily_change:+.1f}% move",
                "position": "above" if daily_change >= 0 else "below",
                "direction": direction,
            })

    # Significant daily moves (>3%)
    for i in range(1, len(closes)):
        daily_change = (closes[i] - closes[i-1]) / closes[i-1] * 100
        if abs(daily_change) > 3.0:
            # Skip if already marked as volume spike
            if any(a["date"] == dates[i] and a["type"] == "volume_spike" for a in areas):
                continue
            areas.append({
                "type": "big_move",
                "date": dates[i],
                "price": highs[i] if daily_change > 0 else lows[i],
                "text": f"Big Move: {daily_change:+.1f}%",
                "position": "above" if daily_change > 0 else "below",
                "direction": "up" if daily_change > 0 else "down",
            })

    # MA crossovers (if we have enough data)
    if len(closes) >= 50:
        sma_20 = []
        sma_50 = []
        for i in range(len(closes)):
            if i >= 19:
                sma_20.append(sum(closes[i-19:i+1]) / 20)
            else:
                sma_20.append(None)
            if i >= 49:
                sma_50.append(sum(closes[i-49:i+1]) / 50)
            else:
                sma_50.append(None)

        # Detect crossovers
        for i in range(50, len(closes)):
            if sma_20[i-1] and sma_50[i-1] and sma_20[i] and sma_50[i]:
                # Golden cross (20 crosses above 50)
                if sma_20[i-1] < sma_50[i-1] and sma_20[i] >= sma_50[i]:
                    areas.append({
                        "type": "golden_cross",
                        "date": dates[i],
                        "price": closes[i],
                        "text": "Golden Cross: SMA20 crossed above SMA50",
                        "position": "below",
                        "direction": "up",
                    })
                # Death cross (20 crosses below 50)
                elif sma_20[i-1] > sma_50[i-1] and sma_20[i] <= sma_50[i]:
                    areas.append({
                        "type": "death_cross",
                        "date": dates[i],
                        "price": closes[i],
                        "text": "Death Cross: SMA20 crossed below SMA50",
                        "position": "above",
                        "direction": "down",
                    })

    return areas


def _add_areas_of_interest(fig, ohlcv_data: List[Dict], row: int = 1) -> None:
    """Add interactive markers for areas of interest on the chart.

    Uses scatter markers with hover text so annotations don't clutter the chart.
    """
    areas = _detect_areas_of_interest(ohlcv_data)
    if not areas:
        return

    # Group by type for different marker styles
    high_low = [a for a in areas if a["type"] in ("high", "low")]
    events = [a for a in areas if a["type"] not in ("high", "low")]

    # Add high/low markers with small labels (these are important to show always)
    for area in high_low:
        color = COLORS["up"] if area["type"] == "high" else COLORS["down"]
        ay = -25 if area["position"] == "above" else 25
        fig.add_annotation(
            x=area["date"],
            y=area["price"],
            text=f"{'▲' if area['type'] == 'high' else '▼'} ${area['price']:.2f}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor=color,
            ax=0,
            ay=ay,
            font={"size": 9, "color": color},
            bgcolor="rgba(255,255,255,0.8)",
            row=row,
            col=1,
        )

    # Add event markers (volume spikes, big moves, crossovers) as hover-only markers
    if events:
        # Separate by direction for coloring
        up_events = [e for e in events if e.get("direction") == "up"]
        down_events = [e for e in events if e.get("direction") == "down"]

        # Add up events (green triangles)
        if up_events:
            fig.add_trace(
                go.Scatter(
                    x=[e["date"] for e in up_events],
                    y=[e["price"] * 1.01 for e in up_events],  # Slightly above price
                    mode="markers",
                    marker={
                        "symbol": "triangle-down",
                        "size": 12,
                        "color": COLORS["up"],
                        "line": {"width": 1, "color": "white"},
                    },
                    name="Bullish Events",
                    text=[e["text"] for e in up_events],
                    hovertemplate="%{text}<extra></extra>",
                    showlegend=False,
                ),
                row=row,
                col=1,
            )

        # Add down events (red triangles)
        if down_events:
            fig.add_trace(
                go.Scatter(
                    x=[e["date"] for e in down_events],
                    y=[e["price"] * 0.99 for e in down_events],  # Slightly below price
                    mode="markers",
                    marker={
                        "symbol": "triangle-up",
                        "size": 12,
                        "color": COLORS["down"],
                        "line": {"width": 1, "color": "white"},
                    },
                    name="Bearish Events",
                    text=[e["text"] for e in down_events],
                    hovertemplate="%{text}<extra></extra>",
                    showlegend=False,
                ),
                row=row,
                col=1,
            )

    # Add vertical lines for major events (crossovers only - they're rare and important)
    crossovers = [a for a in areas if a["type"] in ("golden_cross", "death_cross")]
    for xo in crossovers:
        color = COLORS["up"] if xo["type"] == "golden_cross" else COLORS["down"]
        fig.add_vline(
            x=xo["date"],
            line_width=1,
            line_dash="dot",
            line_color=color,
            opacity=0.5,
            row=row,
            col=1,
        )


def _add_insights_panel(fig, insights: Dict[str, Any], symbol: str) -> None:
    """Add an insights panel to the chart."""
    if not insights:
        return

    # Build insights text
    lines = [f"<b>{symbol} Summary</b>"]

    # Current price and change
    price_range = insights.get("price_range", {})
    if price_range.get("current"):
        change = insights.get("price_change", 0)
        change_color = COLORS["up"] if change >= 0 else COLORS["down"]
        change_str = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
        lines.append(f"Price: <b>${price_range['current']:.2f}</b> (<span style='color:{change_color}'>{change_str}</span>)")

    # Range
    if price_range.get("low") and price_range.get("high"):
        lines.append(f"Range: ${price_range['low']:.2f} - ${price_range['high']:.2f}")

    # Returns
    returns = insights.get("returns", {})
    if returns:
        ret_parts = []
        for period in ["1d", "5d", "1mo", "3mo"]:
            if period in returns:
                r = returns[period]
                ret_parts.append(f"{period}: {r:+.1f}%")
        if ret_parts:
            lines.append(f"Returns: {', '.join(ret_parts)}")

    # Moving averages
    ma = insights.get("moving_averages", {})
    if ma.get("sma_20"):
        above_20 = "▲" if ma.get("above_sma_20") else "▼"
        above_50 = "▲" if ma.get("above_sma_50") else "▼"
        lines.append(f"SMA20: ${ma['sma_20']:.2f} {above_20}  SMA50: ${ma.get('sma_50', 0):.2f} {above_50}")

    # Volatility and volume
    vol = insights.get("volatility")
    avg_vol = insights.get("avg_volume", 0)
    if vol:
        lines.append(f"Volatility: {vol:.0f}% ann. | Avg Vol: {avg_vol/1e6:.1f}M")

    # Key insights (limit to top 4)
    insight_list = insights.get("insights", [])
    if insight_list:
        lines.append("")
        lines.append("<b>Insights</b>")
        for insight in insight_list[:4]:
            lines.append(f"• {insight}")

    # Format as horizontal layout for footer (use columns)
    # Split into two columns for better use of horizontal space
    col1_lines = lines[:len(lines)//2 + 1]
    col2_lines = lines[len(lines)//2 + 1:]

    col1_text = "<br>".join(col1_lines)
    col2_text = "<br>".join(col2_lines) if col2_lines else ""

    # Add as footer below the chart
    fig.add_annotation(
        text=col1_text,
        align="left",
        showarrow=False,
        xref="paper",
        yref="paper",
        x=0.0,
        y=-0.18,  # Below the chart
        xanchor="left",
        yanchor="top",
        bgcolor="rgba(248, 249, 250, 0.95)",
        bordercolor="rgba(128, 128, 128, 0.2)",
        borderwidth=1,
        borderpad=10,
        font={"size": 11, "family": "Arial, sans-serif"},
    )

    if col2_text:
        fig.add_annotation(
            text=col2_text,
            align="left",
            showarrow=False,
            xref="paper",
            yref="paper",
            x=0.52,  # Right column
            y=-0.18,
            xanchor="left",
            yanchor="top",
            bgcolor="rgba(248, 249, 250, 0.95)",
            bordercolor="rgba(128, 128, 128, 0.2)",
            borderwidth=1,
            borderpad=10,
            font={"size": 11, "family": "Arial, sans-serif"},
        )


def create_candlestick_chart(
    ohlcv_data: List[Dict[str, Any]],
    symbol: str,
    title: Optional[str] = None,
    indicators: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    show_ma: bool = True,
    show_annotations: bool = True,
    insights: Optional[Dict[str, Any]] = None,
) -> "go.Figure":
    """
    Create a professional candlestick chart with optional indicators.

    Args:
        ohlcv_data: List of OHLCV dicts with date, open, high, low, close, volume
        symbol: Stock symbol for title
        title: Optional custom title
        indicators: Optional dict mapping indicator name to list of {date, value}
        show_ma: Show moving averages (20, 50, 200 SMA)
        show_annotations: Show high/low price annotations
        insights: Optional insights dict to display on chart

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    # Create figure with secondary y-axis for volume
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.75, 0.25],
    )

    # Extract data
    dates = [row["date"] for row in ohlcv_data]
    opens = [row["open"] for row in ohlcv_data]
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]
    volumes = [row["volume"] for row in ohlcv_data]

    # Add candlestick with professional colors
    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name=symbol,
            increasing={"line": {"color": COLORS["up"]}, "fillcolor": COLORS["up"]},
            decreasing={"line": {"color": COLORS["down"]}, "fillcolor": COLORS["down"]},
            hoverinfo="x+y+text",
        ),
        row=1,
        col=1,
    )

    # Add moving averages
    if show_ma and len(closes) >= 20:
        mas = _compute_moving_averages(closes, [20, 50, 200])
        ma_colors = {20: COLORS["ma_20"], 50: COLORS["ma_50"], 200: COLORS["ma_200"]}
        for period, ma_values in mas.items():
            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=ma_values,
                    mode="lines",
                    name=f"SMA {period}",
                    line={"color": ma_colors.get(period, "#888"), "width": 1.5},
                    hovertemplate=f"SMA{period}: %{{y:.2f}}<extra></extra>",
                ),
                row=1,
                col=1,
            )

    # Add volume bars with color based on price movement
    volume_colors = [
        COLORS["volume_up"] if c >= o else COLORS["volume_down"]
        for o, c in zip(opens, closes)
    ]
    fig.add_trace(
        go.Bar(
            x=dates,
            y=volumes,
            marker_color=volume_colors,
            name="Volume",
            showlegend=False,
            hovertemplate="Vol: %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    # Add indicators if provided
    if indicators:
        indicator_colors = ["#00bcd4", "#e91e63", "#4caf50", "#ff5722"]
        for i, (name, data) in enumerate(indicators.items()):
            ind_dates = [row["date"] for row in data]
            ind_values = [row["value"] for row in data]
            color = indicator_colors[i % len(indicator_colors)]
            fig.add_trace(
                go.Scatter(
                    x=ind_dates,
                    y=ind_values,
                    mode="lines",
                    name=name,
                    line={"color": color, "width": 1.5},
                ),
                row=1,
                col=1,
            )

    # Add areas of interest (high/low, volume spikes, big moves, crossovers)
    if show_annotations and len(ohlcv_data) > 5:
        _add_areas_of_interest(fig, ohlcv_data, row=1)

    # Calculate price change for title
    if closes:
        price_change = ((closes[-1] - closes[0]) / closes[0]) * 100
        change_str = f"+{price_change:.1f}%" if price_change >= 0 else f"{price_change:.1f}%"
        change_color = COLORS["up"] if price_change >= 0 else COLORS["down"]
    else:
        change_str = ""
        change_color = COLORS["text"]

    # Update layout with professional styling
    chart_title = title if title else f"<b>{symbol}</b> <span style='color:{change_color}'>{change_str}</span>"
    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 18}},
        xaxis_rangeslider_visible=False,
        height=650,
        showlegend=True,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(255,255,255,0.8)",
            "bordercolor": "rgba(128,128,128,0.3)",
            "borderwidth": 1,
        },
        **LAYOUT_DEFAULTS,
    )

    # Style axes
    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=COLORS["grid"],
        showline=True,
        linewidth=1,
        linecolor="rgba(128,128,128,0.3)",
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=COLORS["grid"],
        showline=True,
        linewidth=1,
        linecolor="rgba(128,128,128,0.3)",
    )

    fig.update_yaxes(title_text="Price ($)", row=1, col=1, tickprefix="$")
    fig.update_yaxes(title_text="Volume", row=2, col=1)

    # Add insights panel if provided (as footer below chart)
    if insights:
        _add_insights_panel(fig, insights, symbol)
        # Expand bottom margin to fit the footer panel with whitespace
        fig.update_layout(
            height=800,  # Taller to accommodate footer + whitespace
            margin={"l": 60, "r": 40, "t": 60, "b": 220},  # More bottom margin for breathing room
        )

    return fig


def create_prediction_chart(
    ohlcv_data: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    symbol: str,
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a price chart with prediction bands (q10/q50/q90).

    Args:
        ohlcv_data: Historical OHLCV data
        predictions: Prediction data with date, q10, q50, q90
        symbol: Stock symbol for title
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    fig = go.Figure()

    # Historical price line
    dates = [row["date"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=closes,
            mode="lines",
            name="Price",
            line=dict(color="blue", width=2),
        )
    )

    # Prediction bands
    if predictions:
        pred_dates = [row["date"] for row in predictions]
        q10 = [row["q10"] for row in predictions]
        q50 = [row["q50"] for row in predictions]
        q90 = [row["q90"] for row in predictions]

        # Add filled area between q10 and q90
        fig.add_trace(
            go.Scatter(
                x=pred_dates + pred_dates[::-1],
                y=q90 + q10[::-1],
                fill="toself",
                fillcolor="rgba(0, 100, 255, 0.2)",
                line=dict(color="rgba(255,255,255,0)"),
                name="Prediction Range (q10-q90)",
                showlegend=True,
            )
        )

        # Add median prediction line
        fig.add_trace(
            go.Scatter(
                x=pred_dates,
                y=q50,
                mode="lines",
                name="Predicted Median (q50)",
                line=dict(color="orange", width=2, dash="dash"),
            )
        )

    # Update layout
    chart_title = title if title else f"{symbol} Price with Predictions"
    fig.update_layout(
        title=dict(text=chart_title, x=0.5),
        template="plotly_white",
        height=500,
        showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        xaxis_title="Date",
        yaxis_title="Price",
    )

    return fig


def create_equity_curve_chart(
    equity_data: List[Dict[str, Any]],
    title: Optional[str] = None,
    show_drawdown: bool = True,
) -> "go.Figure":
    """
    Create an equity curve chart from backtest results.

    Args:
        equity_data: List of dicts with date, equity, drawdown
        title: Optional custom title
        show_drawdown: Whether to show drawdown subplot

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    dates = [row["date"] for row in equity_data]
    equity = [row["equity"] for row in equity_data]
    drawdown = [row["drawdown"] * 100 for row in equity_data]  # Convert to percentage

    if show_drawdown:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3],
            subplot_titles=("Equity Curve", "Drawdown (%)"),
        )

        # Equity curve
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=equity,
                mode="lines",
                name="Equity",
                line=dict(color="blue", width=2),
            ),
            row=1,
            col=1,
        )

        # Drawdown
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=drawdown,
                mode="lines",
                fill="tozeroy",
                name="Drawdown",
                line=dict(color="red", width=1),
                fillcolor="rgba(255, 0, 0, 0.3)",
            ),
            row=2,
            col=1,
        )

        fig.update_yaxes(title_text="Equity ($)", row=1, col=1)
        fig.update_yaxes(title_text="Drawdown (%)", row=2, col=1)
        fig.update_xaxes(title_text="Date", row=2, col=1)

    else:
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=equity,
                mode="lines",
                name="Equity",
                line=dict(color="blue", width=2),
            )
        )

        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Equity ($)",
        )

    # Update layout
    chart_title = title if title else "Backtest Equity Curve"
    fig.update_layout(
        title=dict(text=chart_title, x=0.5),
        template="plotly_white",
        height=500 if not show_drawdown else 600,
        showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )

    return fig


def create_feature_chart(
    ohlcv_data: List[Dict[str, Any]],
    features: Dict[str, List[Dict[str, Any]]],
    symbol: str,
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a price chart with feature overlays.

    Features are displayed on separate subplots below the price chart.

    Args:
        ohlcv_data: Historical OHLCV data
        features: Dict mapping feature name to list of {date, value}
        symbol: Stock symbol for title
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    n_features = len(features)
    n_rows = 1 + n_features  # Price + one row per feature

    # Calculate row heights
    if n_features > 0:
        price_height = 0.5
        feature_height = 0.5 / n_features
        row_heights = [price_height] + [feature_height] * n_features
    else:
        row_heights = [1.0]

    subplot_titles = [f"{symbol}"] + list(features.keys())

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )

    # Price chart
    dates = [row["date"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=closes,
            mode="lines",
            name="Price",
            line=dict(color="blue", width=2),
        ),
        row=1,
        col=1,
    )

    # Feature subplots
    colors = ["green", "orange", "purple", "red", "cyan"]
    for i, (feature_name, feature_data) in enumerate(features.items()):
        feat_dates = [row["date"] for row in feature_data]
        feat_values = [row["value"] for row in feature_data]
        color = colors[i % len(colors)]

        fig.add_trace(
            go.Scatter(
                x=feat_dates,
                y=feat_values,
                mode="lines",
                name=feature_name,
                line=dict(color=color, width=1.5),
            ),
            row=i + 2,
            col=1,
        )

    # Update layout
    chart_title = title if title else f"{symbol} with Features"
    fig.update_layout(
        title=dict(text=chart_title, x=0.5),
        template="plotly_white",
        height=400 + 150 * n_features,
        showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )

    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_xaxes(title_text="Date", row=n_rows, col=1)

    return fig


def create_comparison_chart(
    symbol_data: Dict[str, List[Dict[str, Any]]],
    title: Optional[str] = None,
    normalize: bool = True,
) -> "go.Figure":
    """
    Create a comparison chart for multiple symbols.

    Shows normalized price performance (base 100) for comparing stocks
    with different price levels.

    Args:
        symbol_data: Dict mapping symbol -> list of {date, close} dicts
        title: Optional custom title
        normalize: Normalize to base 100 (default True)

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    # Colors for different symbols
    symbol_colors = [
        "#2962ff",  # Blue
        "#ff6d00",  # Orange
        "#00c853",  # Green
        "#d500f9",  # Purple
        "#00bcd4",  # Cyan
        "#ff1744",  # Red
    ]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=["Price Performance", "Relative Strength"],
    )

    # Find common date range
    all_dates = set()
    for data in symbol_data.values():
        all_dates.update(row["date"] for row in data)
    common_dates = sorted(all_dates)

    # Track metrics for summary
    performance_metrics = {}

    symbols = list(symbol_data.keys())
    for i, (symbol, data) in enumerate(symbol_data.items()):
        if not data:
            continue

        # Sort by date
        sorted_data = sorted(data, key=lambda x: x["date"])
        dates = [row["date"] for row in sorted_data]
        closes = [row["close"] for row in sorted_data]

        if normalize and closes[0] > 0:
            # Normalize to base 100
            base = closes[0]
            normalized = [(c / base) * 100 for c in closes]
            y_values = normalized
            # Calculate total return
            total_return = ((closes[-1] / closes[0]) - 1) * 100
        else:
            y_values = closes
            total_return = ((closes[-1] / closes[0]) - 1) * 100 if closes[0] > 0 else 0

        performance_metrics[symbol] = {
            "total_return": total_return,
            "start_price": closes[0],
            "end_price": closes[-1],
        }

        color = symbol_colors[i % len(symbol_colors)]

        # Main price/performance line
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=y_values,
                mode="lines",
                name=f"{symbol} ({total_return:+.1f}%)",
                line=dict(color=color, width=2),
                hovertemplate=f"{symbol}<br>Date: %{{x}}<br>Value: %{{y:.2f}}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    # Add relative strength (ratio) if exactly 2 symbols
    if len(symbols) == 2:
        sym1, sym2 = symbols
        data1 = {row["date"]: row["close"] for row in symbol_data[sym1]}
        data2 = {row["date"]: row["close"] for row in symbol_data[sym2]}

        common = sorted(set(data1.keys()) & set(data2.keys()))
        if common:
            ratio_dates = common
            ratios = [data1[d] / data2[d] if data2[d] > 0 else None for d in common]

            # Normalize ratio to base 100
            if ratios[0] and ratios[0] > 0:
                base_ratio = ratios[0]
                norm_ratios = [(r / base_ratio * 100) if r else None for r in ratios]
            else:
                norm_ratios = ratios

            fig.add_trace(
                go.Scatter(
                    x=ratio_dates,
                    y=norm_ratios,
                    mode="lines",
                    name=f"{sym1}/{sym2} Ratio",
                    line=dict(color="#666666", width=1.5),
                    hovertemplate=f"{sym1}/{sym2}<br>Date: %{{x}}<br>Ratio: %{{y:.2f}}<extra></extra>",
                ),
                row=2,
                col=1,
            )

            # Add 100 baseline
            fig.add_hline(
                y=100,
                line_dash="dash",
                line_color="rgba(128,128,128,0.5)",
                row=2,
                col=1,
            )

    # Generate title with performance summary
    if not title:
        perf_parts = [f"{sym}: {m['total_return']:+.1f}%" for sym, m in performance_metrics.items()]
        title = f"<b>Stock Comparison</b> | {' vs '.join(perf_parts)}"

    fig.update_layout(
        title={"text": title, "x": 0.5, "font": {"size": 16}},
        height=650,
        showlegend=True,
        legend={
            "yanchor": "top",
            "y": 0.99,
            "xanchor": "left",
            "x": 0.01,
            "bgcolor": "rgba(255,255,255,0.8)",
        },
        **LAYOUT_DEFAULTS,
    )

    # Style axes
    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=COLORS["grid"],
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor=COLORS["grid"],
    )

    y_label = "Normalized (Base 100)" if normalize else "Price ($)"
    fig.update_yaxes(title_text=y_label, row=1, col=1)
    fig.update_yaxes(title_text="Relative Strength", row=2, col=1)
    fig.update_xaxes(title_text="Date", row=2, col=1)

    return fig


def create_correlation_matrix(
    symbol_data: Dict[str, List[Dict[str, Any]]],
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a correlation heatmap for multiple symbols.

    Args:
        symbol_data: Dict mapping symbol -> list of {date, close} dicts
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()
    import numpy as np

    symbols = list(symbol_data.keys())
    n = len(symbols)

    # Build price series aligned by date
    all_dates = set()
    for data in symbol_data.values():
        all_dates.update(row["date"] for row in data)
    common_dates = sorted(all_dates)

    # Create price matrix
    price_by_date = {sym: {row["date"]: row["close"] for row in data} for sym, data in symbol_data.items()}

    # Calculate daily returns for each symbol
    returns = {sym: [] for sym in symbols}
    for i, d in enumerate(common_dates[1:], 1):
        prev_d = common_dates[i - 1]
        for sym in symbols:
            if d in price_by_date[sym] and prev_d in price_by_date[sym]:
                prev_price = price_by_date[sym][prev_d]
                curr_price = price_by_date[sym][d]
                if prev_price > 0:
                    returns[sym].append((curr_price / prev_price) - 1)
                else:
                    returns[sym].append(None)
            else:
                returns[sym].append(None)

    # Calculate correlation matrix
    corr_matrix = np.zeros((n, n))
    for i, sym1 in enumerate(symbols):
        for j, sym2 in enumerate(symbols):
            r1 = [r for r in returns[sym1] if r is not None]
            r2 = [r for r in returns[sym2] if r is not None]
            # Align returns
            aligned1, aligned2 = [], []
            for k, (a, b) in enumerate(zip(returns[sym1], returns[sym2])):
                if a is not None and b is not None:
                    aligned1.append(a)
                    aligned2.append(b)
            if len(aligned1) > 1:
                corr = np.corrcoef(aligned1, aligned2)[0, 1]
                corr_matrix[i, j] = corr if not np.isnan(corr) else 0
            else:
                corr_matrix[i, j] = 0 if i != j else 1

    # Create heatmap
    fig = go.Figure(data=go.Heatmap(
        z=corr_matrix,
        x=symbols,
        y=symbols,
        colorscale=[
            [0.0, "#d32f2f"],    # Strong negative - red
            [0.25, "#ff8a80"],   # Weak negative - light red
            [0.5, "#ffffff"],    # Zero - white
            [0.75, "#81c784"],   # Weak positive - light green
            [1.0, "#2e7d32"],    # Strong positive - green
        ],
        zmin=-1,
        zmax=1,
        text=[[f"{corr_matrix[i, j]:.2f}" for j in range(n)] for i in range(n)],
        texttemplate="%{text}",
        textfont={"size": 14},
        hovertemplate="%{y} vs %{x}<br>Correlation: %{z:.3f}<extra></extra>",
    ))

    chart_title = title if title else f"<b>Correlation Matrix</b> ({len(symbols)} symbols)"
    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 16}},
        height=400 + 30 * n,
        width=400 + 30 * n,
        xaxis={"side": "bottom"},
        yaxis={"autorange": "reversed"},
        **LAYOUT_DEFAULTS,
    )

    return fig


def create_sector_heatmap(
    sector_data: Dict[str, Dict[str, float]],
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a sector/industry performance heatmap.

    Args:
        sector_data: Dict mapping sector -> {symbol: return_pct}
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    # Flatten data for treemap
    labels = []
    parents = []
    values = []
    colors = []

    for sector, symbols in sector_data.items():
        # Add sector
        labels.append(sector)
        parents.append("")
        sector_avg = sum(symbols.values()) / len(symbols) if symbols else 0
        values.append(abs(sector_avg) + 1)  # Size by magnitude
        colors.append(sector_avg)

        # Add symbols in sector
        for symbol, ret in symbols.items():
            labels.append(f"{symbol}<br>{ret:+.1f}%")
            parents.append(sector)
            values.append(abs(ret) + 1)
            colors.append(ret)

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        marker=dict(
            colors=colors,
            colorscale=[
                [0.0, "#d32f2f"],
                [0.35, "#ff8a80"],
                [0.5, "#f5f5f5"],
                [0.65, "#81c784"],
                [1.0, "#2e7d32"],
            ],
            cmid=0,
            showscale=True,
            colorbar=dict(title="Return %", ticksuffix="%"),
        ),
        textinfo="label",
        hovertemplate="<b>%{label}</b><br>Return: %{color:+.2f}%<extra></extra>",
    ))

    chart_title = title if title else "<b>Sector Performance Heatmap</b>"
    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 16}},
        height=600,
        **LAYOUT_DEFAULTS,
    )

    return fig


def create_volatility_chart(
    ohlcv_data: List[Dict[str, Any]],
    symbol: str,
    window: int = 20,
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a volatility analysis chart.

    Shows price with Bollinger Bands, ATR, and historical volatility.

    Args:
        ohlcv_data: OHLCV data
        symbol: Stock symbol
        window: Lookback window for calculations (default 20)
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()
    import numpy as np

    dates = [row["date"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]
    highs = [row["high"] for row in ohlcv_data]
    lows = [row["low"] for row in ohlcv_data]

    # Calculate returns and volatility
    returns = [0] + [(closes[i] / closes[i-1]) - 1 for i in range(1, len(closes))]

    # Rolling volatility (annualized)
    vol = []
    for i in range(len(returns)):
        if i < window - 1:
            vol.append(None)
        else:
            window_returns = returns[i - window + 1:i + 1]
            std = np.std(window_returns) * np.sqrt(252)
            vol.append(std * 100)  # As percentage

    # Bollinger Bands
    sma = []
    upper_band = []
    lower_band = []
    for i in range(len(closes)):
        if i < window - 1:
            sma.append(None)
            upper_band.append(None)
            lower_band.append(None)
        else:
            window_closes = closes[i - window + 1:i + 1]
            mean = sum(window_closes) / window
            std = np.std(window_closes)
            sma.append(mean)
            upper_band.append(mean + 2 * std)
            lower_band.append(mean - 2 * std)

    # ATR (Average True Range)
    tr = [0]
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i - 1])
        low_close = abs(lows[i] - closes[i - 1])
        tr.append(max(high_low, high_close, low_close))

    atr = []
    for i in range(len(tr)):
        if i < window - 1:
            atr.append(None)
        else:
            atr.append(sum(tr[i - window + 1:i + 1]) / window)

    # Create subplots
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=[f"{symbol} with Bollinger Bands", "Historical Volatility (%)", "ATR"],
    )

    # Price with Bollinger Bands
    fig.add_trace(
        go.Scatter(x=dates, y=upper_band, mode="lines", name="Upper BB",
                   line=dict(color="rgba(128,128,128,0.3)", width=1), showlegend=False),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=lower_band, mode="lines", name="Lower BB",
                   line=dict(color="rgba(128,128,128,0.3)", width=1),
                   fill="tonexty", fillcolor="rgba(128,128,128,0.1)", showlegend=False),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=closes, mode="lines", name="Price",
                   line=dict(color=COLORS["price_line"], width=2)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=sma, mode="lines", name=f"SMA({window})",
                   line=dict(color=COLORS["ma_20"], width=1, dash="dash")),
        row=1, col=1,
    )

    # Volatility
    fig.add_trace(
        go.Scatter(x=dates, y=vol, mode="lines", name="Volatility",
                   line=dict(color="#9c27b0", width=1.5),
                   fill="tozeroy", fillcolor="rgba(156, 39, 176, 0.2)"),
        row=2, col=1,
    )

    # ATR
    fig.add_trace(
        go.Scatter(x=dates, y=atr, mode="lines", name="ATR",
                   line=dict(color="#ff5722", width=1.5),
                   fill="tozeroy", fillcolor="rgba(255, 87, 34, 0.2)"),
        row=3, col=1,
    )

    # Current volatility annotation
    current_vol = next((v for v in reversed(vol) if v is not None), None)
    if current_vol:
        avg_vol = np.mean([v for v in vol if v is not None])
        vol_status = "High" if current_vol > avg_vol * 1.2 else "Low" if current_vol < avg_vol * 0.8 else "Normal"

    chart_title = title if title else f"<b>{symbol} Volatility Analysis</b>"
    if current_vol:
        chart_title += f" | Current: {current_vol:.1f}% ({vol_status})"

    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 16}},
        height=700,
        showlegend=True,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        **LAYOUT_DEFAULTS,
    )

    fig.update_yaxes(title_text="Price ($)", row=1, col=1, tickprefix="$")
    fig.update_yaxes(title_text="Vol %", row=2, col=1, ticksuffix="%")
    fig.update_yaxes(title_text="ATR ($)", row=3, col=1, tickprefix="$")
    fig.update_xaxes(title_text="Date", row=3, col=1)

    return fig


def create_drawdown_chart(
    ohlcv_data: List[Dict[str, Any]],
    symbol: str,
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a drawdown analysis chart.

    Shows price with cumulative high watermark and drawdown percentage.

    Args:
        ohlcv_data: OHLCV data
        symbol: Stock symbol
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    dates = [row["date"] for row in ohlcv_data]
    closes = [row["close"] for row in ohlcv_data]

    # Calculate drawdown
    peak = closes[0]
    peaks = []
    drawdowns = []
    for close in closes:
        peak = max(peak, close)
        peaks.append(peak)
        dd = ((close - peak) / peak) * 100  # As percentage
        drawdowns.append(dd)

    # Find max drawdown
    max_dd = min(drawdowns)
    max_dd_idx = drawdowns.index(max_dd)
    max_dd_date = dates[max_dd_idx]

    # Find drawdown periods (underwater periods)
    underwater_periods = []
    in_dd = False
    dd_start = None
    for i, dd in enumerate(drawdowns):
        if dd < -1 and not in_dd:  # Start of drawdown (>1%)
            in_dd = True
            dd_start = i
        elif dd >= -0.5 and in_dd:  # Recovery
            in_dd = False
            underwater_periods.append({
                "start": dates[dd_start],
                "end": dates[i],
                "min_dd": min(drawdowns[dd_start:i+1]),
                "duration": i - dd_start,
            })

    # Create subplots
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.6, 0.4],
        subplot_titles=[f"{symbol} Price vs Peak", "Drawdown (%)"],
    )

    # Price and peak line
    fig.add_trace(
        go.Scatter(x=dates, y=peaks, mode="lines", name="Peak (High Water Mark)",
                   line=dict(color="rgba(128,128,128,0.5)", width=1, dash="dash")),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=closes, mode="lines", name="Price",
                   line=dict(color=COLORS["price_line"], width=2)),
        row=1, col=1,
    )

    # Drawdown area
    fig.add_trace(
        go.Scatter(x=dates, y=drawdowns, mode="lines", name="Drawdown",
                   line=dict(color=COLORS["down"], width=1.5),
                   fill="tozeroy", fillcolor="rgba(239, 83, 80, 0.3)"),
        row=2, col=1,
    )

    # Mark max drawdown point
    fig.add_trace(
        go.Scatter(x=[max_dd_date], y=[max_dd], mode="markers+text",
                   name=f"Max DD: {max_dd:.1f}%",
                   marker=dict(color=COLORS["down"], size=12, symbol="triangle-down"),
                   text=[f"{max_dd:.1f}%"], textposition="bottom center",
                   textfont=dict(size=12, color=COLORS["down"])),
        row=2, col=1,
    )

    # Add zero line
    fig.add_hline(y=0, line_dash="solid", line_color="rgba(128,128,128,0.5)", row=2, col=1)

    # Summary stats
    avg_dd = sum(drawdowns) / len(drawdowns)
    time_underwater = sum(1 for dd in drawdowns if dd < -1) / len(drawdowns) * 100

    chart_title = title if title else f"<b>{symbol} Drawdown Analysis</b>"
    chart_title += f" | Max: {max_dd:.1f}% | Avg: {avg_dd:.1f}% | Underwater: {time_underwater:.0f}%"

    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 16}},
        height=600,
        showlegend=True,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        **LAYOUT_DEFAULTS,
    )

    fig.update_yaxes(title_text="Price ($)", row=1, col=1, tickprefix="$")
    fig.update_yaxes(title_text="Drawdown %", row=2, col=1, ticksuffix="%")
    fig.update_xaxes(title_text="Date", row=2, col=1)

    return fig


def create_rolling_returns_chart(
    symbol_data: Dict[str, List[Dict[str, Any]]],
    windows: List[int] = [30, 60, 90],
    title: Optional[str] = None,
) -> "go.Figure":
    """
    Create a rolling returns comparison chart.

    Shows rolling returns for multiple time windows across symbols.

    Args:
        symbol_data: Dict mapping symbol -> list of {date, close} dicts
        windows: List of rolling windows in days (default [30, 60, 90])
        title: Optional custom title

    Returns:
        Plotly Figure object
    """
    _check_plotly()

    n_windows = len(windows)
    symbols = list(symbol_data.keys())

    # Create subplots for each window
    fig = make_subplots(
        rows=n_windows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=[f"{w}-Day Rolling Return" for w in windows],
    )

    # Colors for symbols
    symbol_colors = ["#2962ff", "#ff6d00", "#00c853", "#d500f9", "#00bcd4", "#ff1744"]

    for sym_idx, (symbol, data) in enumerate(symbol_data.items()):
        if not data:
            continue

        sorted_data = sorted(data, key=lambda x: x["date"])
        dates = [row["date"] for row in sorted_data]
        closes = [row["close"] for row in sorted_data]
        color = symbol_colors[sym_idx % len(symbol_colors)]

        for win_idx, window in enumerate(windows):
            # Calculate rolling returns
            rolling_ret = []
            for i in range(len(closes)):
                if i < window:
                    rolling_ret.append(None)
                else:
                    ret = ((closes[i] / closes[i - window]) - 1) * 100
                    rolling_ret.append(ret)

            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=rolling_ret,
                    mode="lines",
                    name=f"{symbol}" if win_idx == 0 else None,
                    legendgroup=symbol,
                    showlegend=(win_idx == 0),
                    line=dict(color=color, width=1.5),
                    hovertemplate=f"{symbol}<br>Date: %{{x}}<br>{window}d Return: %{{y:.1f}}%<extra></extra>",
                ),
                row=win_idx + 1,
                col=1,
            )

        # Add zero line to each subplot
        for win_idx in range(n_windows):
            fig.add_hline(y=0, line_dash="dash", line_color="rgba(128,128,128,0.5)",
                         row=win_idx + 1, col=1)

    chart_title = title if title else f"<b>Rolling Returns</b> ({', '.join(symbols)})"
    fig.update_layout(
        title={"text": chart_title, "x": 0.5, "font": {"size": 16}},
        height=250 + 200 * n_windows,
        showlegend=True,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        **LAYOUT_DEFAULTS,
    )

    for i in range(n_windows):
        fig.update_yaxes(title_text="Return %", row=i + 1, col=1, ticksuffix="%")
    fig.update_xaxes(title_text="Date", row=n_windows, col=1)

    return fig
