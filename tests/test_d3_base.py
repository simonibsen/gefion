"""Tests for D3 chart framework — template engine, serialization, theme."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest


TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates"


class TestD3Base:
    """Template loading and data serialization."""

    def test_load_template_returns_jinja_template(self):
        from gefion.charts.d3.base import load_template
        import jinja2
        tmpl = load_template("base.html")
        assert isinstance(tmpl, jinja2.Template)

    def test_load_template_raises_on_missing(self):
        from gefion.charts.d3.base import load_template
        with pytest.raises(Exception):
            load_template("nonexistent_chart.html")

    def test_serialize_handles_dates(self):
        from gefion.charts.d3.base import serialize_for_d3
        result = serialize_for_d3({"date": date(2026, 3, 28)})
        parsed = json.loads(result)
        assert parsed["date"] == "2026-03-28"

    def test_serialize_handles_none(self):
        from gefion.charts.d3.base import serialize_for_d3
        result = serialize_for_d3({"val": None})
        assert "null" in result

    def test_serialize_handles_decimal(self):
        from gefion.charts.d3.base import serialize_for_d3
        result = serialize_for_d3({"price": Decimal("123.45")})
        parsed = json.loads(result)
        assert parsed["price"] == 123.45

    def test_serialize_escapes_script_tags(self):
        from gefion.charts.d3.base import serialize_for_d3
        result = serialize_for_d3({"text": "</script><script>alert(1)</script>"})
        assert "</script>" not in result or "\\u003c/script>" in result.lower()

    def test_render_d3_chart_returns_html(self):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("base.html", {"test": True})
        assert isinstance(html, str)
        assert "<html" in html.lower() or "<!doctype" in html.lower() or "<div" in html.lower()

    def test_render_d3_chart_embeds_data(self):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("base.html", {"symbol": "AAPL", "price": 150.0})
        assert "AAPL" in html

    def test_render_d3_chart_includes_d3_js(self):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("base.html", {})
        assert "d3" in html.lower()


class TestD3Theme:
    """Theme constants and CSS."""

    def test_colors_has_required_keys(self):
        from gefion.charts.d3.theme import COLORS
        required = ["up", "down", "price_line", "ma_20", "ma_50", "ma_200",
                     "grid", "text", "background"]
        for key in required:
            assert key in COLORS, f"Missing color key: {key}"

    def test_chart_palette_has_enough_colors(self):
        from gefion.charts.d3.theme import CHART_PALETTE
        assert len(CHART_PALETTE) >= 8

    def test_get_css_returns_string(self):
        from gefion.charts.d3.theme import get_css
        css = get_css()
        assert isinstance(css, str)
        assert len(css) > 50


class TestD3Templates:
    """Template existence and validity."""

    def test_base_template_exists(self):
        assert (TEMPLATES_DIR / "base.html").exists()

    def test_utils_js_exists(self):
        assert (TEMPLATES_DIR / "utils.js").exists()

    def test_candlestick_template_exists(self):
        assert (TEMPLATES_DIR / "candlestick.html").exists()

    def test_equity_curve_template_exists(self):
        assert (TEMPLATES_DIR / "equity_curve.html").exists()

    def test_features_template_exists(self):
        assert (TEMPLATES_DIR / "features.html").exists()

    def test_sector_heatmap_template_exists(self):
        assert (TEMPLATES_DIR / "sector_heatmap.html").exists()

    def test_rolling_returns_template_exists(self):
        assert (TEMPLATES_DIR / "rolling_returns.html").exists()

    def test_all_templates_valid_jinja2(self):
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        )
        for tmpl_file in TEMPLATES_DIR.glob("*.html"):
            try:
                env.get_template(tmpl_file.name)
            except jinja2.TemplateSyntaxError as e:
                pytest.fail(f"Jinja2 syntax error in {tmpl_file.name}: {e}")

    def test_vendor_d3_exists(self):
        vendor_dir = TEMPLATES_DIR.parent / "vendor"
        d3_files = list(vendor_dir.glob("d3*.js"))
        assert len(d3_files) >= 1, "Vendored D3.js must exist"


class TestCandlestickChart:
    """Candlestick chart renders correctly."""

    @pytest.fixture
    def sample_ohlcv(self):
        return [
            {"date": "2026-03-25", "open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000000},
            {"date": "2026-03-26", "open": 103, "high": 107, "low": 101, "close": 106, "volume": 1200000},
            {"date": "2026-03-27", "open": 106, "high": 108, "low": 104, "close": 105, "volume": 900000},
        ]

    def test_candlestick_returns_html(self, sample_ohlcv):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(sample_ohlcv, "AAPL")
        assert isinstance(html, str)
        assert "AAPL" in html

    def test_candlestick_contains_d3(self, sample_ohlcv):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(sample_ohlcv, "AAPL")
        assert "d3" in html

    def test_candlestick_embeds_data(self, sample_ohlcv):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(sample_ohlcv, "AAPL")
        assert "103" in html  # close price
        assert "1000000" in html or "1e6" in html.lower()  # volume

    def test_candlestick_with_title(self, sample_ohlcv):
        from gefion.charts.d3.renderers import create_candlestick_chart
        html = create_candlestick_chart(sample_ohlcv, "AAPL", title="Test Chart")
        assert "Test Chart" in html


class TestEquityCurveChart:
    """Equity curve D3 chart renders correctly."""

    @pytest.fixture
    def sample_equity(self):
        return [
            {"date": "2026-03-20", "equity": 100000, "drawdown": 0.0},
            {"date": "2026-03-21", "equity": 100500, "drawdown": 0.0},
            {"date": "2026-03-22", "equity": 99800, "drawdown": 0.007},
            {"date": "2026-03-23", "equity": 101000, "drawdown": 0.0},
            {"date": "2026-03-24", "equity": 100200, "drawdown": 0.008},
        ]

    def test_equity_curve_returns_html(self, sample_equity):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        html = create_equity_curve_chart(sample_equity)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html or "<html" in html.lower()

    def test_equity_curve_contains_d3(self, sample_equity):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        html = create_equity_curve_chart(sample_equity)
        assert "d3" in html

    def test_equity_curve_embeds_data(self, sample_equity):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        html = create_equity_curve_chart(sample_equity)
        assert "100000" in html
        assert "100500" in html

    def test_equity_curve_with_title(self, sample_equity):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        html = create_equity_curve_chart(sample_equity, title="My Equity")
        assert "My Equity" in html

    def test_equity_curve_drawdown_toggle(self, sample_equity):
        from gefion.charts.d3.renderers import create_equity_curve_chart
        html_with = create_equity_curve_chart(sample_equity, show_drawdown=True)
        html_without = create_equity_curve_chart(sample_equity, show_drawdown=False)
        assert "show_drawdown" in html_with or "drawdown" in html_with.lower()
        assert isinstance(html_without, str)


class TestFeaturesChart:
    """Features D3 chart renders correctly."""

    @pytest.fixture
    def sample_ohlcv(self):
        return [
            {"date": "2026-03-25", "close": 150.0},
            {"date": "2026-03-26", "close": 152.0},
            {"date": "2026-03-27", "close": 148.0},
        ]

    @pytest.fixture
    def sample_features(self):
        return {
            "rsi_14": [
                {"date": "2026-03-25", "value": 55.0},
                {"date": "2026-03-26", "value": 62.0},
                {"date": "2026-03-27", "value": 45.0},
            ],
            "macd": [
                {"date": "2026-03-25", "value": 1.2},
                {"date": "2026-03-26", "value": 1.8},
                {"date": "2026-03-27", "value": -0.5},
            ],
        }

    def test_features_returns_html(self, sample_ohlcv, sample_features):
        from gefion.charts.d3.renderers import create_feature_chart
        html = create_feature_chart(sample_ohlcv, sample_features, "AAPL")
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html or "<html" in html.lower()

    def test_features_contains_d3(self, sample_ohlcv, sample_features):
        from gefion.charts.d3.renderers import create_feature_chart
        html = create_feature_chart(sample_ohlcv, sample_features, "AAPL")
        assert "d3" in html

    def test_features_embeds_data(self, sample_ohlcv, sample_features):
        from gefion.charts.d3.renderers import create_feature_chart
        html = create_feature_chart(sample_ohlcv, sample_features, "AAPL")
        assert "150" in html
        assert "rsi_14" in html

    def test_features_with_title(self, sample_ohlcv, sample_features):
        from gefion.charts.d3.renderers import create_feature_chart
        html = create_feature_chart(sample_ohlcv, sample_features, "AAPL", title="Feature View")
        assert "Feature View" in html

    def test_features_symbol_in_config(self, sample_ohlcv, sample_features):
        from gefion.charts.d3.renderers import create_feature_chart
        html = create_feature_chart(sample_ohlcv, sample_features, "MSFT")
        assert "MSFT" in html


class TestSectorHeatmapChart:
    """Sector heatmap D3 chart renders correctly."""

    @pytest.fixture
    def sample_sector_data(self):
        return {
            "sectors": [
                {
                    "name": "Technology",
                    "return_pct": 12.5,
                    "symbols": [
                        {"symbol": "AAPL", "return_pct": 15.2},
                        {"symbol": "MSFT", "return_pct": 9.8},
                    ],
                },
                {
                    "name": "Healthcare",
                    "return_pct": -3.1,
                    "symbols": [
                        {"symbol": "JNJ", "return_pct": -5.2},
                        {"symbol": "PFE", "return_pct": -1.0},
                    ],
                },
            ]
        }

    def test_sector_heatmap_returns_html(self, sample_sector_data):
        from gefion.charts.d3.renderers import create_sector_heatmap_d3
        html = create_sector_heatmap_d3(sample_sector_data)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html or "<html" in html.lower()

    def test_sector_heatmap_contains_d3(self, sample_sector_data):
        from gefion.charts.d3.renderers import create_sector_heatmap_d3
        html = create_sector_heatmap_d3(sample_sector_data)
        assert "d3" in html

    def test_sector_heatmap_embeds_data(self, sample_sector_data):
        from gefion.charts.d3.renderers import create_sector_heatmap_d3
        html = create_sector_heatmap_d3(sample_sector_data)
        assert "Technology" in html
        assert "AAPL" in html

    def test_sector_heatmap_with_title(self, sample_sector_data):
        from gefion.charts.d3.renderers import create_sector_heatmap_d3
        html = create_sector_heatmap_d3(sample_sector_data, title="Sector View")
        assert "Sector View" in html

    def test_sector_heatmap_with_period(self, sample_sector_data):
        from gefion.charts.d3.renderers import create_sector_heatmap_d3
        html = create_sector_heatmap_d3(sample_sector_data, period="1M")
        assert "1M" in html


class TestRollingReturnsChart:
    """Rolling returns D3 chart renders correctly."""

    @pytest.fixture
    def sample_rolling_data(self):
        return {
            "windows": {
                "20": [
                    {"date": "2026-03-25", "value": 0.05},
                    {"date": "2026-03-26", "value": 0.03},
                    {"date": "2026-03-27", "value": -0.02},
                ],
                "60": [
                    {"date": "2026-03-25", "value": 0.12},
                    {"date": "2026-03-26", "value": 0.10},
                    {"date": "2026-03-27", "value": 0.08},
                ],
            }
        }

    def test_rolling_returns_html(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        html = create_rolling_returns_d3(sample_rolling_data, "AAPL")
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html or "<html" in html.lower()

    def test_rolling_returns_contains_d3(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        html = create_rolling_returns_d3(sample_rolling_data, "AAPL")
        assert "d3" in html

    def test_rolling_returns_embeds_data(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        html = create_rolling_returns_d3(sample_rolling_data, "AAPL")
        assert "0.05" in html
        assert "0.12" in html

    def test_rolling_returns_with_title(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        html = create_rolling_returns_d3(sample_rolling_data, "AAPL", title="Rolling View")
        assert "Rolling View" in html

    def test_rolling_returns_symbol_in_config(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        html = create_rolling_returns_d3(sample_rolling_data, "TSLA")
        assert "TSLA" in html

    def test_rolling_returns_window_labels(self, sample_rolling_data):
        from gefion.charts.d3.renderers import create_rolling_returns_d3
        labels = {"20": "20-day", "60": "60-day"}
        html = create_rolling_returns_d3(
            sample_rolling_data, "AAPL", window_labels=labels
        )
        assert "20-day" in html
