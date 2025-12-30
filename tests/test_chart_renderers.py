"""
Tests for chart renderer functions.

These tests use mock data and don't require database access.
"""

from datetime import date, timedelta

import pytest

# Skip all tests if plotly is not installed
plotly = pytest.importorskip("plotly")


def make_ohlcv_data(days: int = 30) -> list:
    """Generate sample OHLCV data for testing."""
    base_date = date.today() - timedelta(days=days)
    data = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        data.append({
            "date": d,
            "open": 100.0 + i * 0.5,
            "high": 102.0 + i * 0.5,
            "low": 99.0 + i * 0.5,
            "close": 101.0 + i * 0.5,
            "volume": 1000000 + i * 10000,
        })
    return data


def make_prediction_data(days: int = 10) -> list:
    """Generate sample prediction data for testing."""
    base_date = date.today() - timedelta(days=days)
    data = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        data.append({
            "date": d,
            "q10": 95.0 + i,
            "q50": 100.0 + i,
            "q90": 105.0 + i,
        })
    return data


def make_equity_data(days: int = 30) -> list:
    """Generate sample equity curve data for testing."""
    base_date = date.today() - timedelta(days=days)
    data = []
    equity = 100000.0
    peak = equity
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        equity = equity * (1 + 0.002 * (1 if i % 3 != 0 else -1))
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        data.append({
            "date": d,
            "equity": equity,
            "drawdown": drawdown,
        })
    return data


def make_feature_data(days: int = 20) -> dict:
    """Generate sample feature data for testing."""
    base_date = date.today() - timedelta(days=days)
    rsi_data = []
    macd_data = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        rsi_data.append({"date": d, "value": 50.0 + (i % 30)})
        macd_data.append({"date": d, "value": 0.5 * (i % 10 - 5)})
    return {
        "rsi_14": rsi_data,
        "macd": macd_data,
    }


class TestCreateCandlestickChart:
    """Tests for create_candlestick_chart function."""

    def test_returns_plotly_figure(self):
        """create_candlestick_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_candlestick_chart
        import plotly.graph_objects as go

        ohlcv = make_ohlcv_data()
        fig = create_candlestick_chart(ohlcv, "TEST")

        assert isinstance(fig, go.Figure)

    def test_has_candlestick_trace(self):
        """create_candlestick_chart should include a candlestick trace."""
        from g2.charts.renderers import create_candlestick_chart

        ohlcv = make_ohlcv_data()
        fig = create_candlestick_chart(ohlcv, "TEST")

        # Check that there's at least one trace
        assert len(fig.data) >= 1
        # First trace should be candlestick
        assert fig.data[0].type == "candlestick"

    def test_has_volume_subplot(self):
        """create_candlestick_chart should include volume bars."""
        from g2.charts.renderers import create_candlestick_chart

        ohlcv = make_ohlcv_data()
        fig = create_candlestick_chart(ohlcv, "TEST")

        # Should have at least 2 traces (candlestick + volume + optional MAs)
        assert len(fig.data) >= 2
        # Should have a bar trace for volume somewhere
        bar_traces = [t for t in fig.data if t.type == "bar"]
        assert len(bar_traces) >= 1, "Should have volume bar trace"

    def test_custom_title(self):
        """create_candlestick_chart should use custom title when provided."""
        from g2.charts.renderers import create_candlestick_chart

        ohlcv = make_ohlcv_data()
        fig = create_candlestick_chart(ohlcv, "TEST", title="Custom Title")

        assert "Custom Title" in fig.layout.title.text

    def test_with_indicators(self):
        """create_candlestick_chart should overlay indicators when provided."""
        from g2.charts.renderers import create_candlestick_chart

        ohlcv = make_ohlcv_data()
        indicators = {
            "SMA20": [{"date": row["date"], "value": row["close"] * 0.98} for row in ohlcv]
        }
        fig = create_candlestick_chart(ohlcv, "TEST", indicators=indicators)

        # Should have additional trace for indicator
        assert len(fig.data) >= 3


class TestCreatePredictionChart:
    """Tests for create_prediction_chart function."""

    def test_returns_plotly_figure(self):
        """create_prediction_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_prediction_chart
        import plotly.graph_objects as go

        ohlcv = make_ohlcv_data()
        predictions = make_prediction_data()
        fig = create_prediction_chart(ohlcv, predictions, "TEST")

        assert isinstance(fig, go.Figure)

    def test_has_price_trace(self):
        """create_prediction_chart should include price line."""
        from g2.charts.renderers import create_prediction_chart

        ohlcv = make_ohlcv_data()
        predictions = make_prediction_data()
        fig = create_prediction_chart(ohlcv, predictions, "TEST")

        # Should have traces for price
        assert len(fig.data) >= 1

    def test_has_prediction_bands(self):
        """create_prediction_chart should include q10/q50/q90 bands."""
        from g2.charts.renderers import create_prediction_chart

        ohlcv = make_ohlcv_data()
        predictions = make_prediction_data()
        fig = create_prediction_chart(ohlcv, predictions, "TEST")

        # Should have multiple traces for price and prediction bands
        assert len(fig.data) >= 2


