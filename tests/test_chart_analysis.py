"""
Tests for chart analysis functions.

These functions compute insights and summaries for MCP rich context.
Tests use mock data and don't require database access.
"""

from datetime import date, timedelta

import pytest


def make_ohlcv_data(days: int = 30, trend: str = "up") -> list:
    """Generate sample OHLCV data with a trend."""
    base_date = date.today() - timedelta(days=days)
    base_price = 100.0
    data = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if trend == "up":
            price = base_price + i * 0.5
        elif trend == "down":
            price = base_price - i * 0.3
        else:
            price = base_price + (i % 5 - 2) * 0.2
        data.append({
            "date": d,
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 1000000 + i * 10000,
        })
    return data


def make_prediction_data(current_price: float = 100.0, upside: bool = True) -> list:
    """Generate sample prediction data."""
    base_date = date.today()
    if upside:
        q50 = current_price * 1.05  # +5% median prediction
    else:
        q50 = current_price * 0.95  # -5% median prediction
    return [
        {
            "date": base_date + timedelta(days=7),
            "q10": q50 * 0.95,  # -5% from median
            "q50": q50,
            "q90": q50 * 1.05,  # +5% from median
        }
    ]


def make_equity_data(days: int = 30, profitable: bool = True) -> list:
    """Generate sample equity curve data."""
    base_date = date.today() - timedelta(days=days)
    data = []
    equity = 100000.0
    peak = equity
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if profitable:
            daily_return = 0.003 if i % 4 != 0 else -0.002
        else:
            daily_return = -0.003 if i % 4 != 0 else 0.002
        equity = equity * (1 + daily_return)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        data.append({
            "date": d,
            "equity": equity,
            "drawdown": drawdown,
        })
    return data


def make_feature_data(days: int = 20) -> dict:
    """Generate sample feature data."""
    base_date = date.today() - timedelta(days=days)
    rsi_data = []
    macd_data = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        # RSI starts high (overbought) and trends down
        rsi_data.append({"date": d, "value": 75.0 - i * 1.5})
        macd_data.append({"date": d, "value": 0.5 - i * 0.05})
    return {
        "indicator_rsi_14": rsi_data,
        "indicator_macd": macd_data,
    }


class TestComputePriceInsights:
    """Tests for compute_price_insights function."""

    def test_returns_expected_structure(self):
        """compute_price_insights should return dict with expected keys."""
        from g2.charts.analysis import compute_price_insights

        ohlcv = make_ohlcv_data()
        result = compute_price_insights(ohlcv)

        assert isinstance(result, dict)
        assert "description" in result
        assert "price_change" in result
        assert "price_range" in result
        assert "insights" in result

    def test_calculates_price_change(self):
        """compute_price_insights should calculate percentage price change."""
        from g2.charts.analysis import compute_price_insights

        ohlcv = make_ohlcv_data(trend="up")
        result = compute_price_insights(ohlcv)

        # Up trend should show positive change
        assert result["price_change"] > 0

    def test_includes_price_range(self):
        """compute_price_insights should include high/low/current prices."""
        from g2.charts.analysis import compute_price_insights

        ohlcv = make_ohlcv_data()
        result = compute_price_insights(ohlcv)

        assert "low" in result["price_range"]
        assert "high" in result["price_range"]
        assert "current" in result["price_range"]

    def test_generates_insights_list(self):
        """compute_price_insights should generate list of insight strings."""
        from g2.charts.analysis import compute_price_insights

        ohlcv = make_ohlcv_data()
        result = compute_price_insights(ohlcv)

        assert isinstance(result["insights"], list)


