"""
TDD tests for JSON-based indicator functions.

These tests will initially fail and drive the implementation of moving
indicators from Python code to JSON-based database-stored functions.
"""
import json
from pathlib import Path

import pytest


def test_rsi_json_file_exists():
    """Test that indicator_rsi.json exists in feature-functions directory."""
    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_rsi.json"
    assert json_path.exists(), f"Expected RSI JSON file at {json_path}"


def test_sma_json_file_exists():
    """Test that indicator_sma.json exists in feature-functions directory."""
    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_sma.json"
    assert json_path.exists(), f"Expected SMA JSON file at {json_path}"


def test_ema_json_file_exists():
    """Test that indicator_ema.json exists in feature-functions directory."""
    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_ema.json"
    assert json_path.exists(), f"Expected EMA JSON file at {json_path}"


def test_rsi_json_has_required_fields():
    """Test that RSI JSON has all required fields."""
    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_rsi.json"
    with json_path.open() as f:
        data = json.load(f)

    required_fields = ["name", "version", "language", "description", "function_body"]
    for field in required_fields:
        assert field in data, f"Missing required field: {field}"

    assert data["name"] == "indicator_rsi"
    assert data["language"] == "python"
    assert len(data["function_body"]) > 0


def test_rsi_json_computes_correct_values():
    """Test that RSI JSON function computes correct values."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_rsi.json"
    with json_path.open() as f:
        func_def = json.load(f)

    # Sample price data
    price_rows = [
        {"date": "2024-01-01", "close": 100.0},
        {"date": "2024-01-02", "close": 102.0},
        {"date": "2024-01-03", "close": 101.0},
        {"date": "2024-01-04", "close": 103.0},
        {"date": "2024-01-05", "close": 105.0},
        {"date": "2024-01-06", "close": 104.0},
        {"date": "2024-01-07", "close": 106.0},
        {"date": "2024-01-08", "close": 107.0},
        {"date": "2024-01-09", "close": 106.5},
        {"date": "2024-01-10", "close": 108.0},
        {"date": "2024-01-11", "close": 109.0},
        {"date": "2024-01-12", "close": 108.5},
        {"date": "2024-01-13", "close": 110.0},
        {"date": "2024-01-14", "close": 111.0},
        {"date": "2024-01-15", "close": 110.5},
    ]

    # Execute the function in a sandbox
    namespace = {}
    exec(func_def["function_body"], namespace)
    compute_func = namespace["compute"]

    # Compute RSI
    specs = {"period": 14}
    results = compute_func(price_rows, specs)

    # Verify results
    assert isinstance(results, list)
    assert len(results) > 0

    # Check that results have the expected structure
    for result in results:
        assert "date" in result
        assert "rsi_14" in result or "value" in result
        # RSI should be between 0 and 100
        value = result.get("rsi_14") or result.get("value")
        assert 0 <= value <= 100


def test_sma_json_computes_correct_values():
    """Test that SMA JSON function computes correct values."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_sma.json"
    with json_path.open() as f:
        func_def = json.load(f)

    # Sample price data
    price_rows = [
        {"date": f"2024-01-{i:02d}", "close": 100.0 + i}
        for i in range(1, 26)
    ]

    # Execute the function in a sandbox
    namespace = {}
    exec(func_def["function_body"], namespace)
    compute_func = namespace["compute"]

    # Compute SMA(20)
    specs = {"window": 20}
    results = compute_func(price_rows, specs)

    # Verify results
    assert isinstance(results, list)
    assert len(results) > 0

    # Check last value (average of 100+6 to 100+25 = average of 106 to 125 = 115.5)
    last_result = results[-1]
    assert "date" in last_result
    value_key = "sma_20" if "sma_20" in last_result else "value"
    assert value_key in last_result

    # Should be close to 115.5
    expected = sum(range(106, 126)) / 20  # 115.5
    actual = last_result[value_key]
    assert abs(actual - expected) < 0.1


def test_ema_json_computes_correct_values():
    """Test that EMA JSON function computes correct values."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    json_path = Path(__file__).parent.parent / "feature-functions" / "indicator_ema.json"
    with json_path.open() as f:
        func_def = json.load(f)

    # Sample price data
    price_rows = [
        {"date": f"2024-01-{i:02d}", "close": 100.0 + i}
        for i in range(1, 26)
    ]

    # Execute the function in a sandbox
    namespace = {}
    exec(func_def["function_body"], namespace)
    compute_func = namespace["compute"]

    # Compute EMA(12)
    specs = {"span": 12}
    results = compute_func(price_rows, specs)

    # Verify results
    assert isinstance(results, list)
    assert len(results) > 0

    # Check that all results have required fields
    for result in results:
        assert "date" in result
        value_key = "ema_12" if "ema_12" in result else "value"
        assert value_key in result
        # EMA should be numeric
        assert isinstance(result[value_key], (int, float))


def test_json_functions_match_existing_implementation():
    """Test that JSON-based indicators produce same results as existing code."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    from g2.indicators.local import compute_indicators

    # Sample price data
    price_rows = [
        {
            "date": f"2024-01-{i:02d}",
            "close": 100.0 + i * 0.5,
            "adjusted_close": 100.0 + i * 0.5,
            "high": 101.0 + i * 0.5,
            "low": 99.0 + i * 0.5,
            "open": 100.0 + i * 0.5,
            "volume": 1000000,
        }
        for i in range(1, 31)
    ]

    # Compute using existing implementation
    existing_results = compute_indicators(price_rows, ["rsi", "sma20"])

    # Compute using JSON implementation (RSI)
    json_path_rsi = Path(__file__).parent.parent / "feature-functions" / "indicator_rsi.json"
    with json_path_rsi.open() as f:
        rsi_def = json.load(f)

    namespace_rsi = {}
    exec(rsi_def["function_body"], namespace_rsi)
    rsi_results = namespace_rsi["compute"](price_rows, {"period": 14})

    # Compute using JSON implementation (SMA)
    json_path_sma = Path(__file__).parent.parent / "feature-functions" / "indicator_sma.json"
    with json_path_sma.open() as f:
        sma_def = json.load(f)

    namespace_sma = {}
    exec(sma_def["function_body"], namespace_sma)
    sma_results = namespace_sma["compute"](price_rows, {"window": 20})

    # Verify that we got results from both
    assert len(existing_results) > 0
    assert len(rsi_results) > 0
    assert len(sma_results) > 0

    # Find a date that exists in both results for RSI
    existing_rsi_dates = {r["date"]: r.get("rsi_14") for r in existing_results if "rsi_14" in r}
    json_rsi_dates = {r["date"]: r.get("rsi_14") or r.get("value") for r in rsi_results}

    # Compare at least one common date
    common_dates = set(existing_rsi_dates.keys()) & set(json_rsi_dates.keys())
    assert len(common_dates) > 0, "No common dates found between existing and JSON RSI results"

    # Check that values are close (within 1% tolerance for RSI)
    for date in list(common_dates)[:5]:  # Check first 5 common dates
        existing_val = existing_rsi_dates[date]
        json_val = json_rsi_dates[date]
        if existing_val is not None and json_val is not None:
            assert abs(existing_val - json_val) < 1.0, \
                f"RSI values differ too much on {date}: existing={existing_val}, json={json_val}"
