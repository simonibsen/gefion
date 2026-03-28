"""Tests for D3 chart renderers — each create_* returns valid HTML."""
import pytest


SAMPLE_OHLCV = [
    {"date": "2026-03-20", "open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000000},
    {"date": "2026-03-21", "open": 103, "high": 107, "low": 101, "close": 106, "volume": 1200000},
    {"date": "2026-03-22", "open": 106, "high": 108, "low": 104, "close": 105, "volume": 900000},
    {"date": "2026-03-23", "open": 105, "high": 110, "low": 103, "close": 109, "volume": 1100000},
    {"date": "2026-03-24", "open": 109, "high": 112, "low": 107, "close": 111, "volume": 1300000},
]


class TestCandlestickRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(SAMPLE_OHLCV, "AAPL")
        assert "<html" in html.lower() or "<!doctype" in html.lower()
        assert "d3" in html

    def test_contains_symbol(self):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(SAMPLE_OHLCV, "TSLA")
        assert "TSLA" in html

    def test_custom_title(self):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(SAMPLE_OHLCV, "AAPL", title="My Chart")
        assert "My Chart" in html


class TestPredictionRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_prediction_chart
        data = [{"date": "2026-03-20", "close": 100, "q10": -0.02, "q50": 0.01, "q90": 0.04}]
        html = create_prediction_chart(data, [], "AAPL")
        assert "d3" in html

    def test_contains_prediction_refs(self):
        from gefion.charts.d3.renderers import create_prediction_chart
        data = [{"date": "2026-03-20", "close": 100, "q10": -0.02, "q50": 0.01, "q90": 0.04}]
        html = create_prediction_chart(data, [], "AAPL")
        assert "q50" in html or "q10" in html


class TestEquityCurveRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        data = [{"date": "2026-03-20", "equity": 10000, "drawdown": 0},
                {"date": "2026-03-21", "equity": 10200, "drawdown": 0},
                {"date": "2026-03-22", "equity": 9900, "drawdown": -0.03}]
        html = create_equity_curve_chart(data)
        assert "d3" in html

    def test_with_drawdown(self):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        data = [{"date": "2026-03-20", "equity": 10000, "drawdown": 0}]
        html = create_equity_curve_chart(data, show_drawdown=True)
        assert "drawdown" in html.lower()


class TestFeatureRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_feature_chart
        ohlcv = [{"date": "2026-03-20", "close": 100}]
        features = {"rsi_14": [{"date": "2026-03-20", "value": 55.0}]}
        html = create_feature_chart(ohlcv, features, "AAPL")
        assert "d3" in html


class TestComparisonRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_comparison_chart
        data = {"AAPL": [{"date": "2026-03-20", "close": 150}],
                "MSFT": [{"date": "2026-03-20", "close": 300}]}
        html = create_comparison_chart(data)
        assert "d3" in html

    def test_contains_all_symbols(self):
        from gefion.charts.d3.renderers import create_comparison_chart
        data = {"AAPL": [{"date": "2026-03-20", "close": 150}],
                "GOOGL": [{"date": "2026-03-20", "close": 140}]}
        html = create_comparison_chart(data)
        assert "AAPL" in html
        assert "GOOGL" in html


class TestCorrelationRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_correlation_matrix
        data = {"AAPL": [{"date": "2026-03-20", "close": 150}],
                "MSFT": [{"date": "2026-03-20", "close": 300}]}
        html = create_correlation_matrix(data)
        assert "d3" in html


class TestSectorHeatmapRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_sector_heatmap
        data = {"Technology": {"AAPL": 2.5, "MSFT": 1.8}}
        html = create_sector_heatmap(data)
        assert "d3" in html

    def test_contains_sectors(self):
        from gefion.charts.d3.renderers import create_sector_heatmap
        data = {"Technology": {"AAPL": 2.5}, "Healthcare": {"JNJ": -0.5}}
        html = create_sector_heatmap(data)
        assert "Technology" in html


class TestVolatilityRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_volatility_chart
        html = create_volatility_chart(SAMPLE_OHLCV, "AAPL")
        assert "d3" in html


class TestDrawdownRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_drawdown_chart
        html = create_drawdown_chart(SAMPLE_OHLCV, "AAPL")
        assert "d3" in html


class TestRollingReturnsRenderer:

    def test_returns_html(self):
        from gefion.charts.d3.renderers import create_rolling_returns_chart
        data = {"AAPL": SAMPLE_OHLCV}
        html = create_rolling_returns_chart(data)
        assert "d3" in html
