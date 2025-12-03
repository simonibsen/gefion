import json
from pathlib import Path

import pytest

from g2.alphavantage.catalog import (
    ENDPOINTS,
    parse_cpi_monthly,
    parse_daily_adjusted,
    parse_listing_status,
)


def load_fixture(name: str) -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


def test_endpoint_catalog_contains_core_endpoints():
    required = {
        "TIME_SERIES_DAILY_ADJUSTED",
        "CPI",
    }
    assert required.issubset(ENDPOINTS.keys())
    assert ENDPOINTS["TIME_SERIES_DAILY_ADJUSTED"]["params"]["function"] == "TIME_SERIES_DAILY_ADJUSTED"
    assert ENDPOINTS["CPI"]["params"]["function"] == "CPI"


def test_parse_daily_adjusted_from_demo_payload():
    data = load_fixture("demo_time_series_daily_adjusted.json")
    rows = parse_daily_adjusted(symbol="IBM", payload=data)

    assert rows  # non-empty
    first = rows[0]
    assert first["symbol"] == "IBM"
    assert {"date", "open", "high", "low", "close", "adjusted_close", "volume"} <= set(first.keys())


def test_parse_cpi_monthly_from_demo_payload():
    data = load_fixture("demo_cpi.json")
    rows = parse_cpi_monthly(payload=data)

    assert rows  # non-empty
    first = rows[0]
    assert {"date", "value"} <= set(first.keys())
    # CPI demo has string numbers; ensure we cast to float
    assert isinstance(first["value"], float)


def test_parse_daily_adjusted_handles_missing_series():
    empty_payload = {"Time Series (Daily)": {}}
    rows = parse_daily_adjusted(symbol="T", payload=empty_payload)
    assert rows == []


def test_parse_cpi_monthly_handles_missing_data():
    rows = parse_cpi_monthly(payload={"data": []})
    assert rows == []


def test_parse_listing_status_from_demo_payload():
    data = load_fixture("demo_listing_status.json")
    rows = parse_listing_status(payload=data)
    assert len(rows) == 3
    assert rows[1]["symbol"] == "AAPL"
    # Filter active NASDAQ symbols
    active_nasdaq = [
        r["symbol"] for r in rows if r.get("status") == "Active" and r.get("exchange") == "NASDAQ"
    ]
    assert active_nasdaq == ["AAPL"]