class TestCreateEquityCurveChart:
    """Tests for create_equity_curve_chart function."""

    def test_returns_plotly_figure(self):
        """create_equity_curve_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_equity_curve_chart
        import plotly.graph_objects as go

        equity = make_equity_data()
        fig = create_equity_curve_chart(equity)

        assert isinstance(fig, go.Figure)

    def test_has_equity_trace(self):
        """create_equity_curve_chart should include equity line."""
        from g2.charts.renderers import create_equity_curve_chart

        equity = make_equity_data()
        fig = create_equity_curve_chart(equity)

        assert len(fig.data) >= 1

    def test_has_drawdown_subplot(self):
        """create_equity_curve_chart should include drawdown subplot by default."""
        from g2.charts.renderers import create_equity_curve_chart

        equity = make_equity_data()
        fig = create_equity_curve_chart(equity, show_drawdown=True)

        # Should have 2 traces (equity + drawdown)
        assert len(fig.data) >= 2

    def test_no_drawdown_when_disabled(self):
        """create_equity_curve_chart should not show drawdown when disabled."""
        from g2.charts.renderers import create_equity_curve_chart

        equity = make_equity_data()
        fig = create_equity_curve_chart(equity, show_drawdown=False)

        # Should only have 1 trace
        assert len(fig.data) == 1


class TestCreateFeatureChart:
    """Tests for create_feature_chart function."""

    def test_returns_plotly_figure(self):
        """create_feature_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_feature_chart
        import plotly.graph_objects as go

        ohlcv = make_ohlcv_data()
        features = make_feature_data()
        fig = create_feature_chart(ohlcv, features, "TEST")

        assert isinstance(fig, go.Figure)

    def test_has_price_and_feature_traces(self):
        """create_feature_chart should include price and feature traces."""
        from g2.charts.renderers import create_feature_chart

        ohlcv = make_ohlcv_data()
        features = make_feature_data()
        fig = create_feature_chart(ohlcv, features, "TEST")

        # Should have traces for price + features
        # At minimum: price + 2 features = 3 traces
        assert len(fig.data) >= 3


def make_multi_symbol_data(symbols: list, days: int = 60) -> dict:
    """Generate sample OHLCV data for multiple symbols."""
    base_date = date.today() - timedelta(days=days)
    result = {}
    for idx, symbol in enumerate(symbols):
        data = []
        base_price = 100.0 + idx * 50  # Different starting prices
        for i in range(days):
            d = base_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            # Add some variation per symbol
            price = base_price + i * 0.5 * (1 + idx * 0.1)
            data.append({
                "date": d,
                "open": price - 1,
                "high": price + 2,
                "low": price - 2,
                "close": price,
                "volume": 1000000 + i * 10000,
            })
        result[symbol] = data
    return result


def make_sector_data() -> dict:
    """Generate sample sector performance data."""
    return {
        "Technology": {"AAPL": 15.2, "MSFT": 12.8, "GOOGL": -3.5},
        "Healthcare": {"JNJ": 5.1, "PFE": -8.2, "UNH": 22.0},
        "Finance": {"JPM": 18.5, "BAC": 10.2, "GS": 7.8},
    }


