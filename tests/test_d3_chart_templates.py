"""Tests for D3 chart templates — predictions, comparison, correlation,
volatility, and drawdown charts.

TDD: These tests are written FIRST, before the templates exist.
"""
import json
from pathlib import Path

import jinja2
import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates"


# ---------------------------------------------------------------------------
# Template existence tests
# ---------------------------------------------------------------------------
class TestChartTemplatesExist:
    """All five new chart templates must exist as valid Jinja2 files."""

    @pytest.fixture
    def jinja_env(self):
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        )

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_template_file_exists(self, name):
        assert (TEMPLATES_DIR / name).exists(), f"Template {name} must exist"

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_template_is_valid_jinja2(self, jinja_env, name):
        try:
            jinja_env.get_template(name)
        except jinja2.TemplateSyntaxError as e:
            pytest.fail(f"Jinja2 syntax error in {name}: {e}")


# ---------------------------------------------------------------------------
# Structural tests — each template must follow the canonical pattern
# ---------------------------------------------------------------------------
class TestTemplateStructure:
    """Each template must follow the candlestick pattern."""

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_starts_with_doctype(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert text.strip().startswith("<!DOCTYPE html>")

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_has_theme_css_injection(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ theme_css }}" in text

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_has_chart_container(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert 'id="chart"' in text

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_has_d3_script_injection(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ d3_script }}" in text

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_includes_utils_js(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{% include 'utils.js' %}" in text

    @pytest.mark.parametrize("name", [
        "predictions.html",
        "comparison.html",
        "correlation.html",
        "volatility.html",
        "drawdown.html",
    ])
    def test_has_data_and_config_variables(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ data_json }}" in text
        assert "{{ config_json }}" in text
        assert "{{ width }}" in text
        assert "{{ height }}" in text


# ---------------------------------------------------------------------------
# Rendering tests — templates produce valid HTML with embedded data
# ---------------------------------------------------------------------------
class TestPredictionsChart:
    """Predictions chart renders with price line and prediction bands."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"date": "2026-03-20", "close": 150.0, "q10": -0.05, "q50": 0.01, "q90": 0.08},
            {"date": "2026-03-21", "close": 152.0, "q10": -0.04, "q50": 0.02, "q90": 0.07},
            {"date": "2026-03-22", "close": 148.0, "q10": -0.06, "q50": 0.00, "q90": 0.09},
        ]

    @pytest.fixture
    def config(self):
        return {"symbol": "AAPL", "title": "AAPL Predictions", "horizon_days": 5}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("predictions.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("predictions.html", sample_data, config)
        assert "150.0" in html or "150" in html
        assert "q50" in html or "0.01" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("predictions.html", sample_data, config)
        assert "AAPL Predictions" in html


class TestComparisonChart:
    """Comparison chart renders with multi-symbol normalized data."""

    @pytest.fixture
    def sample_data(self):
        return {
            "symbols": [
                {"symbol": "AAPL", "data": [
                    {"date": "2026-03-20", "close": 150.0},
                    {"date": "2026-03-21", "close": 155.0},
                ]},
                {"symbol": "MSFT", "data": [
                    {"date": "2026-03-20", "close": 400.0},
                    {"date": "2026-03-21", "close": 410.0},
                ]},
            ]
        }

    @pytest.fixture
    def config(self):
        return {"title": "Symbol Comparison", "normalize": True}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("comparison.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_symbols(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("comparison.html", sample_data, config)
        assert "AAPL" in html
        assert "MSFT" in html


class TestCorrelationChart:
    """Correlation heatmap chart renders correctly."""

    @pytest.fixture
    def sample_data(self):
        return {
            "symbols": ["AAPL", "MSFT", "GOOG"],
            "matrix": [
                [1.0, 0.85, 0.72],
                [0.85, 1.0, 0.68],
                [0.72, 0.68, 1.0],
            ],
        }

    @pytest.fixture
    def config(self):
        return {"title": "Correlation Matrix"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("correlation.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_symbols(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("correlation.html", sample_data, config)
        assert "AAPL" in html
        assert "MSFT" in html
        assert "GOOG" in html

    def test_embeds_matrix_values(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("correlation.html", sample_data, config)
        assert "0.85" in html


class TestVolatilityChart:
    """Volatility chart renders with three subplots."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"date": "2026-03-20", "close": 150.0, "bb_upper": 160.0, "bb_middle": 150.0, "bb_lower": 140.0, "atr": 5.0, "hv": 0.25},
            {"date": "2026-03-21", "close": 152.0, "bb_upper": 162.0, "bb_middle": 151.0, "bb_lower": 141.0, "atr": 5.2, "hv": 0.26},
            {"date": "2026-03-22", "close": 148.0, "bb_upper": 158.0, "bb_middle": 149.0, "bb_lower": 139.0, "atr": 5.5, "hv": 0.28},
        ]

    @pytest.fixture
    def config(self):
        return {"symbol": "AAPL", "title": "AAPL Volatility", "window": 20}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("volatility.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("volatility.html", sample_data, config)
        assert "150" in html
        assert "bb_upper" in html or "160" in html


class TestDrawdownChart:
    """Drawdown chart renders with price and drawdown subplots."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"date": "2026-03-20", "close": 150.0, "drawdown": 0.0},
            {"date": "2026-03-21", "close": 145.0, "drawdown": -3.33},
            {"date": "2026-03-22", "close": 140.0, "drawdown": -6.67},
            {"date": "2026-03-23", "close": 148.0, "drawdown": -1.33},
        ]

    @pytest.fixture
    def config(self):
        return {"symbol": "AAPL", "title": "AAPL Drawdown"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("drawdown.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("drawdown.html", sample_data, config)
        assert "drawdown" in html or "-6.67" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("drawdown.html", sample_data, config)
        assert "AAPL Drawdown" in html
