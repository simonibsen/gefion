"""D3 chart theme — TradingView-inspired color scheme and styling."""
from typing import Dict, List

# Professional color scheme (matches Plotly renderers.py)
COLORS: Dict[str, str] = {
    "up": "#26a69a",
    "down": "#ef5350",
    "price_line": "#2962ff",
    "volume_up": "rgba(38, 166, 154, 0.5)",
    "volume_down": "rgba(239, 83, 80, 0.5)",
    "ma_20": "#ff9800",
    "ma_50": "#9c27b0",
    "ma_200": "#00bcd4",
    "prediction_fill": "rgba(41, 98, 255, 0.15)",
    "prediction_line": "#ff9800",
    "grid": "rgba(128, 128, 128, 0.1)",
    "text": "#333333",
    "background": "#ffffff",
    "tooltip_bg": "rgba(255, 255, 255, 0.95)",
    "tooltip_border": "#ccc",
    "crosshair": "rgba(128, 128, 128, 0.4)",
}

# Multi-series palette (8+ distinct colors)
CHART_PALETTE: List[str] = [
    "#2962ff", "#ff9800", "#9c27b0", "#00bcd4",
    "#e91e63", "#4caf50", "#ff5722", "#607d8b",
    "#795548", "#009688", "#3f51b5", "#cddc39",
]

FONTS = {
    "family": "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
    "size_title": "16px",
    "size_label": "12px",
    "size_tick": "11px",
    "size_tooltip": "12px",
}

LAYOUT = {
    "margin": {"top": 40, "right": 30, "bottom": 40, "left": 60},
    "volume_height_ratio": 0.2,
    "subplot_gap": 10,
}


def get_css() -> str:
    """Return CSS string with theme variables and base chart styles."""
    return f"""
    :root {{
        --color-up: {COLORS['up']};
        --color-down: {COLORS['down']};
        --color-price: {COLORS['price_line']};
        --color-grid: {COLORS['grid']};
        --color-text: {COLORS['text']};
        --color-bg: {COLORS['background']};
        --font-family: {FONTS['family']};
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: var(--font-family);
        color: var(--color-text);
        background: var(--color-bg);
    }}
    .chart-container {{
        width: 100%;
        position: relative;
    }}
    .chart-title {{
        font-size: {FONTS['size_title']};
        font-weight: 600;
        text-align: center;
        padding: 8px 0;
    }}
    .tooltip {{
        position: absolute;
        background: {COLORS['tooltip_bg']};
        border: 1px solid {COLORS['tooltip_border']};
        border-radius: 4px;
        padding: 6px 10px;
        font-size: {FONTS['size_tooltip']};
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.15s;
        z-index: 100;
        box-shadow: 0 2px 6px rgba(0,0,0,0.1);
        white-space: nowrap;
    }}
    .tooltip.visible {{ opacity: 1; }}
    .axis text {{ font-size: {FONTS['size_tick']}; fill: var(--color-text); }}
    .axis line, .axis path {{ stroke: var(--color-grid); }}
    .grid line {{ stroke: var(--color-grid); stroke-dasharray: 2,2; }}
    /* Smooth transitions on all interactive elements */
    rect, circle, path.line, .ma-line {{ transition: opacity 0.15s ease; }}
    rect:hover, circle:hover {{ filter: brightness(1.15); }}
    .chart-container svg {{ overflow: visible; }}
    /* Annotation styling */
    .annotation-marker {{
        fill: {COLORS['prediction_line']};
        stroke: white; stroke-width: 1.5;
        filter: drop-shadow(0 1px 3px rgba(0,0,0,0.2));
    }}
    .annotation-label {{
        font-size: 10px; fill: {COLORS['text']}; font-weight: 500;
        paint-order: stroke; stroke: white; stroke-width: 2px;
    }}
    """
