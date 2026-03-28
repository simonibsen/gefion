"""Tests for D3 chart suggestions and generic chart rendering."""
import pytest


class TestSuggestVisualization:
    """suggest_visualization returns useful suggestions."""

    def test_returns_dict_with_chart_type(self):
        from gefion.charts.d3.suggestions import suggest_visualization
        result = suggest_visualization({"page_name": "Dashboard"})
        assert isinstance(result, dict)
        assert "chart_type" in result
        assert "reason" in result

    def test_ml_page_suggests_predictions(self):
        from gefion.charts.d3.suggestions import suggest_visualization
        ctx = {"page_name": "ML Pipeline", "data_stats": {"prediction_totals": {"quantile": 100}}}
        result = suggest_visualization(ctx)
        assert result["chart_type"] in ("predictions", "pred_vs_actual", "calibration")

    def test_dashboard_suggests_pipeline(self):
        from gefion.charts.d3.suggestions import suggest_visualization
        result = suggest_visualization({"page_name": "Dashboard"})
        assert result["chart_type"] == "pipeline_health"

    def test_handles_empty_context(self):
        from gefion.charts.d3.suggestions import suggest_visualization
        result = suggest_visualization({})
        assert "chart_type" in result


class TestRenderGenericChart:
    """render_generic_chart produces HTML for any chart type."""

    def test_line_chart(self):
        from gefion.charts.d3.suggestions import render_generic_chart
        data = {"symbols": [{"symbol": "TEST", "data": [{"date": "2026-03-28", "close": 100}]}]}
        html = render_generic_chart("line", data, {"title": "Test Line"})
        assert "d3" in html
        assert "Test Line" in html

    def test_scatter_chart(self):
        from gefion.charts.d3.suggestions import render_generic_chart
        data = [{"predicted": 0.01, "actual": 0.02, "symbol": "AAPL", "date": "2026-03-28"}]
        html = render_generic_chart("scatter", data)
        assert "d3" in html

    def test_heatmap_chart(self):
        from gefion.charts.d3.suggestions import render_generic_chart
        data = {"symbols": ["A", "B"], "matrix": [[1.0, 0.5], [0.5, 1.0]]}
        html = render_generic_chart("heatmap", data)
        assert "d3" in html

    def test_unknown_type_uses_base(self):
        from gefion.charts.d3.suggestions import render_generic_chart
        html = render_generic_chart("unknown_type", {"test": True})
        assert isinstance(html, str)

    def test_specific_chart_types(self):
        from gefion.charts.d3.suggestions import render_generic_chart
        for ctype in ("pipeline_health", "calibration", "confusion_matrix"):
            html = render_generic_chart(ctype, {})
            assert "d3" in html, f"Chart type {ctype} should render D3"