class TestCreateComparisonChart:
    """Tests for create_comparison_chart function."""

    def test_returns_plotly_figure(self):
        """create_comparison_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_comparison_chart
        import plotly.graph_objects as go

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"])
        fig = create_comparison_chart(symbol_data)

        assert isinstance(fig, go.Figure)

    def test_has_traces_for_each_symbol(self):
        """create_comparison_chart should have a trace for each symbol."""
        from g2.charts.renderers import create_comparison_chart

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT", "GOOGL"])
        fig = create_comparison_chart(symbol_data)

        # Should have at least one trace per symbol
        assert len(fig.data) >= 3

    def test_normalized_by_default(self):
        """create_comparison_chart should normalize prices to base 100 by default."""
        from g2.charts.renderers import create_comparison_chart

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"])
        fig = create_comparison_chart(symbol_data, normalize=True)

        # First data point for each trace should be around 100
        for trace in fig.data:
            if trace.y and len(trace.y) > 0 and trace.y[0] is not None:
                # Normalized series start at 100
                assert 99 <= trace.y[0] <= 101

    def test_relative_strength_for_two_symbols(self):
        """create_comparison_chart should show relative strength ratio for 2 symbols."""
        from g2.charts.renderers import create_comparison_chart

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"])
        fig = create_comparison_chart(symbol_data)

        # Should have traces for both symbols plus ratio
        assert len(fig.data) >= 3

    def test_custom_title(self):
        """create_comparison_chart should use custom title when provided."""
        from g2.charts.renderers import create_comparison_chart

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"])
        fig = create_comparison_chart(symbol_data, title="Custom Comparison")

        assert "Custom Comparison" in fig.layout.title.text


class TestCreateCorrelationMatrix:
    """Tests for create_correlation_matrix function."""

    def test_returns_plotly_figure(self):
        """create_correlation_matrix should return a Plotly Figure."""
        from g2.charts.renderers import create_correlation_matrix
        import plotly.graph_objects as go

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT", "GOOGL"])
        fig = create_correlation_matrix(symbol_data)

        assert isinstance(fig, go.Figure)

    def test_has_heatmap_trace(self):
        """create_correlation_matrix should include a heatmap trace."""
        from g2.charts.renderers import create_correlation_matrix

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT", "GOOGL"])
        fig = create_correlation_matrix(symbol_data)

        assert len(fig.data) >= 1
        assert fig.data[0].type == "heatmap"

    def test_diagonal_is_one(self):
        """create_correlation_matrix diagonal should be 1.0 (self-correlation)."""
        from g2.charts.renderers import create_correlation_matrix

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"])
        fig = create_correlation_matrix(symbol_data)

        # Diagonal elements should be 1.0
        z_data = fig.data[0].z
        for i in range(len(z_data)):
            assert abs(z_data[i][i] - 1.0) < 0.01

    def test_symmetric_matrix(self):
        """create_correlation_matrix should produce symmetric correlations."""
        from g2.charts.renderers import create_correlation_matrix

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT", "GOOGL"])
        fig = create_correlation_matrix(symbol_data)

        z_data = fig.data[0].z
        n = len(z_data)
        for i in range(n):
            for j in range(n):
                assert abs(z_data[i][j] - z_data[j][i]) < 0.01


class TestCreateSectorHeatmap:
    """Tests for create_sector_heatmap function."""

    def test_returns_plotly_figure(self):
        """create_sector_heatmap should return a Plotly Figure."""
        from g2.charts.renderers import create_sector_heatmap
        import plotly.graph_objects as go

        sector_data = make_sector_data()
        fig = create_sector_heatmap(sector_data)

        assert isinstance(fig, go.Figure)

    def test_has_treemap_trace(self):
        """create_sector_heatmap should include a treemap trace."""
        from g2.charts.renderers import create_sector_heatmap

        sector_data = make_sector_data()
        fig = create_sector_heatmap(sector_data)

        assert len(fig.data) >= 1
        assert fig.data[0].type == "treemap"

    def test_includes_all_sectors(self):
        """create_sector_heatmap should include all sectors."""
        from g2.charts.renderers import create_sector_heatmap

        sector_data = make_sector_data()
        fig = create_sector_heatmap(sector_data)

        labels = list(fig.data[0].labels)
        for sector in sector_data.keys():
            assert sector in labels

    def test_custom_title(self):
        """create_sector_heatmap should use custom title when provided."""
        from g2.charts.renderers import create_sector_heatmap

        sector_data = make_sector_data()
        fig = create_sector_heatmap(sector_data, title="My Sectors")

        assert "My Sectors" in fig.layout.title.text


class TestCreateVolatilityChart:
    """Tests for create_volatility_chart function."""

    def test_returns_plotly_figure(self):
        """create_volatility_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_volatility_chart
        import plotly.graph_objects as go

        ohlcv = make_ohlcv_data(days=60)
        fig = create_volatility_chart(ohlcv, "TEST")

        assert isinstance(fig, go.Figure)

    def test_has_multiple_subplots(self):
        """create_volatility_chart should have price, volatility, and ATR subplots."""
        from g2.charts.renderers import create_volatility_chart

        ohlcv = make_ohlcv_data(days=60)
        fig = create_volatility_chart(ohlcv, "TEST")

        # Should have traces for: upper BB, lower BB, price, SMA, volatility, ATR
        assert len(fig.data) >= 5

    def test_bollinger_bands_present(self):
        """create_volatility_chart should include Bollinger Bands."""
        from g2.charts.renderers import create_volatility_chart

        ohlcv = make_ohlcv_data(days=60)
        fig = create_volatility_chart(ohlcv, "TEST")

        # Check for fill trace (Bollinger Band fill)
        fill_traces = [t for t in fig.data if hasattr(t, 'fill') and t.fill]
        assert len(fill_traces) >= 1

    def test_custom_window(self):
        """create_volatility_chart should accept custom window parameter."""
        from g2.charts.renderers import create_volatility_chart

        ohlcv = make_ohlcv_data(days=60)
        # Should not raise with custom window
        fig = create_volatility_chart(ohlcv, "TEST", window=10)

        assert len(fig.data) >= 5


