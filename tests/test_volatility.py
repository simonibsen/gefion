"""Tests for volatility computation and adaptive thresholds.

TDD: These tests are written FIRST, before implementation.
"""
import math
import pytest
import pandas as pd
import numpy as np


class TestHistoricalVolatility:
    """Tests for calculate_historical_volatility function."""

    def test_calculates_annualized_volatility(self):
        """Test basic volatility calculation with annualization."""
        from g2.ml.volatility import calculate_historical_volatility

        # Create returns with known std dev
        # 1% daily std dev = ~15.9% annualized (1% * sqrt(252))
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0, 0.01, 100))

        vol = calculate_historical_volatility(returns, window=60, annualize=True)

        # Should be approximately 15.9% (1% * sqrt(252))
        assert vol is not None
        assert 0.10 < vol < 0.25  # Reasonable range for 1% daily vol

    def test_returns_none_for_insufficient_data(self):
        """Test returns None when not enough data for window."""
        from g2.ml.volatility import calculate_historical_volatility

        returns = pd.Series([0.01, 0.02, -0.01])  # Only 3 data points

        vol = calculate_historical_volatility(returns, window=60, annualize=True)

        assert vol is None

    def test_non_annualized_volatility(self):
        """Test volatility without annualization."""
        from g2.ml.volatility import calculate_historical_volatility

        np.random.seed(42)
        returns = pd.Series(np.random.normal(0, 0.01, 100))

        vol = calculate_historical_volatility(returns, window=60, annualize=False)

        # Should be approximately 1% (daily)
        assert vol is not None
        assert 0.005 < vol < 0.02


class TestBollingerBandWidth:
    """Tests for calculate_bb_width function."""

    def test_calculates_normalized_width(self):
        """Test BB width calculation."""
        from g2.ml.volatility import calculate_bb_width

        # Upper=110, Lower=90, Middle=100 -> width = 20/100 = 0.20
        width = calculate_bb_width(bb_upper=110, bb_lower=90, bb_middle=100)

        assert width == pytest.approx(0.20)

    def test_returns_none_for_zero_middle(self):
        """Test returns None when middle band is zero."""
        from g2.ml.volatility import calculate_bb_width

        width = calculate_bb_width(bb_upper=110, bb_lower=90, bb_middle=0)

        assert width is None

    def test_returns_none_for_negative_middle(self):
        """Test returns None when middle band is negative."""
        from g2.ml.volatility import calculate_bb_width

        width = calculate_bb_width(bb_upper=110, bb_lower=90, bb_middle=-100)

        assert width is None


class TestAdaptiveThresholds:
    """Tests for compute_adaptive_thresholds function."""

    def test_scales_by_horizon_sqrt_t(self):
        """Test thresholds scale by sqrt(T) for different horizons."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.25  # 25% annual volatility

        weak_7, strong_7 = compute_adaptive_thresholds(vol, horizon_days=7)
        weak_30, strong_30 = compute_adaptive_thresholds(vol, horizon_days=30)

        # 30-day thresholds should be ~2x 7-day (sqrt(30/7) ≈ 2.07)
        ratio = weak_30 / weak_7
        assert 1.8 < ratio < 2.3

    def test_aapl_example_thresholds(self):
        """Test AAPL-like stock (25% vol) gets expected thresholds."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.25  # 25% annual volatility

        weak_7, strong_7 = compute_adaptive_thresholds(vol, horizon_days=7)

        # Expected: horizon_vol = 0.25 * sqrt(7/252) = 0.0417
        # weak = 0.0417 * 0.5 = 0.0208 (2.1%)
        # strong = 0.0417 * 1.5 = 0.0625 (6.3%)
        assert weak_7 == pytest.approx(0.021, rel=0.1)
        assert strong_7 == pytest.approx(0.063, rel=0.1)

    def test_tsla_example_thresholds(self):
        """Test TSLA-like stock (60% vol) gets wider thresholds."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.60  # 60% annual volatility

        weak_7, strong_7 = compute_adaptive_thresholds(vol, horizon_days=7)

        # Expected: horizon_vol = 0.60 * sqrt(7/252) = 0.10
        # weak = 0.10 * 0.5 = 0.05 (5%)
        # strong = 0.10 * 1.5 = 0.15 (15%)
        assert weak_7 == pytest.approx(0.05, rel=0.1)
        assert strong_7 == pytest.approx(0.15, rel=0.1)

    def test_high_volatility_percentile_adjustment(self):
        """Test high volatility percentile gets wider thresholds."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.25

        weak_normal, strong_normal = compute_adaptive_thresholds(
            vol, horizon_days=7, volatility_percentile=0.5
        )
        weak_high, strong_high = compute_adaptive_thresholds(
            vol, horizon_days=7, volatility_percentile=0.95
        )

        # High percentile should have 1.2x wider thresholds
        assert weak_high == pytest.approx(weak_normal * 1.2)
        assert strong_high == pytest.approx(strong_normal * 1.2)

    def test_low_volatility_percentile_adjustment(self):
        """Test low volatility percentile gets narrower thresholds."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.25

        weak_normal, strong_normal = compute_adaptive_thresholds(
            vol, horizon_days=7, volatility_percentile=0.5
        )
        weak_low, strong_low = compute_adaptive_thresholds(
            vol, horizon_days=7, volatility_percentile=0.05
        )

        # Low percentile should have 0.8x narrower thresholds
        assert weak_low == pytest.approx(weak_normal * 0.8)
        assert strong_low == pytest.approx(strong_normal * 0.8)

    def test_custom_sigma_multipliers(self):
        """Test custom weak/strong sigma multipliers."""
        from g2.ml.volatility import compute_adaptive_thresholds

        vol = 0.25

        weak, strong = compute_adaptive_thresholds(
            vol, horizon_days=7, weak_sigma=1.0, strong_sigma=2.0
        )

        # With 1.0/2.0 multipliers, strong should be 2x weak
        assert strong == pytest.approx(weak * 2.0)


class TestVolatilityPercentile:
    """Tests for compute_volatility_percentile function."""

    def test_median_volatility_returns_50th_percentile(self):
        """Test median volatility stock returns ~0.5 percentile."""
        from g2.ml.volatility import compute_volatility_percentile

        all_vols = pd.Series([0.10, 0.20, 0.30, 0.40, 0.50])
        stock_vol = 0.30  # Median

        percentile = compute_volatility_percentile(stock_vol, all_vols)

        assert percentile == pytest.approx(0.4)  # 2 out of 5 are below

    def test_highest_volatility_returns_high_percentile(self):
        """Test highest volatility stock returns high percentile."""
        from g2.ml.volatility import compute_volatility_percentile

        all_vols = pd.Series([0.10, 0.20, 0.30, 0.40, 0.50])
        stock_vol = 0.60  # Higher than all

        percentile = compute_volatility_percentile(stock_vol, all_vols)

        assert percentile == 1.0  # All 5 are below

    def test_lowest_volatility_returns_zero_percentile(self):
        """Test lowest volatility stock returns 0 percentile."""
        from g2.ml.volatility import compute_volatility_percentile

        all_vols = pd.Series([0.10, 0.20, 0.30, 0.40, 0.50])
        stock_vol = 0.05  # Lower than all

        percentile = compute_volatility_percentile(stock_vol, all_vols)

        assert percentile == 0.0  # None are below


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
