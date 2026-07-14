"""
AlphaVantage endpoint catalog and simple parsers for demo payloads.

We avoid network calls here; tests rely on documented demo responses.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Dict, List, Mapping, Optional

from gefion.quality.taxonomy import normalize_industry, normalize_sector

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


def parse_index_data(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse INDEX_DATA (daily OHLC, e.g. VIX) into macro-series rows.

    The canonical value is the close (spec 007 R3: `value` is required, OHLC
    optional). Rows with missing/invalid numbers are skipped, not fatal.
    """
    from datetime import date as _date

    series = payload.get("Time Series (Daily)", {})
    if not isinstance(series, Mapping):
        return []

    rows: List[Dict[str, object]] = []
    for date_str, values in series.items():
        try:
            rows.append(
                {
                    "date": _date.fromisoformat(date_str),
                    "value": float(values.get("4. close")),
                    "open": float(values.get("1. open")),
                    "high": float(values.get("2. high")),
                    "low": float(values.get("3. low")),
                }
            )
        except (TypeError, ValueError, AttributeError):
            continue
    rows.sort(key=lambda r: r["date"])
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
        "sector": normalize_sector(overview.get("Sector")),
        "industry": normalize_industry(overview.get("Industry")),
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


def _parse_date(val: Optional[str]) -> Optional[date_type]:
    """Parse a YYYY-MM-DD string to a date object."""
    if not val or val in ("None", "-", ""):
        return None
    try:
        parts = val.split("-")
        return date_type(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


# --- Quarterly Financial Statement Parsers ---
# Each returns List[Dict] matching the quarterly_financials table columns.
# Core fields are typed; remaining fields go into the 'raw' JSONB column.

_INCOME_CORE_FIELDS = {
    "totalRevenue": ("revenue", _safe_int),
    "netIncome": ("net_income", _safe_int),
    "grossProfit": ("gross_profit", _safe_int),
    "ebitda": ("ebitda", _safe_int),
    "operatingIncome": ("operating_income", _safe_int),
    "eps": ("eps", _safe_float),
}

_BALANCE_SHEET_CORE_FIELDS = {
    "totalAssets": ("total_assets", _safe_int),
    "totalLiabilities": ("total_liabilities", _safe_int),
    "totalShareholderEquity": ("shareholder_equity", _safe_int),
    "totalCurrentAssets": ("current_assets", _safe_int),
    "totalCurrentLiabilities": ("current_liabilities", _safe_int),
    "longTermDebt": ("long_term_debt", _safe_int),
    "commonStockSharesOutstanding": ("shares_outstanding", _safe_int),
}

_CASH_FLOW_CORE_FIELDS = {
    "operatingCashflow": ("operating_cashflow", _safe_int),
    "capitalExpenditures": ("capital_expenditures", _safe_int),
}

_EARNINGS_CORE_FIELDS = {
    "reportedEPS": ("reported_eps", _safe_float),
    "estimatedEPS": ("estimated_eps", _safe_float),
    "surprise": ("surprise", _safe_float),
    "surprisePercentage": ("surprise_percentage", _safe_float),
}

# Keys to exclude from the raw overflow dict (metadata, not financial data)
_SKIP_RAW_KEYS = {"fiscalDateEnding", "reportedDate"}


def _parse_quarterly_reports(
    payload: Mapping[str, object],
    reports_key: str,
    statement_type: str,
    core_fields: Dict[str, tuple],
    include_reported_at: bool = False,
) -> List[Dict[str, object]]:
    """Generic parser for quarterly financial reports."""
    reports = payload.get(reports_key)
    if not isinstance(reports, list):
        return []

    results: List[Dict[str, object]] = []
    all_core_av_keys = set(core_fields.keys())

    for report in reports:
        if not isinstance(report, dict):
            continue

        fiscal_date = _parse_date(report.get("fiscalDateEnding"))
        if fiscal_date is None:
            continue

        record: Dict[str, object] = {
            "date": fiscal_date,
            "statement_type": statement_type,
            "reported_at": _parse_date(report.get("reportedDate")) if include_reported_at else None,
        }

        # Extract core typed fields
        for av_key, (col_name, parser_fn) in core_fields.items():
            record[col_name] = parser_fn(report.get(av_key))

        # Overflow: everything not in core fields or skip list
        raw = {}
        for k, v in report.items():
            if k not in all_core_av_keys and k not in _SKIP_RAW_KEYS:
                raw[k] = v
        if raw:
            record["raw"] = raw

        results.append(record)

    return results


def parse_income_statement(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse INCOME_STATEMENT response into quarterly records."""
    return _parse_quarterly_reports(
        payload, "quarterlyReports", "income", _INCOME_CORE_FIELDS,
    )


def parse_balance_sheet(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse BALANCE_SHEET response into quarterly records."""
    return _parse_quarterly_reports(
        payload, "quarterlyReports", "balance_sheet", _BALANCE_SHEET_CORE_FIELDS,
    )


def parse_cash_flow(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse CASH_FLOW response into quarterly records."""
    return _parse_quarterly_reports(
        payload, "quarterlyReports", "cash_flow", _CASH_FLOW_CORE_FIELDS,
    )


def parse_earnings(payload: Mapping[str, object]) -> List[Dict[str, object]]:
    """Parse EARNINGS response into quarterly earnings records."""
    return _parse_quarterly_reports(
        payload, "quarterlyEarnings", "earnings", _EARNINGS_CORE_FIELDS,
        include_reported_at=True,
    )


# ============================================================================
# Documentation source of truth — consumed by scripts/gen_data_dictionary.py.
# Every new AlphaVantage endpoint that lands data in our DB MUST add an entry
# here so the data dictionary stays accurate.
# ============================================================================

# field_map entry: av_field → (table, db_column, human_description)
ENDPOINT_DOCS: Dict[str, Dict[str, object]] = {
    "TIME_SERIES_DAILY_ADJUSTED": {
        "cadence": "daily",
        "tables": ["stock_ohlcv"],
        "cli": "gefion data-update / gefion prices-ingest",
        "field_map": {
            "1. open": ("stock_ohlcv", "open", "Opening price"),
            "2. high": ("stock_ohlcv", "high", "Daily high"),
            "3. low": ("stock_ohlcv", "low", "Daily low"),
            "4. close": ("stock_ohlcv", "close", "Closing price"),
            "5. adjusted close": ("stock_ohlcv", "adjusted_close", "Split/dividend-adjusted close"),
            "6. volume": ("stock_ohlcv", "volume", "Share volume"),
            "7. dividend amount": ("stock_ohlcv", "dividend_amount", "Cash dividend per share on date"),
            "8. split coefficient": ("stock_ohlcv", "split_coefficient", "Split ratio (1.0 = no split)"),
        },
    },
    "LISTING_STATUS": {
        "cadence": "weekly",
        "tables": ["stocks"],
        "cli": "(implicit, universe management)",
        "field_map": {
            "symbol": ("stocks", "symbol", "Stock ticker"),
            "name": ("stocks", "name", "Company name"),
            "exchange": ("stocks", "exchange", "Listing exchange (NASDAQ, NYSE, ...)"),
            "assetType": ("stocks", "asset_type", "Stock / ETF / etc."),
            "ipoDate": ("stocks", "ipo_date", "IPO date"),
            "delistingDate": ("stocks", "delisting_date", "Delisting date if applicable"),
            "status": ("stocks", "status", "active / delisted"),
        },
    },
    "OVERVIEW": {
        "cadence": "on-demand (gefion fundamentals-update)",
        "tables": ["stocks_fundamentals"],
        "cli": "gefion fundamentals-update",
        "field_map": {
            "Symbol": ("stocks_fundamentals", "symbol", "Stock ticker"),
            "Name": ("stocks_fundamentals", "name", "Company name"),
            "Sector": ("stocks_fundamentals", "sector", "GICS sector"),
            "Industry": ("stocks_fundamentals", "industry", "GICS industry"),
            "Exchange": ("stocks_fundamentals", "exchange", "Listing exchange"),
            "AssetType": ("stocks_fundamentals", "asset_type", "Stock / ETF / etc."),
            "MarketCapitalization": ("stocks_fundamentals", "market_cap", "Market capitalization in USD"),
            "PERatio": ("stocks_fundamentals", "pe_ratio", "Trailing P/E ratio"),
            "ForwardPE": ("stocks_fundamentals", "forward_pe", "Forward P/E ratio"),
            "PEGRatio": ("stocks_fundamentals", "peg_ratio", "Price/Earnings-to-Growth"),
            "BookValue": ("stocks_fundamentals", "book_value", "Book value per share"),
            "DividendYield": ("stocks_fundamentals", "dividend_yield", "Annualized dividend yield"),
            "EPS": ("stocks_fundamentals", "eps", "Earnings per share (TTM)"),
            "RevenuePerShareTTM": ("stocks_fundamentals", "revenue_per_share", "Revenue per share TTM"),
            "ProfitMargin": ("stocks_fundamentals", "profit_margin", "Net profit margin"),
            "OperatingMarginTTM": ("stocks_fundamentals", "operating_margin", "Operating margin TTM"),
            "ReturnOnEquityTTM": ("stocks_fundamentals", "return_on_equity", "Return on equity TTM"),
            "Beta": ("stocks_fundamentals", "beta", "Beta vs market"),
            "EVToEBITDA": ("stocks_fundamentals", "ev_to_ebitda", "Enterprise value / EBITDA"),
            "SharesOutstanding": ("stocks_fundamentals", "shares_outstanding", "Shares outstanding"),
        },
    },
    "INCOME_STATEMENT": {
        "cadence": "quarterly (gefion financials-backfill)",
        "tables": ["quarterly_financials"],
        "cli": "gefion financials-backfill",
        "notes": "statement_type = 'income'. Non-core fields preserved in `raw` JSONB column.",
        "field_map": {
            "totalRevenue": ("quarterly_financials", "revenue", "Total revenue for the quarter"),
            "netIncome": ("quarterly_financials", "net_income", "Net income"),
            "grossProfit": ("quarterly_financials", "gross_profit", "Gross profit"),
            "ebitda": ("quarterly_financials", "ebitda", "EBITDA"),
            "operatingIncome": ("quarterly_financials", "operating_income", "Operating income"),
            "eps": ("quarterly_financials", "eps", "Earnings per share (basic)"),
        },
    },
    "BALANCE_SHEET": {
        "cadence": "quarterly (gefion financials-backfill)",
        "tables": ["quarterly_financials"],
        "cli": "gefion financials-backfill",
        "notes": "statement_type = 'balance_sheet'. Non-core fields preserved in `raw` JSONB column.",
        "field_map": {
            "totalAssets": ("quarterly_financials", "total_assets", "Total assets"),
            "totalLiabilities": ("quarterly_financials", "total_liabilities", "Total liabilities"),
            "totalShareholderEquity": ("quarterly_financials", "shareholder_equity", "Shareholder equity"),
            "totalCurrentAssets": ("quarterly_financials", "current_assets", "Current assets"),
            "totalCurrentLiabilities": ("quarterly_financials", "current_liabilities", "Current liabilities"),
            "longTermDebt": ("quarterly_financials", "long_term_debt", "Long-term debt"),
            "commonStockSharesOutstanding": ("quarterly_financials", "shares_outstanding", "Shares outstanding at quarter end"),
        },
    },
    "CASH_FLOW": {
        "cadence": "quarterly (gefion financials-backfill)",
        "tables": ["quarterly_financials"],
        "cli": "gefion financials-backfill",
        "notes": "statement_type = 'cash_flow'. Non-core fields preserved in `raw` JSONB column.",
        "field_map": {
            "operatingCashflow": ("quarterly_financials", "operating_cashflow", "Operating cash flow"),
            "capitalExpenditures": ("quarterly_financials", "capital_expenditures", "Capital expenditures (CapEx)"),
        },
    },
    "EARNINGS": {
        "cadence": "quarterly (gefion financials-backfill)",
        "tables": ["quarterly_financials"],
        "cli": "gefion financials-backfill",
        "notes": "statement_type = 'earnings'. Non-core fields preserved in `raw` JSONB column.",
        "field_map": {
            "reportedEPS": ("quarterly_financials", "reported_eps", "Actual reported EPS"),
            "estimatedEPS": ("quarterly_financials", "estimated_eps", "Analyst consensus EPS estimate"),
            "surprise": ("quarterly_financials", "surprise", "Reported − estimated"),
            "surprisePercentage": ("quarterly_financials", "surprise_percentage", "Surprise as % of estimate"),
        },
    },
    "CPI": {
        "cadence": "monthly",
        "tables": [],
        "cli": "(not yet wired up)",
        "notes": "Parser exists in `catalog.parse_cpi_monthly` but no destination table — scaffolded, not in production.",
        "field_map": {},
    },
}
