"""D3 chart renderers — each function returns a self-contained HTML string."""
from typing import Any, Dict, List, Optional

from gefion.charts.d3.base import render_d3_chart
from gefion.charts.d3.theme import COLORS


def create_candlestick_chart(
    ohlcv_data: List[Dict],
    symbol: str,
    title: Optional[str] = None,
    indicators: Optional[List[str]] = None,
    show_ma: bool = True,
    insights: Optional[Dict] = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Render a candlestick chart with volume subplot and moving averages."""
    config = {
        "symbol": symbol,
        "title": title or f"{symbol} Price Chart",
        "show_ma": show_ma,
        "colors": COLORS,
    }
    if insights and insights.get("areas_of_interest"):
        config["annotations"] = insights["areas_of_interest"]

    return render_d3_chart("candlestick.html", ohlcv_data, config, width, height)
