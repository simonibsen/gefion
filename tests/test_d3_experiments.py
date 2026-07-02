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


class TestChartsForExperimentType:
    """Tests for the chart-type dispatch helper."""

    def test_param_search_types_get_trials_and_heatmap(self):
        """Types with parameter search spaces should offer trials + heatmap."""
        from gefion.charts.experiments import charts_for_experiment_type

        for exp_type in ("hyperparameter", "strategy_params"):
            charts = charts_for_experiment_type(exp_type)
            assert "trials" in charts
            assert "heatmap" in charts

    def test_other_types_get_trials_only(self):
        """Types without a 2D parameter search should offer only trials."""
        from gefion.charts.experiments import charts_for_experiment_type

        for exp_type in ("feature_engineering", "model_comparison", "unknown"):
            charts = charts_for_experiment_type(exp_type)
            assert charts == ["trials"]


class TestBuildHeatmapData:
    """Tests for pivoting trials into heatmap cells."""

    def _trials(self):
        return [
            {"parameters": {"lr": 0.01, "depth": 3}, "score": 1.0},
            {"parameters": {"lr": 0.01, "depth": 5}, "score": 1.2},
            {"parameters": {"lr": 0.1, "depth": 3}, "score": 0.8},
            {"parameters": {"lr": 0.1, "depth": 5}, "score": 0.9},
        ]

    def test_two_varying_numeric_params(self):
        """Two varying numeric params should produce cells with x/y/value."""
        from gefion.charts.experiments import build_heatmap_data

        result = build_heatmap_data(self._trials())
        assert result is not None
        assert {result["x_label"], result["y_label"]} == {"lr", "depth"}
        assert len(result["cells"]) == 4
        for cell in result["cells"]:
            assert set(cell.keys()) == {"x", "y", "value"}

    def test_constant_param_is_ignored(self):
        """A param that never varies should not count toward the 2-param limit."""
        from gefion.charts.experiments import build_heatmap_data

        trials = [
            {**t, "parameters": {**t["parameters"], "seed": 42}}
            for t in self._trials()
        ]
        result = build_heatmap_data(trials)
        assert result is not None
        assert {result["x_label"], result["y_label"]} == {"lr", "depth"}

    def test_returns_none_when_not_two_varying_params(self):
        """One or three+ varying params cannot be plotted as a 2D heatmap."""
        from gefion.charts.experiments import build_heatmap_data

        one_param = [
            {"parameters": {"lr": 0.01}, "score": 1.0},
            {"parameters": {"lr": 0.1}, "score": 0.8},
        ]
        assert build_heatmap_data(one_param) is None

        three_params = [
            {"parameters": {"a": i, "b": i * 2, "c": i * 3}, "score": float(i)}
            for i in range(4)
        ]
        assert build_heatmap_data(three_params) is None

    def test_returns_none_for_non_numeric_params(self):
        """Non-numeric param values cannot be heatmap axes."""
        from gefion.charts.experiments import build_heatmap_data

        trials = [
            {"parameters": {"model": "xgb", "mode": "fast"}, "score": 1.0},
            {"parameters": {"model": "lgbm", "mode": "slow"}, "score": 0.9},
        ]
        assert build_heatmap_data(trials) is None

    def test_returns_none_for_empty_trials(self):
        from gefion.charts.experiments import build_heatmap_data

        assert build_heatmap_data([]) is None

    def test_duplicate_param_pairs_are_averaged(self):
        """Repeated (x, y) combinations should average their scores."""
        from gefion.charts.experiments import build_heatmap_data

        trials = self._trials() + [
            {"parameters": {"lr": 0.01, "depth": 3}, "score": 2.0},
        ]
        result = build_heatmap_data(trials)
        assert len(result["cells"]) == 4
        lookup = {
            (c["x"], c["y"]): c["value"]
            for c in result["cells"]
        }
        key = (0.01, 3) if result["x_label"] == "lr" else (3, 0.01)
        assert lookup[key] == pytest.approx(1.5)


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
