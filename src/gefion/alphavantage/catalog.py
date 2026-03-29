"""
AlphaVantage endpoint catalog and simple parsers for demo payloads.

We avoid network calls here; tests rely on documented demo responses.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional

Endpoint = Dict[str, object]

# Minimal catalog of supported endpoints and required params.
ENDPOINTS: Dict[str, Endpoint] = {
    "TIME_SERIES_DAILY_ADJUSTED": {
        "params": {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": "<symbol>",
            "outputsize": "compact",  # caller may override
        },
        "demo": "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol=IBM&apikey=demo",
    },
    "CPI": {
        "params": {
            "function": "CPI",
            "interval": "monthly",
        },
        "demo": "https://www.alphavantage.co/query?function=CPI&interval=monthly&apikey=demo",
    },
    "LISTING_STATUS": {
        "params": {
            "function": "LISTING_STATUS",
        },
        "demo": "https://www.alphavantage.co/query?function=LISTING_STATUS&apikey=demo",
    },
}


def parse_daily_adjusted(symbol: str, payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse TIME_SERIES_DAILY_ADJUSTED into normalized rows."""
    series = payload.get("Time Series (Daily)", {})
    if not isinstance(series, Mapping):
        return []

    rows: List[Dict[str, object]] = []
    for date_str, values in series.items():
        try:
            rows.append(
                {
                    "symbol": symbol,
                    "date": date_str,
                    "open": float(values.get("1. open")),
                    "high": float(values.get("2. high")),
                    "low": float(values.get("3. low")),
                    "close": float(values.get("4. close")),
                    "adjusted_close": float(values.get("5. adjusted close")),
                    "volume": int(values.get("6. volume")),
                    "dividend_amount": float(values.get("7. dividend amount")),
                    "split_coefficient": float(values.get("8. split coefficient")),
                }
            )
        except (TypeError, ValueError, AttributeError):
            # Skip rows with missing/invalid data
            continue
    return rows


def parse_cpi_monthly(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse CPI monthly series from AlphaVantage."""
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []

    rows: List[Dict[str, object]] = []
    for entry in data:
        try:
            rows.append(
                {
                    "date": entry["date"],
                    "value": float(entry["value"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def parse_listing_status(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse LISTING_STATUS CSV-like payload into dicts."""
    data = payload.get("data") or payload.get("rows") or payload.get("listing") or payload.get("listings")
    if data is None:
        # AlphaVantage returns CSV; callers should pre-parse to rows of dicts.
        return []
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, object]] = []
    for entry in data:
        # Expect keys: symbol, name, exchange, assetType, ipoDate, delistingDate, status
        try:
            rows.append(
                {
                    "symbol": entry["symbol"],
                    "name": entry.get("name"),
                    "exchange": entry.get("exchange"),
                    "asset_type": entry.get("assetType"),
                    "ipo_date": entry.get("ipoDate"),
                    "delisting_date": entry.get("delistingDate"),
                    "status": entry.get("status"),
                }
            )
        except (TypeError, KeyError):
            continue
    return rows


def _safe_float(val):
    """Convert to float, returning None for 'None', '-', empty, or invalid."""
    if val is None or val == "None" or val == "-" or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    """Convert to int, returning None for invalid."""
    if val is None or val == "None" or val == "-" or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None


def parse_overview(overview: Mapping[str, object]) -> Dict[str, object]:
    """Parse OVERVIEW response into a normalized fundamentals dict.

    Extracts company metadata (name, sector, industry) and 13 numeric
    fundamental fields for the stocks_fundamentals table.
    """
    return {
        "symbol": overview.get("Symbol"),
        "name": overview.get("Name") or None,
        "sector": overview.get("Sector") or None,
        "industry": overview.get("Industry") or None,
        "exchange": overview.get("Exchange") or None,
        "asset_type": overview.get("AssetType") or None,
        "market_cap": _safe_int(overview.get("MarketCapitalization")),
        "pe_ratio": _safe_float(overview.get("PERatio")),
        "forward_pe": _safe_float(overview.get("ForwardPE")),
        "peg_ratio": _safe_float(overview.get("PEGRatio")),
        "book_value": _safe_float(overview.get("BookValue")),
        "dividend_yield": _safe_float(overview.get("DividendYield")),
        "eps": _safe_float(overview.get("EPS")),
        "revenue_per_share": _safe_float(overview.get("RevenuePerShareTTM")),
        "profit_margin": _safe_float(overview.get("ProfitMargin")),
        "operating_margin": _safe_float(overview.get("OperatingMarginTTM")),
        "return_on_equity": _safe_float(overview.get("ReturnOnEquityTTM")),
        "beta": _safe_float(overview.get("Beta")),
        "ev_to_ebitda": _safe_float(overview.get("EVToEBITDA")),
        "shares_outstanding": _safe_int(overview.get("SharesOutstanding")),
    }
