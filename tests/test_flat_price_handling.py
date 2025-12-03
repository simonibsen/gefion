"""Test handling of flat/frozen price data in indicator computation."""
from datetime import date, timedelta
from g2.indicators.local import compute_indicators


def test_compute_indicators_handles_flat_prices():
    """Test that flat/frozen prices (delisted stocks) are handled gracefully."""
    # Simulate frozen stock data - all OHLC values identical
    price_rows = [
        {
            "date": date(2024, 1, i),
            "open": 10.72,
            "high": 10.72,
            "low": 10.72,
            "close": 10.72,
            "adjusted_close": 10.72,
            "volume": 0,
        }
        for i in range(1, 31)  # 30 days of flat data
    ]

    # Should not crash, should return gracefully
    results, failed = compute_indicators(price_rows, ["adx", "stoch", "rsi"], return_failures=True)

    # ADX and STOCH should fail gracefully (no variation)
    # RSI should work (only needs close prices)
    failed_names = [f[0] for f in failed]

    # Should not raise exceptions
    assert isinstance(results, list)
    assert isinstance(failed, list)

    # ADX and STOCH should be silently skipped (no error since we check for variation)
    # They won't appear in failed list because they return early
    # RSI might still compute on flat prices


def test_compute_indicators_handles_partial_flat_prices():
    """Test that partially flat prices (some variation) work correctly."""
    # Start with varied prices, then go flat
    price_rows = []

    # First 20 days with variation
    for i in range(1, 21):
        price_rows.append({
            "date": date(2024, 1, i),
            "open": 10.0 + (i * 0.1),
            "high": 10.5 + (i * 0.1),
            "low": 9.5 + (i * 0.1),
            "close": 10.0 + (i * 0.1),
            "adjusted_close": 10.0 + (i * 0.1),
            "volume": 1000,
        })

    # Last 10 days flat (frozen)
    for i in range(21, 31):
        price_rows.append({
            "date": date(2024, 1, i),
            "open": 12.0,
            "high": 12.0,
            "low": 12.0,
            "close": 12.0,
            "adjusted_close": 12.0,
            "volume": 0,
        })

    # Should compute successfully for the varied portion
    results, failed = compute_indicators(price_rows, ["adx", "stoch", "rsi"], return_failures=True)

    # Should have some results from the varied portion
    assert len(results) > 0, "Should produce results from varied price data"

    # Should not fail
    assert len(failed) == 0, f"Should not fail, got failures: {failed}"


def test_compute_indicators_handles_single_unique_value():
    """Test that data with only one unique value is handled gracefully."""
    # All values exactly the same
    start_date = date(2024, 1, 1)
    price_rows = [
        {
            "date": start_date + timedelta(days=i),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "adjusted_close": 100.0,
            "volume": 0,
        }
        for i in range(100)  # 100 days, all identical
    ]

    # Should not crash
    results, failed = compute_indicators(price_rows, ["adx", "stoch"], return_failures=True)

    # Should return empty or minimal results (no variation to compute from)
    assert isinstance(results, list)
    assert isinstance(failed, list)
    # No exceptions should be raised
