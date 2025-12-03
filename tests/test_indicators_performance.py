"""
Performance tests for local indicator computation.

These tests verify that indicator calculations use efficient pandas operations
rather than slow row-by-row iteration patterns.
"""
import time
from datetime import date, timedelta

import pytest

from g2.indicators.local import compute_indicators


def generate_price_rows(num_rows=1000):
    """Generate test price data."""
    base_date = date(2020, 1, 1)
    rows = []
    for i in range(num_rows):
        rows.append({
            "date": base_date + timedelta(days=i),
            "open": 100.0 + i * 0.1,
            "high": 102.0 + i * 0.1,
            "low": 98.0 + i * 0.1,
            "close": 101.0 + i * 0.1,
            "adjusted_close": 101.0 + i * 0.1,
            "volume": 1000000 + i * 1000,
        })
    return rows


def test_compute_indicators_performance():
    """Test that indicator computation is efficient (not using iterrows)."""
    # Generate 1000 days of price data
    rows = generate_price_rows(1000)

    indicators = ["rsi", "macd", "bbands", "adx", "stoch", "sma20", "sma50", "sma200", "ema12", "ema26", "psar"]

    start = time.time()
    result = compute_indicators(rows, indicators)
    elapsed = time.time() - start

    # Verify we got results
    assert len(result) > 0, "Should have computed indicators"

    # With efficient vectorized operations, this should complete in < 0.5 seconds
    # Using iterrows() would take 5-50+ seconds
    assert elapsed < 0.5, f"Indicator computation took {elapsed:.2f}s - likely using inefficient DataFrame iteration"

    # Verify result structure
    assert all("date" in r for r in result), "All results should have date"
    assert any("rsi_14" in r for r in result), "Should have RSI values"
    assert any("macd" in r for r in result), "Should have MACD values"


def test_compute_indicators_returns_dict_records():
    """Test that compute_indicators returns list of dicts efficiently."""
    rows = generate_price_rows(200)

    indicators = ["rsi", "sma20"]
    result = compute_indicators(rows, indicators)

    # Verify output format
    assert isinstance(result, list), "Should return a list"
    assert all(isinstance(r, dict) for r in result), "Each item should be a dict"
    assert all("date" in r and "source" in r for r in result), "Should have base fields"


def test_compute_indicators_handles_nan_properly():
    """Test that NaN values are handled correctly without performance penalty."""
    rows = generate_price_rows(100)

    # Add some missing values
    rows[10]["close"] = None
    rows[20]["adjusted_close"] = None

    indicators = ["rsi", "sma20"]
    result = compute_indicators(rows, indicators)

    # Should still get results, just with appropriate NaNs filtered out
    assert len(result) > 0, "Should handle NaN values"
    # Values should be numeric, not pandas NA types
    for r in result:
        for k, v in r.items():
            if k not in ("date", "source"):
                assert isinstance(v, (int, float)), f"Value for {k} should be numeric, got {type(v)}"


def test_compute_indicators_large_dataset():
    """Test performance with larger dataset."""
    # Generate 5000 days (~13.7 years) of price data
    rows = generate_price_rows(5000)

    indicators = ["rsi", "macd", "bbands"]

    start = time.time()
    result = compute_indicators(rows, indicators)
    elapsed = time.time() - start

    assert len(result) > 0
    # Even with 5000 rows and multiple indicators, should complete quickly
    assert elapsed < 2.0, f"Large dataset took {elapsed:.2f}s - performance issue detected"
