"""D3 chart renderers — each function returns a self-contained HTML string.

Drop-in replacements for the Plotly renderers in gefion.charts.renderers.
Same function signatures, but return HTML strings instead of go.Figure objects.
"""
from typing import Any, Dict, List, Optional

from gefion.charts.d3.base import render_d3_chart
from gefion.charts.d3.theme import COLORS, CHART_PALETTE
from gefion.observability import create_span, set_attributes


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
    """Candlestick chart with volume subplot, moving averages, and annotations."""
    with create_span("charts.d3.candlestick", symbol=symbol):
        config = {
            "symbol": symbol,
            "title": title or f"{symbol} Price Chart",
            "show_ma": show_ma,
            "colors": COLORS,
        }
        if insights and insights.get("areas_of_interest"):
            config["annotations"] = insights["areas_of_interest"]
        return render_d3_chart("candlestick.html", ohlcv_data, config, width, height)


def create_prediction_chart(
    ohlcv_data: List[Dict],
    predictions: List[Dict],
    symbol: str,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Price line with q10/q50/q90 prediction bands."""
    with create_span("charts.d3.predictions", symbol=symbol):
        # Merge OHLCV and predictions by date
        pred_by_date = {p["date"]: p for p in predictions} if predictions else {}
        merged = []
        for row in ohlcv_data:
            entry = {"date": row["date"], "close": row.get("close", row.get("price"))}
            pred = pred_by_date.get(row["date"], {})
            entry["q10"] = pred.get("q10")
            entry["q50"] = pred.get("q50")
            entry["q90"] = pred.get("q90")
            merged.append(entry)

        config = {
            "symbol": symbol,
            "title": title or f"{symbol} Predictions",
            "colors": COLORS,
        }
        return render_d3_chart("predictions.html", merged, config, width, height)


def create_equity_curve_chart(
    equity_data: List[Dict],
    title: Optional[str] = None,
    show_drawdown: bool = True,
    width: int = 800,
    height: int = 500,
) -> str:
    """Equity curve with optional drawdown subplot."""
    config = {
        "title": title or "Equity Curve",
        "show_drawdown": show_drawdown,
        "colors": COLORS,
    }
    return render_d3_chart("equity_curve.html", equity_data, config, width, height)


def create_feature_chart(
    ohlcv_data: List[Dict],
    features: Dict[str, List[Dict]],
    symbol: str,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 700,
) -> str:
    """Price chart with feature overlay subplots."""
    data = {
        "ohlcv": ohlcv_data,
        "features": features,
    }
    feature_names = list(features.keys())
    # Scale height based on number of features
    adjusted_height = max(height, 300 + len(feature_names) * 120)
    config = {
        "symbol": symbol,
        "title": title or f"{symbol} Features",
        "feature_names": feature_names,
        "palette": CHART_PALETTE,
        "colors": COLORS,
    }
    return render_d3_chart("features.html", data, config, width, adjusted_height)


def create_comparison_chart(
    symbol_data: Dict[str, List[Dict]],
    title: Optional[str] = None,
    normalize: bool = True,
    width: int = 800,
    height: int = 500,
) -> str:
    """Multi-symbol normalized price comparison."""
    with create_span("charts.d3.comparison", symbol_count=len(symbol_data)):
        symbols = []
        for sym, rows in symbol_data.items():
            symbols.append({"symbol": sym, "data": rows})

        data = {"symbols": symbols}
        config = {
            "title": title or "Price Comparison",
            "normalize": normalize,
            "palette": CHART_PALETTE,
        }
        return render_d3_chart("comparison.html", data, config, width, height)


def create_correlation_matrix(
    symbol_data: Dict[str, List[Dict]],
    title: Optional[str] = None,
    width: int = 600,
    height: int = 600,
) -> str:
    """Correlation heatmap matrix."""
    with create_span("charts.d3.correlation", symbol_count=len(symbol_data)):
        return _compute_correlation_matrix(symbol_data, title, width, height)


def _compute_correlation_matrix(
    symbol_data: Dict[str, List[Dict]],
    title: Optional[str] = None,
    width: int = 600,
    height: int = 600,
) -> str:
    import math

    symbols = sorted(symbol_data.keys())
    # Compute returns for each symbol
    returns = {}
    for sym, rows in symbol_data.items():
        closes = [r["close"] for r in sorted(rows, key=lambda x: x["date"])]
        rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))] if len(closes) > 1 else []
        returns[sym] = rets

    # Compute correlation matrix
    n = len(symbols)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            r1, r2 = returns.get(symbols[i], []), returns.get(symbols[j], [])
            min_len = min(len(r1), len(r2))
            if min_len < 2:
                matrix[i][j] = 1.0 if i == j else 0.0
                continue
            r1, r2 = r1[:min_len], r2[:min_len]
            mean1, mean2 = sum(r1)/min_len, sum(r2)/min_len
            cov = sum((a - mean1) * (b - mean2) for a, b in zip(r1, r2)) / min_len
            std1 = math.sqrt(sum((a - mean1)**2 for a in r1) / min_len)
            std2 = math.sqrt(sum((b - mean2)**2 for b in r2) / min_len)
            matrix[i][j] = cov / (std1 * std2) if std1 > 0 and std2 > 0 else 0.0

    data = {"symbols": symbols, "matrix": matrix}
    config = {"title": title or "Correlation Matrix"}
    return render_d3_chart("correlation.html", data, config, width, height)


def create_sector_heatmap(
    sector_data: Dict[str, Dict[str, float]],
    title: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Treemap-style sector performance visualization."""
    with create_span("charts.d3.sector_heatmap"):
        sectors = []
        for sector_name, symbols in sector_data.items():
            sym_list = [{"symbol": s, "return_pct": r} for s, r in symbols.items()]
            avg_return = sum(symbols.values()) / len(symbols) if symbols else 0
            sectors.append({"name": sector_name, "return_pct": avg_return, "symbols": sym_list})

        data = {"sectors": sectors}
        config = {"title": title or "Sector Performance"}
        return render_d3_chart("sector_heatmap.html", data, config, width, height)


def create_volatility_chart(
    ohlcv_data: List[Dict],
    symbol: str,
    window: int = 20,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Bollinger Bands + ATR + Historical Volatility subplots."""
    with create_span("charts.d3.volatility", symbol=symbol, window=window):
        import math

        # Compute Bollinger Bands, ATR, and HV from OHLCV
        closes = [d["close"] for d in ohlcv_data]
        enriched = []
        for i, d in enumerate(ohlcv_data):
            entry = dict(d)
            if i >= window - 1:
                window_closes = closes[i - window + 1:i + 1]
                mean = sum(window_closes) / window
                std = math.sqrt(sum((c - mean)**2 for c in window_closes) / window)
                entry["bb_upper"] = mean + 2 * std
                entry["bb_middle"] = mean
                entry["bb_lower"] = mean - 2 * std

                # Historical volatility (annualized)
                if i >= window:
                    rets = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(i - window + 1, i + 1) if closes[j-1] != 0]
                    if rets:
                        ret_mean = sum(rets) / len(rets)
                        ret_std = math.sqrt(sum((r - ret_mean)**2 for r in rets) / len(rets))
                        entry["hv"] = ret_std * math.sqrt(252) * 100
                    else:
                        entry["hv"] = None
                else:
                    entry["hv"] = None

                # ATR
                if i >= 1:
                    tr = max(d["high"] - d["low"],
                             abs(d["high"] - ohlcv_data[i-1]["close"]),
                             abs(d["low"] - ohlcv_data[i-1]["close"]))
                    entry["atr"] = tr
                else:
                    entry["atr"] = d["high"] - d["low"]
            enriched.append(entry)

        config = {
            "symbol": symbol,
            "title": title or f"{symbol} Volatility Analysis",
            "window": window,
            "colors": COLORS,
        }
        return render_d3_chart("volatility.html", enriched, config, width, height)


def create_drawdown_chart(
    ohlcv_data: List[Dict],
    symbol: str,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Price + drawdown area subplot."""
    with create_span("charts.d3.drawdown", symbol=symbol):
        # Compute drawdown from OHLCV
        closes = [d["close"] for d in ohlcv_data]
        peak = closes[0] if closes else 0
        enriched = []
        for i, d in enumerate(ohlcv_data):
            entry = dict(d)
            if closes[i] > peak:
                peak = closes[i]
            entry["drawdown"] = ((closes[i] - peak) / peak * 100) if peak > 0 else 0
            enriched.append(entry)

        config = {
            "symbol": symbol,
            "title": title or f"{symbol} Drawdown Analysis",
            "colors": COLORS,
        }
        return render_d3_chart("drawdown.html", enriched, config, width, height)


def create_rolling_returns_chart(
    symbol_data: Dict[str, List[Dict]],
    windows: Optional[List[int]] = None,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Multi-window rolling returns chart."""
    if windows is None:
        windows = [20, 60, 120]

    # Compute rolling returns for the first symbol (or all)
    all_windows = {}
    for sym, rows in symbol_data.items():
        sorted_rows = sorted(rows, key=lambda x: x["date"])
        closes = [r["close"] for r in sorted_rows]
        dates = [r["date"] for r in sorted_rows]
        for w in windows:
            key = str(w)
            if key not in all_windows:
                all_windows[key] = []
            for i in range(w, len(closes)):
                ret = (closes[i] - closes[i - w]) / closes[i - w] * 100 if closes[i - w] != 0 else 0
                all_windows[key].append({"date": dates[i], "value": ret})
        break  # Only first symbol for now

    data = {"windows": all_windows}
    window_labels = {str(w): f"{w}-day" for w in windows}
    config = {
        "title": title or "Rolling Returns",
        "window_labels": window_labels,
        "palette": CHART_PALETTE,
    }
    return render_d3_chart("rolling_returns.html", data, config, width, height)


# --- Aliases for alternative calling conventions ---

def create_sector_heatmap_d3(
    data: Dict[str, Any],
    title: Optional[str] = None,
    period: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Sector heatmap — accepts pre-structured data dict with sectors list."""
    config = {"title": title or "Sector Performance"}
    if period:
        config["period"] = period
    return render_d3_chart("sector_heatmap.html", data, config, width, height)


def create_rolling_returns_d3(
    data: Dict[str, Any],
    symbol: str,
    title: Optional[str] = None,
    window_labels: Optional[Dict[str, str]] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Rolling returns — accepts pre-computed window data dict."""
    config = {
        "symbol": symbol,
        "title": title or f"{symbol} Rolling Returns",
        "window_labels": window_labels or {},
        "palette": CHART_PALETTE,
    }
    return render_d3_chart("rolling_returns.html", data, config, width, height)


def create_features_chart(
    data: Dict[str, Any],
    symbol: str,
    title: Optional[str] = None,
    width: int = 800,
    height: int = 700,
) -> str:
    """Features chart — accepts pre-structured data dict with ohlcv + features."""
    feature_names = list(data.get("features", {}).keys())
    adjusted_height = max(height, 300 + len(feature_names) * 120)
    config = {
        "symbol": symbol,
        "title": title or f"{symbol} Features",
        "feature_names": feature_names,
        "palette": CHART_PALETTE,
        "colors": COLORS,
    }
    return render_d3_chart("features.html", data, config, width, adjusted_height)
def create_sector_heatmap_d3(
    sector_data: Dict[str, Any],
    title: Optional[str] = None,
    period: Optional[str] = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Render a treemap-style sector heatmap from pre-structured sector data.

    Args:
        sector_data: Dict with 'sectors' list containing name, return_pct, symbols.
        title: Chart title.
        period: Period label (e.g. '1M', '1Y').
        width: Chart width in pixels.
        height: Chart height in pixels.
    """
    config: Dict[str, Any] = {
        "title": title or "Sector Performance",
    }
    if period:
        config["period"] = period
    return render_d3_chart("sector_heatmap.html", sector_data, config, width, height)


def create_rolling_returns_d3(
    rolling_data: Dict[str, Any],
    symbol: str,
    title: Optional[str] = None,
    window_labels: Optional[Dict[str, str]] = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Render a multi-line rolling returns chart from pre-computed window data.

    Args:
        rolling_data: Dict with 'windows' mapping window keys to [{date, value}].
        symbol: Symbol name for display.
        title: Chart title.
        window_labels: Mapping of window key to display label.
        width: Chart width in pixels.
        height: Chart height in pixels.
    """
    config: Dict[str, Any] = {
        "symbol": symbol,
        "title": title or f"{symbol} Rolling Returns",
    }
    if window_labels:
        config["window_labels"] = window_labels
    return render_d3_chart("rolling_returns.html", rolling_data, config, width, height)


# ---------------------------------------------------------------------------
# Phase 3: New chart category renderers
# ---------------------------------------------------------------------------


def create_calibration_chart(
    calibration_data: List[Dict],
    model_name: str = "",
    title: Optional[str] = None,
    width: int = 500,
    height: int = 500,
) -> str:
    """Calibration curve: predicted probability vs observed frequency."""
    with create_span("charts.d3.calibration", model_name=model_name):
        config = {"title": title or f"Calibration — {model_name}", "model_name": model_name}
        return render_d3_chart("calibration.html", calibration_data, config, width, height)


def create_pred_vs_actual_chart(
    data: List[Dict],
    model_name: str = "",
    title: Optional[str] = None,
    width: int = 600,
    height: int = 500,
) -> str:
    """Scatter plot: predicted return vs actual return."""
    config = {"title": title or f"Predicted vs Actual — {model_name}", "model_name": model_name}
    return render_d3_chart("pred_vs_actual.html", data, config, width, height)


def create_confusion_matrix_chart(
    data: Dict[str, Any],
    model_name: str = "",
    title: Optional[str] = None,
    width: int = 550,
    height: int = 550,
) -> str:
    """Confusion matrix heatmap for trend classifier."""
    config = {"title": title or f"Confusion Matrix — {model_name}", "model_name": model_name}
    return render_d3_chart("confusion_matrix.html", data, config, width, height)


def create_pipeline_health_chart(
    data: Dict[str, Any],
    title: Optional[str] = None,
    width: int = 800,
    height: int = 500,
) -> str:
    """Multi-panel pipeline health dashboard."""
    with create_span("charts.d3.pipeline_health"):
        config = {"title": title or "Pipeline Health"}
        return render_d3_chart("pipeline_health.html", data, config, width, height)


def create_accuracy_over_time_chart(
    data: List[Dict],
    model_name: str = "",
    title: Optional[str] = None,
    width: int = 800,
    height: int = 400,
) -> str:
    """Line chart showing model accuracy metrics over time."""
    config = {"title": title or f"Accuracy Over Time — {model_name}", "model_name": model_name}
    return render_d3_chart("accuracy_over_time.html", data, config, width, height)


def create_portfolio_chart(
    data: Dict[str, Any],
    strategy_name: str = "",
    title: Optional[str] = None,
    width: int = 900,
    height: int = 500,
) -> str:
    """Enhanced equity curve with risk metrics panel."""
    config = {"title": title or f"Portfolio — {strategy_name}", "strategy_name": strategy_name}
    return render_d3_chart("portfolio.html", data, config, width, height)


# ---------------------------------------------------------------------------
# Experiment charts
# ---------------------------------------------------------------------------


def create_experiment_trials(
    trials: List[Dict],
    title: Optional[str] = None,
    width: int = 700,
    height: int = 400,
) -> str:
    """Trial performance scatter: trial number vs score, colored by promoted/rejected."""
    config = {"title": title or "Experiment Trials"}
    return render_d3_chart("experiment_trials.html", trials, config, width, height)


def create_experiment_fdr(
    experiments: List[Dict],
    fdr_rate: float = 0.10,
    title: Optional[str] = None,
    width: int = 700,
    height: int = 400,
) -> str:
    """FDR cycle summary: p-values with threshold line, promoted/rejected markers."""
    config = {"title": title or "FDR Cycle Summary", "fdr_rate": fdr_rate}
    return render_d3_chart("experiment_fdr.html", experiments, config, width, height)


def create_experiment_heatmap(
    data: List[Dict],
    x_label: str = "Parameter 1",
    y_label: str = "Parameter 2",
    title: Optional[str] = None,
    width: int = 600,
    height: int = 500,
) -> str:
    """Parameter sensitivity heatmap for 2-parameter experiments."""
    config = {"title": title or "Parameter Sensitivity", "x_label": x_label, "y_label": y_label}
    return render_d3_chart("experiment_heatmap.html", data, config, width, height)


def create_experiment_features(
    features: List[Dict],
    title: Optional[str] = None,
    width: int = 700,
    height: int = 400,
) -> str:
    """Feature importance before/after: grouped bar chart."""
    config = {"title": title or "Feature Importance: Before vs After"}
    return render_d3_chart("experiment_features.html", features, config, width, height)
