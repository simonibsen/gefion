"""
Charts package for data visualization.

Provides Plotly-based interactive charts for:
- Price candlestick charts with indicators
- Prediction visualization with confidence bands
- Backtest equity curves
- Feature overlays on price data
"""

from gefion.charts.queries import (
    fetch_ohlcv_for_chart,
    fetch_predictions_for_chart,
    fetch_features_for_chart,
    fetch_backtest_equity_curve,
)
from gefion.charts.renderers import (
    create_candlestick_chart,
    create_prediction_chart,
    create_equity_curve_chart,
    create_feature_chart,
)
from gefion.charts.analysis import (
    compute_price_insights,
    compute_prediction_insights,
    compute_backtest_insights,
    detect_technical_signals,
)
from gefion.charts.output import (
    get_chart_output_dir,
    save_chart_html,
    open_in_browser,
    generate_chart_filename,
)

__all__ = [
    # Queries
    "fetch_ohlcv_for_chart",
    "fetch_predictions_for_chart",
    "fetch_features_for_chart",
    "fetch_backtest_equity_curve",
    # Renderers
    "create_candlestick_chart",
    "create_prediction_chart",
    "create_equity_curve_chart",
    "create_feature_chart",
    # Analysis
    "compute_price_insights",
    "compute_prediction_insights",
    "compute_backtest_insights",
    "detect_technical_signals",
    # Output
    "get_chart_output_dir",
    "save_chart_html",
    "open_in_browser",
    "generate_chart_filename",
]
