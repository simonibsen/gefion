"""AI chart suggestions — analyze context and suggest/render visualizations.

Used by the Ask Gefion chat widget and the MCP render_chart tool.
"""
from typing import Any, Dict, List, Optional

from gefion.charts.d3.base import render_d3_chart
from gefion.charts.d3.theme import CHART_PALETTE, COLORS


# Generic chart primitives the AI can compose with arbitrary data
CHART_PRIMITIVES = {
    "line": "Single or multi-series line chart",
    "scatter": "Scatter plot with optional color/size encoding",
    "bar": "Vertical bar chart (grouped or stacked)",
    "heatmap": "2D heatmap matrix",
    "histogram": "Distribution histogram",
    "area": "Area chart (filled line)",
    "candlestick": "OHLCV candlestick chart",
    "treemap": "Hierarchical treemap",
}


def suggest_visualization(
    page_context: Dict[str, Any],
    available_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Suggest a chart type and config based on current page context.

    Returns: {"chart_type": str, "params": dict, "reason": str}
    """
    page = page_context.get("page_name", "")
    stats = page_context.get("data_stats", {})
    empty = page_context.get("empty_states", [])

    # Rule-based suggestions
    if page == "ML Pipeline":
        preds = stats.get("prediction_totals", stats.get("predictions", {}))
        if isinstance(preds, dict) and preds.get("quantile"):
            return {
                "chart_type": "predictions",
                "params": {"chart": "pred_vs_actual"},
                "reason": f"You have {preds['quantile']} quantile predictions — see how they compare to actual returns.",
            }
        if isinstance(preds, dict) and preds.get("trend_class"):
            return {
                "chart_type": "confusion_matrix",
                "params": {},
                "reason": f"You have {preds['trend_class']} trend predictions — see the confusion matrix.",
            }

    if page == "Dashboard":
        return {
            "chart_type": "pipeline_health",
            "params": {},
            "reason": "Quick overview of data freshness, feature coverage, and prediction status.",
        }

    if page == "Features":
        return {
            "chart_type": "line",
            "params": {"x": "date", "y": "value", "title": "Feature Values Over Time"},
            "reason": "Visualize how a feature changes over time for a specific symbol.",
        }

    if page == "Backtesting":
        return {
            "chart_type": "portfolio",
            "params": {},
            "reason": "Compare equity curves and risk metrics across strategies.",
        }

    # Default
    return {
        "chart_type": "line",
        "params": {"title": "Custom Visualization"},
        "reason": "Ask a specific question to get a tailored chart.",
    }


def render_generic_chart(
    chart_type: str,
    data: Any,
    config: Optional[Dict[str, Any]] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Render a generic chart from a primitive type + data.

    This is the function called by the MCP render_chart tool.
    The AI picks a chart_type and provides data; we render it.

    Args:
        chart_type: One of CHART_PRIMITIVES keys, or a specific chart template name
        data: Chart data (format depends on chart_type)
        config: Optional config (title, labels, colors, etc.)
        width: Chart width
        height: Chart height

    Returns:
        Self-contained HTML string.
    """
    cfg = config or {}
    cfg.setdefault("palette", CHART_PALETTE)
    cfg.setdefault("colors", COLORS)

    # Map generic primitives to templates
    template_map = {
        "line": "comparison.html",  # multi-line chart
        "scatter": "pred_vs_actual.html",
        "bar": "pipeline_health.html",
        "heatmap": "correlation.html",
        "histogram": "pipeline_health.html",
        "area": "equity_curve.html",
        "candlestick": "candlestick.html",
        "treemap": "sector_heatmap.html",
        # Specific chart types map directly
        "predictions": "predictions.html",
        "calibration": "calibration.html",
        "pred_vs_actual": "pred_vs_actual.html",
        "confusion_matrix": "confusion_matrix.html",
        "pipeline_health": "pipeline_health.html",
        "portfolio": "portfolio.html",
        "accuracy_over_time": "accuracy_over_time.html",
        "volatility": "volatility.html",
        "drawdown": "drawdown.html",
        "rolling_returns": "rolling_returns.html",
        "correlation": "correlation.html",
        "sector_heatmap": "sector_heatmap.html",
        "equity_curve": "equity_curve.html",
        "features": "features.html",
        "comparison": "comparison.html",
    }

    template = template_map.get(chart_type, "base.html")
    return render_d3_chart(template, data, cfg, width, height)
