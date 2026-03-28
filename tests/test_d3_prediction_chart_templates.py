"""Tests for D3 prediction/ML chart templates — calibration, pred_vs_actual,
confusion_matrix, pipeline_health, portfolio, and accuracy_over_time.

TDD: These tests are written FIRST, before the templates exist.
"""
import json
from pathlib import Path

import jinja2
import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates"

NEW_TEMPLATES = [
    "calibration.html",
    "pred_vs_actual.html",
    "confusion_matrix.html",
    "pipeline_health.html",
    "portfolio.html",
    "accuracy_over_time.html",
]


# ---------------------------------------------------------------------------
# Template existence tests
# ---------------------------------------------------------------------------
class TestPredictionTemplatesExist:
    """All six new chart templates must exist as valid Jinja2 files."""

    @pytest.fixture
    def jinja_env(self):
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        )

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_template_file_exists(self, name):
        assert (TEMPLATES_DIR / name).exists(), f"Template {name} must exist"

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_template_is_valid_jinja2(self, jinja_env, name):
        try:
            jinja_env.get_template(name)
        except jinja2.TemplateSyntaxError as e:
            pytest.fail(f"Jinja2 syntax error in {name}: {e}")


# ---------------------------------------------------------------------------
# Structural tests — each template must follow the canonical pattern
# ---------------------------------------------------------------------------
class TestPredictionTemplateStructure:
    """Each template must follow the established pattern."""

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_starts_with_doctype(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert text.strip().startswith("<!DOCTYPE html>")

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_has_theme_css_injection(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ theme_css }}" in text

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_has_chart_container(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert 'id="chart"' in text

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_has_d3_script_injection(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ d3_script }}" in text

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_includes_utils_js(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{% include 'utils.js' %}" in text

    @pytest.mark.parametrize("name", NEW_TEMPLATES)
    def test_has_data_and_config_variables(self, name):
        text = (TEMPLATES_DIR / name).read_text()
        assert "{{ data_json }}" in text
        assert "{{ config_json }}" in text
        assert "{{ width }}" in text
        assert "{{ height }}" in text


# ---------------------------------------------------------------------------
# Rendering tests — templates produce valid HTML with embedded data
# ---------------------------------------------------------------------------
class TestCalibrationChart:
    """Calibration curve chart renders with diagonal and calibration line."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"predicted": 0.1, "observed": 0.08, "count": 50},
            {"predicted": 0.2, "observed": 0.22, "count": 45},
            {"predicted": 0.3, "observed": 0.28, "count": 60},
            {"predicted": 0.5, "observed": 0.52, "count": 40},
            {"predicted": 0.8, "observed": 0.75, "count": 30},
        ]

    @pytest.fixture
    def config(self):
        return {"title": "Calibration Curve", "model_name": "quantile_v3"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("calibration.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("calibration.html", sample_data, config)
        assert "0.08" in html
        assert "predicted" in html or "0.1" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("calibration.html", sample_data, config)
        assert "Calibration Curve" in html


class TestPredVsActualChart:
    """Predicted vs actual scatter plot renders correctly."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"predicted": 0.02, "actual": 0.015, "symbol": "AAPL", "date": "2026-03-25"},
            {"predicted": -0.01, "actual": 0.005, "symbol": "MSFT", "date": "2026-03-25"},
            {"predicted": 0.03, "actual": 0.028, "symbol": "GOOG", "date": "2026-03-25"},
        ]

    @pytest.fixture
    def config(self):
        return {"title": "Predicted vs Actual", "model_name": "quantile_v3"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pred_vs_actual.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pred_vs_actual.html", sample_data, config)
        assert "AAPL" in html
        assert "0.02" in html or "predicted" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pred_vs_actual.html", sample_data, config)
        assert "Predicted vs Actual" in html


class TestConfusionMatrixChart:
    """Confusion matrix heatmap renders correctly."""

    @pytest.fixture
    def sample_data(self):
        return {
            "labels": ["strong_down", "weak_down", "neutral", "weak_up", "strong_up"],
            "matrix": [
                [10, 5, 2, 1, 0],
                [4, 12, 3, 1, 0],
                [1, 3, 15, 4, 1],
                [0, 1, 4, 11, 3],
                [0, 0, 1, 3, 9],
            ],
        }

    @pytest.fixture
    def config(self):
        return {"title": "Confusion Matrix", "model_name": "trend_classifier_v2"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("confusion_matrix.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_labels(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("confusion_matrix.html", sample_data, config)
        assert "strong_down" in html
        assert "neutral" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("confusion_matrix.html", sample_data, config)
        assert "Confusion Matrix" in html


class TestPipelineHealthChart:
    """Pipeline health dashboard renders with multi-panel layout."""

    @pytest.fixture
    def sample_data(self):
        return {
            "freshness": [
                {"name": "OHLCV", "days_old": 2},
                {"name": "Features", "days_old": 5},
            ],
            "coverage": {"computed": 85, "total": 100},
            "predictions": [
                {"type": "quantile", "count": 500},
                {"type": "trend_class", "count": 186},
            ],
        }

    @pytest.fixture
    def config(self):
        return {"title": "Pipeline Health"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pipeline_health.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pipeline_health.html", sample_data, config)
        assert "OHLCV" in html
        assert "quantile" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("pipeline_health.html", sample_data, config)
        assert "Pipeline Health" in html


class TestPortfolioChart:
    """Portfolio chart renders with equity curve and metrics panel."""

    @pytest.fixture
    def sample_data(self):
        return {
            "equity": [
                {"date": "2026-01-01", "value": 100000, "drawdown": 0.0},
                {"date": "2026-02-01", "value": 105000, "drawdown": 0.0},
                {"date": "2026-03-01", "value": 102000, "drawdown": 0.0286},
            ],
            "metrics": {
                "total_return": 15.2,
                "sharpe": 1.8,
                "max_drawdown": -12.5,
                "win_rate": 58.3,
                "trades": 150,
            },
        }

    @pytest.fixture
    def config(self):
        return {"title": "Portfolio Performance", "strategy_name": "momentum_v2"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("portfolio.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("portfolio.html", sample_data, config)
        assert "100000" in html or "100,000" in html
        assert "15.2" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("portfolio.html", sample_data, config)
        assert "Portfolio Performance" in html


class TestAccuracyOverTimeChart:
    """Accuracy over time chart renders with multiple metric lines."""

    @pytest.fixture
    def sample_data(self):
        return [
            {"date": "2026-03-01", "accuracy": 0.65, "coverage_10": 0.12, "coverage_50": 0.48, "coverage_90": 0.88},
            {"date": "2026-03-08", "accuracy": 0.68, "coverage_10": 0.11, "coverage_50": 0.50, "coverage_90": 0.89},
            {"date": "2026-03-15", "accuracy": 0.63, "coverage_10": 0.13, "coverage_50": 0.47, "coverage_90": 0.87},
        ]

    @pytest.fixture
    def config(self):
        return {"title": "Accuracy Over Time", "model_name": "quantile_v3"}

    def test_renders_html(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("accuracy_over_time.html", sample_data, config)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_embeds_data(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("accuracy_over_time.html", sample_data, config)
        assert "0.65" in html
        assert "accuracy" in html

    def test_includes_title(self, sample_data, config):
        from gefion.charts.d3.base import render_d3_chart
        html = render_d3_chart("accuracy_over_time.html", sample_data, config)
        assert "Accuracy Over Time" in html
