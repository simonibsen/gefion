"""Test derivative feature computation for ML."""
import pandas as pd
import numpy as np
from datetime import date, timedelta
from g2.features.derivatives import (
    add_derivative_features,
    compute_slope,
    compute_concavity,
)


def test_compute_slope_uptrend():
    """Test slope computation on upward trending data."""
    # Linear uptrend: y = 2x
    s = pd.Series([0, 2, 4, 6, 8, 10])

    slope = compute_slope(s, window=3)

    # Should detect slope of ~2.0
    assert not pd.isna(slope.iloc[-1])
    assert 1.8 < slope.iloc[-1] < 2.2


def test_compute_slope_downtrend():
    """Test slope computation on downward trending data."""
    # Linear downtrend
    s = pd.Series([100, 95, 90, 85, 80])

    slope = compute_slope(s, window=3)

    # Should detect negative slope
    assert slope.iloc[-1] < 0


def test_compute_concavity_accelerating():
    """Test concavity on accelerating series."""
    # Accelerating: 1, 2, 4, 7, 11, 16 (differences: 1, 2, 3, 4, 5)
    s = pd.Series([1, 2, 4, 7, 11, 16])

    concavity = compute_concavity(s, window=4)

    # Positive concavity = accelerating
    assert concavity.iloc[-1] > 0


def test_compute_concavity_decelerating():
    """Test concavity on decelerating series."""
    # Decelerating: 1, 6, 10, 13, 15, 16 (differences: 5, 4, 3, 2, 1)
    s = pd.Series([1, 6, 10, 13, 15, 16])

    concavity = compute_concavity(s, window=4)

    # Negative concavity = decelerating
    assert concavity.iloc[-1] < 0


def test_add_derivative_features():
    """Test adding derivative features to DataFrame."""
    df = pd.DataFrame({
        'date': [date(2024, 1, i) for i in range(1, 11)],
        'rsi_14': [50, 52, 55, 59, 64, 70, 75, 79, 82, 84],
        'macd': [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
    })

    result = add_derivative_features(
        df,
        columns=['rsi_14', 'macd'],
        slope_window=3,
        concavity_window=3
    )

    # Should add 4 new columns (2 per indicator)
    assert 'rsi_14_slope_3' in result.columns
    assert 'rsi_14_concavity_3' in result.columns
    assert 'macd_slope_3' in result.columns
    assert 'macd_concavity_3' in result.columns

    # Original columns should still exist
    assert 'rsi_14' in result.columns
    assert 'macd' in result.columns
    assert 'date' in result.columns

    # Should have values for slopes
    assert not result['rsi_14_slope_3'].isna().all()
    assert not result['macd_slope_3'].isna().all()


def test_derivative_features_detect_divergence():
    """Test that derivatives can detect price/indicator divergence."""
    # Price making higher highs
    price = pd.Series([100, 105, 110, 115, 120])

    # RSI making lower highs (bearish divergence)
    rsi = pd.Series([70, 68, 66, 64, 62])

    price_slope = compute_slope(price, window=3)
    rsi_slope = compute_slope(rsi, window=3)

    # Price slope should be positive
    assert price_slope.iloc[-1] > 0

    # RSI slope should be negative
    assert rsi_slope.iloc[-1] < 0

    # This divergence is a classic bearish signal for ML models


def test_concavity_detects_momentum_shift():
    """Test that concavity detects changes in momentum."""
    # Price rising, then leveling off (losing momentum)
    s = pd.Series([100, 105, 109, 112, 114, 115, 115.5, 116])

    concavity = compute_concavity(s, window=4)

    # All concavity values should be negative (decelerating throughout)
    # This correctly indicates the series is slowing down
    valid_concavity = concavity.dropna()
    assert (valid_concavity < 0).all(), "Decelerating series should have negative concavity"


def test_handles_missing_column_gracefully():
    """Test that missing columns are handled without errors."""
    df = pd.DataFrame({
        'rsi_14': [50, 52, 55],
    })

    # Request derivatives for column that doesn't exist
    result = add_derivative_features(
        df,
        columns=['rsi_14', 'nonexistent_column'],
        slope_window=2
    )

    # Should add derivatives for rsi_14
    assert 'rsi_14_slope_2' in result.columns

    # Should not fail on missing column
    assert 'nonexistent_column_slope_2' not in result.columns
