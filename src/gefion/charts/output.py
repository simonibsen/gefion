"""
Output functions for saving charts and opening in browser.
"""

import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import plotly.graph_objects as go


def get_chart_output_dir() -> Path:
    """
    Get the chart output directory.

    Uses G2_CHART_DIR environment variable if set,
    otherwise defaults to ~/.gefion/charts/

    Returns:
        Path to chart output directory (created if needed)
    """
    env_dir = os.getenv("G2_CHART_DIR")
    if env_dir:
        output_dir = Path(env_dir)
    else:
        output_dir = Path.home() / ".gefion" / "charts"

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def generate_chart_filename(symbol: str, chart_type: str) -> str:
    """
    Generate a unique filename for a chart.

    Args:
        symbol: Stock symbol (e.g., 'AAPL')
        chart_type: Type of chart (price, predictions, backtest, features)

    Returns:
        Filename like 'AAPL_price_20241230_143052.html'
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{symbol}_{chart_type}_{timestamp}.html"


def save_chart_html(fig: "go.Figure", filename: str) -> Path:
    """
    Save a Plotly figure as HTML.

    Args:
        fig: Plotly Figure object
        filename: Filename to save as

    Returns:
        Full path to saved file
    """
    output_dir = get_chart_output_dir()
    output_path = output_dir / filename
    fig.write_html(str(output_path), include_plotlyjs=True, full_html=True)
    return output_path


def save_html_string(html: str, filename: str) -> Path:
    """Save a raw HTML string to the chart output directory.

    Args:
        html: Self-contained HTML string (e.g., D3 chart)
        filename: Filename to save as

    Returns:
        Full path to saved file
    """
    output_dir = get_chart_output_dir()
    output_path = output_dir / filename
    output_path.write_text(html)
    return output_path


def open_in_browser(path: Path) -> None:
    """
    Open a file in the default browser.

    Args:
        path: Path to file to open
    """
    webbrowser.open(f"file://{path.absolute()}")
