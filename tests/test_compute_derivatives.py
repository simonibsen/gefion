"""Test pure compute_derivatives function (TDD).

This tests the pure computation function that takes source data
and derivative specifications, returns computed derivatives.
Similar to compute_indicators pattern.
"""
import pytest
from datetime import date
import pandas as pd
from g2.features.derivatives import compute_derivatives


def test_compute_derivatives_basic():
    """Test basic derivative computation."""
    # Source data: RSI values over time
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i}
        for i in range(1, 21)
    ]

    # Derivative specs
    derivative_specs = [
        {'name': 'rsi_14_slope_5', 'type': 'slope', 'window': 5, 'method': 'linreg'}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should return list of dicts with date and computed values
    assert isinstance(results, list)
    assert len(results) > 0
    assert 'date' in results[0]
    # First few rows won't have values (need window size)
    # Check a later row that should have the derivative
    assert any('rsi_14_slope_5' in r for r in results)


def test_compute_derivatives_multiple_specs():
    """Test computing multiple derivatives from same source."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i * 2}
        for i in range(1, 21)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5},
        {'name': 'slope_10', 'type': 'slope', 'window': 10},
        {'name': 'concavity_5', 'type': 'concavity', 'window': 5},
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should compute all derivatives
    assert len(results) > 0
    first_result = results[-1]  # Last row should have all values
    assert 'slope_5' in first_result
    assert 'slope_10' in first_result
    assert 'concavity_5' in first_result


def test_compute_derivatives_handles_empty_source():
    """Test handling of empty source data."""
    source_rows = []
    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should return empty list
    assert results == []


def test_compute_derivatives_handles_insufficient_data():
    """Test handling when not enough data for window size."""
    # Only 3 rows, but window is 5
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0}
        for i in range(1, 4)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should return results with NaN for insufficient data
    assert isinstance(results, list)


def test_compute_derivatives_slope_uptrend():
    """Test slope computation detects uptrend."""
    # Linear uptrend
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i * 2}
        for i in range(1, 11)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Last result should have positive slope
    last_result = results[-1]
    assert last_result['slope_5'] > 0


def test_compute_derivatives_slope_downtrend():
    """Test slope computation detects downtrend."""
    # Linear downtrend
    source_rows = [
        {'date': date(2024, 1, i), 'value': 100.0 - i * 2}
        for i in range(1, 11)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Last result should have negative slope
    last_result = results[-1]
    assert last_result['slope_5'] < 0


def test_compute_derivatives_concavity_accelerating():
    """Test concavity detects acceleration."""
    # Accelerating series: 1, 2, 4, 7, 11, 16
    values = [1, 2, 4, 7, 11, 16, 22, 29, 37]
    source_rows = [
        {'date': date(2024, 1, i), 'value': values[i-1]}
        for i in range(1, len(values) + 1)
    ]

    derivative_specs = [
        {'name': 'concavity_5', 'type': 'concavity', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should have positive concavity (accelerating)
    last_result = results[-1]
    assert last_result['concavity_5'] > 0


def test_compute_derivatives_concavity_decelerating():
    """Test concavity detects deceleration."""
    # Decelerating series: differences decrease
    values = [1, 6, 10, 13, 15, 16, 16.5, 16.75]
    source_rows = [
        {'date': date(2024, 1, i), 'value': values[i-1]}
        for i in range(1, len(values) + 1)
    ]

    derivative_specs = [
        {'name': 'concavity_5', 'type': 'concavity', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should have negative concavity (decelerating)
    last_result = results[-1]
    assert last_result['concavity_5'] < 0


def test_compute_derivatives_with_return_failures():
    """Test return_failures parameter for error tracking."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i}
        for i in range(1, 21)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5},
        {'name': 'invalid', 'type': 'unknown_type', 'window': 5},
    ]

    results, failures = compute_derivatives(
        source_rows,
        derivative_specs,
        return_failures=True
    )

    # Should return both results and failures
    assert isinstance(results, list)
    assert isinstance(failures, list)

    # Should have computed valid derivative
    assert any('slope_5' in r for r in results if r.get('slope_5') is not None)

    # Should track the invalid derivative
    assert len(failures) > 0


def test_compute_derivatives_preserves_date_order():
    """Test that results maintain date ordering."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i}
        for i in range(1, 21)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Dates should be in ascending order
    dates = [r['date'] for r in results]
    assert dates == sorted(dates)


def test_compute_derivatives_different_methods():
    """Test different computation methods (linreg vs diff)."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i * 2}
        for i in range(1, 21)
    ]

    specs_linreg = [
        {'name': 'slope_linreg', 'type': 'slope', 'window': 5, 'method': 'linreg'}
    ]

    specs_diff = [
        {'name': 'slope_diff', 'type': 'slope', 'window': 5, 'method': 'diff'}
    ]

    results_linreg = compute_derivatives(source_rows, specs_linreg)
    results_diff = compute_derivatives(source_rows, specs_diff)

    # Both should produce results (though values may differ)
    assert len(results_linreg) > 0
    assert len(results_diff) > 0


def test_compute_derivatives_handles_missing_values():
    """Test handling of missing/null values in source data."""
    source_rows = [
        {'date': date(2024, 1, 1), 'value': 50.0},
        {'date': date(2024, 1, 2), 'value': None},  # Missing
        {'date': date(2024, 1, 3), 'value': 52.0},
        {'date': date(2024, 1, 4), 'value': 53.0},
        {'date': date(2024, 1, 5), 'value': 54.0},
    ]

    derivative_specs = [
        {'name': 'slope_3', 'type': 'slope', 'window': 3}
    ]

    # Should not crash
    results = compute_derivatives(source_rows, derivative_specs)
    assert isinstance(results, list)


def test_compute_derivatives_pure_function():
    """Test that compute_derivatives is a pure function (no side effects)."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i}
        for i in range(1, 21)
    ]
    original_source = source_rows.copy()

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5}
    ]

    compute_derivatives(source_rows, derivative_specs)

    # Source data should not be modified
    assert source_rows == original_source


def test_compute_derivatives_multiple_windows():
    """Test computing same derivative type with different windows."""
    source_rows = [
        {'date': date(2024, 1, i), 'value': 50.0 + i}
        for i in range(1, 31)
    ]

    derivative_specs = [
        {'name': 'slope_5', 'type': 'slope', 'window': 5},
        {'name': 'slope_10', 'type': 'slope', 'window': 10},
        {'name': 'slope_20', 'type': 'slope', 'window': 20},
    ]

    results = compute_derivatives(source_rows, derivative_specs)

    # Should have all three slopes
    last_result = results[-1]
    assert 'slope_5' in last_result
    assert 'slope_10' in last_result
    assert 'slope_20' in last_result

    # Different windows may have different values
    # (in stable uptrend they should be similar but not necessarily equal)