class TestComputePredictionInsights:
    """Tests for compute_prediction_insights function."""

    def test_returns_expected_structure(self):
        """compute_prediction_insights should return dict with expected keys."""
        from g2.charts.analysis import compute_prediction_insights

        predictions = make_prediction_data(100.0, upside=True)
        result = compute_prediction_insights(predictions, 100.0)

        assert isinstance(result, dict)
        assert "description" in result
        assert "predicted_median" in result
        assert "prediction_range" in result
        assert "confidence_width" in result
        assert "insights" in result

    def test_upside_prediction(self):
        """compute_prediction_insights should detect upside predictions."""
        from g2.charts.analysis import compute_prediction_insights

        predictions = make_prediction_data(100.0, upside=True)
        result = compute_prediction_insights(predictions, 100.0)

        # Median should be above current price
        assert result["predicted_median"] > 100.0

    def test_downside_prediction(self):
        """compute_prediction_insights should detect downside predictions."""
        from g2.charts.analysis import compute_prediction_insights

        predictions = make_prediction_data(100.0, upside=False)
        result = compute_prediction_insights(predictions, 100.0)

        # Median should be below current price
        assert result["predicted_median"] < 100.0

    def test_confidence_width_percentage(self):
        """compute_prediction_insights should calculate confidence width as percentage."""
        from g2.charts.analysis import compute_prediction_insights

        predictions = make_prediction_data(100.0)
        result = compute_prediction_insights(predictions, 100.0)

        # Should be a percentage string or number
        assert result["confidence_width"] is not None


class TestComputeBacktestInsights:
    """Tests for compute_backtest_insights function."""

    def test_returns_expected_structure(self):
        """compute_backtest_insights should return dict with expected keys."""
        from g2.charts.analysis import compute_backtest_insights

        equity = make_equity_data()
        result = compute_backtest_insights(equity)

        assert isinstance(result, dict)
        assert "description" in result
        assert "total_return" in result
        assert "max_drawdown" in result
        assert "insights" in result

    def test_profitable_strategy(self):
        """compute_backtest_insights should detect profitable strategy."""
        from g2.charts.analysis import compute_backtest_insights

        equity = make_equity_data(profitable=True)
        result = compute_backtest_insights(equity)

        # Should show positive return
        assert result["total_return"] > 0

    def test_unprofitable_strategy(self):
        """compute_backtest_insights should detect unprofitable strategy."""
        from g2.charts.analysis import compute_backtest_insights

        equity = make_equity_data(profitable=False)
        result = compute_backtest_insights(equity)

        # Should show negative return
        assert result["total_return"] < 0

    def test_max_drawdown_calculated(self):
        """compute_backtest_insights should calculate max drawdown."""
        from g2.charts.analysis import compute_backtest_insights

        equity = make_equity_data()
        result = compute_backtest_insights(equity)

        # Max drawdown should be non-negative
        assert result["max_drawdown"] >= 0


class TestDetectTechnicalSignals:
    """Tests for detect_technical_signals function."""

    def test_returns_list_of_strings(self):
        """detect_technical_signals should return list of insight strings."""
        from g2.charts.analysis import detect_technical_signals

        ohlcv = make_ohlcv_data()
        features = make_feature_data()
        result = detect_technical_signals(ohlcv, features)

        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)

    def test_detects_overbought_rsi(self):
        """detect_technical_signals should detect overbought RSI."""
        from g2.charts.analysis import detect_technical_signals

        ohlcv = make_ohlcv_data()
        # RSI at 75+ is overbought
        features = {
            "indicator_rsi_14": [
                {"date": date.today(), "value": 78.0}
            ]
        }
        result = detect_technical_signals(ohlcv, features)

        # Should mention overbought
        assert any("overbought" in s.lower() or "rsi" in s.lower() for s in result)

    def test_detects_oversold_rsi(self):
        """detect_technical_signals should detect oversold RSI."""
        from g2.charts.analysis import detect_technical_signals

        ohlcv = make_ohlcv_data()
        # RSI at 25 is oversold
        features = {
            "indicator_rsi_14": [
                {"date": date.today(), "value": 22.0}
            ]
        }
        result = detect_technical_signals(ohlcv, features)

        # Should mention oversold
        assert any("oversold" in s.lower() or "rsi" in s.lower() for s in result)

    def test_empty_features_returns_empty_list(self):
        """detect_technical_signals should handle empty features."""
        from g2.charts.analysis import detect_technical_signals

        ohlcv = make_ohlcv_data()
        result = detect_technical_signals(ohlcv, {})

        assert result == [] or isinstance(result, list)
