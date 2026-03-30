"""Tests for D3 experiment chart templates and renderers.

TDD: Tests written first, before implementation.
"""
import pytest


class TestExperimentTrialsChart:
    """Tests for the trial performance scatter chart."""

    def test_template_exists(self):
        """experiment_trials.html template should exist."""
        from pathlib import Path
        template = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates" / "experiment_trials.html"
        assert template.exists(), f"Template not found: {template}"

    def test_renderer_exists(self):
        """create_experiment_trials() should be importable."""
        from gefion.charts.d3.renderers import create_experiment_trials
        assert create_experiment_trials is not None

    def test_renders_html_with_svg(self):
        """Renderer should return HTML string containing SVG."""
        from gefion.charts.d3.renderers import create_experiment_trials

        trials = [
            {"trial": 1, "score": 1.5, "params": {"lr": 0.01}, "promoted": True},
            {"trial": 2, "score": 0.8, "params": {"lr": 0.1}, "promoted": False},
            {"trial": 3, "score": 2.1, "params": {"lr": 0.05}, "promoted": True},
        ]
        html = create_experiment_trials(trials, title="Test Trials")
        assert isinstance(html, str)
        assert "<svg" in html.lower() or "svg" in html.lower()
        assert len(html) > 100


class TestExperimentFDRChart:
    """Tests for the FDR cycle summary chart."""

    def test_template_exists(self):
        """experiment_fdr.html template should exist."""
        from pathlib import Path
        template = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates" / "experiment_fdr.html"
        assert template.exists(), f"Template not found: {template}"

    def test_renderer_exists(self):
        """create_experiment_fdr() should be importable."""
        from gefion.charts.d3.renderers import create_experiment_fdr
        assert create_experiment_fdr is not None

    def test_renders_html_with_threshold(self):
        """FDR chart should contain threshold line reference."""
        from gefion.charts.d3.renderers import create_experiment_fdr

        experiments = [
            {"name": "exp-1", "p_value": 0.003, "promoted": True},
            {"name": "exp-2", "p_value": 0.42, "promoted": False},
            {"name": "exp-3", "p_value": 0.01, "promoted": True},
        ]
        html = create_experiment_fdr(experiments, fdr_rate=0.10, title="FDR Summary")
        assert isinstance(html, str)
        assert len(html) > 100


class TestExperimentHeatmapChart:
    """Tests for the parameter sensitivity heatmap."""

    def test_template_exists(self):
        """experiment_heatmap.html template should exist."""
        from pathlib import Path
        template = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates" / "experiment_heatmap.html"
        assert template.exists(), f"Template not found: {template}"

    def test_renderer_exists(self):
        """create_experiment_heatmap() should be importable."""
        from gefion.charts.d3.renderers import create_experiment_heatmap
        assert create_experiment_heatmap is not None


class TestExperimentFeaturesChart:
    """Tests for the feature importance before/after chart."""

    def test_template_exists(self):
        """experiment_features.html template should exist."""
        from pathlib import Path
        template = Path(__file__).parent.parent / "src" / "gefion" / "charts" / "d3" / "templates" / "experiment_features.html"
        assert template.exists(), f"Template not found: {template}"

    def test_renderer_exists(self):
        """create_experiment_features() should be importable."""
        from gefion.charts.d3.renderers import create_experiment_features
        assert create_experiment_features is not None

    def test_renders_html(self):
        """Feature importance chart should render."""
        from gefion.charts.d3.renderers import create_experiment_features

        features = [
            {"name": "rsi_14", "importance_before": 0.15, "importance_after": 0.12},
            {"name": "macd", "importance_before": 0.10, "importance_after": 0.08},
            {"name": "frac_diff", "importance_before": 0.0, "importance_after": 0.20},
        ]
        html = create_experiment_features(features, title="Feature Importance")
        assert isinstance(html, str)
        assert len(html) > 100