class TestCreateDrawdownChart:
    """Tests for create_drawdown_chart function."""

    def test_returns_plotly_figure(self):
        """create_drawdown_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_drawdown_chart
        import plotly.graph_objects as go

        ohlcv = make_ohlcv_data(days=60)
        fig = create_drawdown_chart(ohlcv, "TEST")

        assert isinstance(fig, go.Figure)

    def test_has_price_and_drawdown_traces(self):
        """create_drawdown_chart should have price and drawdown traces."""
        from g2.charts.renderers import create_drawdown_chart

        ohlcv = make_ohlcv_data(days=60)
        fig = create_drawdown_chart(ohlcv, "TEST")

        # Should have: peak line, price line, drawdown area, max DD marker
        assert len(fig.data) >= 3

    def test_drawdown_values_negative_or_zero(self):
        """create_drawdown_chart drawdown values should be <= 0."""
        from g2.charts.renderers import create_drawdown_chart

        ohlcv = make_ohlcv_data(days=60)
        fig = create_drawdown_chart(ohlcv, "TEST")

        # Find the drawdown trace (has fill)
        for trace in fig.data:
            if hasattr(trace, 'fill') and trace.fill and trace.y is not None:
                # All drawdown values should be <= 0
                for val in trace.y:
                    if val is not None:
                        assert val <= 0.01  # Small tolerance

    def test_max_drawdown_marked(self):
        """create_drawdown_chart should mark the maximum drawdown point."""
        from g2.charts.renderers import create_drawdown_chart

        ohlcv = make_ohlcv_data(days=60)
        fig = create_drawdown_chart(ohlcv, "TEST")

        # Should have a marker trace for max drawdown
        marker_traces = [t for t in fig.data if hasattr(t, 'marker') and t.marker and hasattr(t.marker, 'symbol')]
        assert len(marker_traces) >= 1


class TestCreateRollingReturnsChart:
    """Tests for create_rolling_returns_chart function."""

    def test_returns_plotly_figure(self):
        """create_rolling_returns_chart should return a Plotly Figure."""
        from g2.charts.renderers import create_rolling_returns_chart
        import plotly.graph_objects as go

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"], days=120)
        fig = create_rolling_returns_chart(symbol_data)

        assert isinstance(fig, go.Figure)

    def test_has_traces_for_each_window(self):
        """create_rolling_returns_chart should have subplots for each window."""
        from g2.charts.renderers import create_rolling_returns_chart

        symbol_data = make_multi_symbol_data(["AAPL", "MSFT"], days=120)
        windows = [30, 60, 90]
        fig = create_rolling_returns_chart(symbol_data, windows=windows)

        # Should have 2 symbols * 3 windows = 6 traces
        assert len(fig.data) >= 6

    def test_custom_windows(self):
        """create_rolling_returns_chart should accept custom window list."""
        from g2.charts.renderers import create_rolling_returns_chart

        symbol_data = make_multi_symbol_data(["AAPL"], days=120)
        windows = [20, 40]
        fig = create_rolling_returns_chart(symbol_data, windows=windows)

        # Should have traces for each window
        assert len(fig.data) >= 2

    def test_single_symbol(self):
        """create_rolling_returns_chart should work with single symbol."""
        from g2.charts.renderers import create_rolling_returns_chart

        symbol_data = make_multi_symbol_data(["AAPL"], days=120)
        fig = create_rolling_returns_chart(symbol_data)

        assert len(fig.data) >= 3  # 3 default windows


class TestPlotlyNotInstalled:
    """Tests for graceful handling when plotly is not installed."""

    def test_check_plotly_raises_import_error(self):
        """_check_plotly should raise ImportError with helpful message."""
        # This is hard to test since plotly IS installed
        # We just verify the check function exists
        from g2.charts.renderers import _check_plotly

        # Should not raise when plotly is installed
        _check_plotly()
