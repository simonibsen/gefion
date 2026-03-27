"""
Indicator metadata and legacy API support.

Indicators are now regular features computed via the features dispatcher.
This module only contains metadata (INDICATOR_FUNCTIONS dict) for validation
and legacy AlphaVantage API fetching support.

For indicator computation, see:
- gefion.features.indicators.compute_indicators() - Local computation from OHLCV data
- gefion.features.dispatcher - Generic feature computation dispatcher
- gefion features-compute --function-names indicator - CLI command
"""
from __future__ import annotations

from gefion.alphavantage import indicators as indicator_parsers


# Indicator metadata for validation and legacy AlphaVantage API support
# Maps indicator name to (API_FUNCTION, parser, api_params)
INDICATOR_FUNCTIONS = {
    "rsi": (
        "RSI",
        indicator_parsers.parse_rsi,
        {"interval": "daily", "time_period": "14", "series_type": "close"},
    ),
    "macd": (
        "MACD",
        indicator_parsers.parse_macd,
        {
            "interval": "daily",
            "series_type": "close",
            "fastperiod": "12",
            "slowperiod": "26",
            "signalperiod": "9",
        },
    ),
    "sma20": (
        "SMA",
        lambda p: indicator_parsers.parse_sma(p, 20),
        {"interval": "daily", "time_period": "20", "series_type": "close"},
    ),
    "bbands": (
        "BBANDS",
        indicator_parsers.parse_bbands,
        {
            "interval": "daily",
            "time_period": "20",
            "series_type": "close",
            "nbdevup": "2",
            "nbdevdn": "2",
            "matype": "0",
        },
    ),
    "adx": (
        "ADX",
        indicator_parsers.parse_adx,
        {"interval": "daily", "time_period": "14"},
    ),
    "stoch": (
        "STOCH",
        indicator_parsers.parse_stoch,
        {
            "interval": "daily",
            "fastkperiod": "14",
            "slowkperiod": "3",
            "slowdperiod": "3",
            "slowkmatype": "1",
            "slowdmatype": "1",
        },
    ),
    "sma50": (
        "SMA",
        lambda p: indicator_parsers.parse_sma(p, 50),
        {"interval": "daily", "time_period": "50", "series_type": "close"},
    ),
    "sma200": (
        "SMA",
        lambda p: indicator_parsers.parse_sma(p, 200),
        {"interval": "daily", "time_period": "200", "series_type": "close"},
    ),
    "ema12": (
        "EMA",
        lambda p: indicator_parsers.parse_ema(p, 12),
        {"interval": "daily", "time_period": "12", "series_type": "close"},
    ),
    "ema26": (
        "EMA",
        lambda p: indicator_parsers.parse_ema(p, 26),
        {"interval": "daily", "time_period": "26", "series_type": "close"},
    ),
    "psar": (
        "SAR",
        indicator_parsers.parse_psar,
        {"interval": "daily", "acceleration": "0.02", "maximum": "0.2"},
    ),
}
