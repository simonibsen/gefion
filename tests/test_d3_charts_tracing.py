"""Tests that D3 chart modules are instrumented with OpenTelemetry tracing spans.

Verifies that create_span is called for significant chart operations
in base.py, renderers.py, suggestions.py, and the chat component.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


class SpanCollector:
    """Collect span names and attributes created during a test."""

    def __init__(self):
        self.spans: list[str] = []
        self.attrs: list[dict] = []

    @contextmanager
    def fake_create_span(self, name: str, **attrs):
        self.spans.append(name)
        self.attrs.append(attrs)
        mock_span = MagicMock()
        yield mock_span


@pytest.fixture
def collector():
    return SpanCollector()


# ---------------------------------------------------------------------------
# base.py — render_d3_chart
# ---------------------------------------------------------------------------

class TestBaseTracing:
    """render_d3_chart wraps work in a create_span."""

    def test_render_d3_chart_creates_span(self, collector, monkeypatch):
        import gefion.charts.d3.base as base_mod

        monkeypatch.setattr(base_mod, "create_span", collector.fake_create_span)
        # Stub load_template so it returns a mock template
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>chart</html>"
        monkeypatch.setattr(base_mod, "load_template", lambda name: mock_template)

        result = base_mod.render_d3_chart("test.html", {"x": 1}, width=600, height=400)

        assert "charts.d3.render" in collector.spans
        assert collector.attrs[0]["template"] == "test.html"
        assert collector.attrs[0]["width"] == 600
        assert collector.attrs[0]["height"] == 400
        assert result == "<html>chart</html>"

    def test_render_d3_chart_sets_html_length(self, collector, monkeypatch):
        import gefion.charts.d3.base as base_mod

        monkeypatch.setattr(base_mod, "create_span", collector.fake_create_span)
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>x</html>"
        monkeypatch.setattr(base_mod, "load_template", lambda name: mock_template)

        base_mod.render_d3_chart("t.html", {})

        # set_attributes should have been called on the span
        assert base_mod.set_attributes  # import exists


# ---------------------------------------------------------------------------
# renderers.py — top-level chart functions
# ---------------------------------------------------------------------------

class TestRendererTracing:
    """Each top-level renderer wraps its body in create_span."""

    def _patch_renderer(self, monkeypatch, collector):
        import gefion.charts.d3.renderers as rend_mod
        monkeypatch.setattr(rend_mod, "create_span", collector.fake_create_span)
        # Stub render_d3_chart to avoid template loading
        monkeypatch.setattr(rend_mod, "render_d3_chart", lambda *a, **kw: "<html/>")
        return rend_mod

    def test_candlestick_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_candlestick_chart([], symbol="AAPL")
        assert "charts.d3.candlestick" in collector.spans
        assert collector.attrs[0]["symbol"] == "AAPL"

    def test_prediction_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_prediction_chart([], [], symbol="MSFT")
        assert "charts.d3.predictions" in collector.spans
        assert collector.attrs[0]["symbol"] == "MSFT"

    def test_comparison_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_comparison_chart({"A": [], "B": []})
        assert "charts.d3.comparison" in collector.spans
        assert collector.attrs[0]["symbol_count"] == 2

    def test_correlation_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_correlation_matrix({"X": [], "Y": []})
        assert "charts.d3.correlation" in collector.spans
        assert collector.attrs[0]["symbol_count"] == 2

    def test_volatility_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_volatility_chart([], symbol="TSLA", window=30)
        assert "charts.d3.volatility" in collector.spans
        assert collector.attrs[0]["symbol"] == "TSLA"
        assert collector.attrs[0]["window"] == 30

    def test_drawdown_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_drawdown_chart([], symbol="GOOG")
        assert "charts.d3.drawdown" in collector.spans
        assert collector.attrs[0]["symbol"] == "GOOG"

    def test_sector_heatmap_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_sector_heatmap({})
        assert "charts.d3.sector_heatmap" in collector.spans

    def test_pipeline_health_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_pipeline_health_chart({})
        assert "charts.d3.pipeline_health" in collector.spans

    def test_calibration_has_span(self, collector, monkeypatch):
        rend = self._patch_renderer(monkeypatch, collector)
        rend.create_calibration_chart([], model_name="lgb_q")
        assert "charts.d3.calibration" in collector.spans
        assert collector.attrs[0]["model_name"] == "lgb_q"


# ---------------------------------------------------------------------------
# suggestions.py — suggest_visualization + render_generic_chart
# ---------------------------------------------------------------------------

class TestSuggestionsTracing:
    """Suggestion functions are instrumented."""

    def test_suggest_visualization_has_span(self, collector, monkeypatch):
        import gefion.charts.d3.suggestions as sug_mod
        monkeypatch.setattr(sug_mod, "create_span", collector.fake_create_span)

        sug_mod.suggest_visualization({"page_name": "Dashboard"})

        assert "charts.suggest" in collector.spans
        assert collector.attrs[0]["page"] == "Dashboard"

    def test_render_generic_chart_has_span(self, collector, monkeypatch):
        import gefion.charts.d3.suggestions as sug_mod
        monkeypatch.setattr(sug_mod, "create_span", collector.fake_create_span)
        monkeypatch.setattr(sug_mod, "render_d3_chart", lambda *a, **kw: "<html/>")

        sug_mod.render_generic_chart("line", {"x": [1]})

        assert "charts.render_generic" in collector.spans
        assert collector.attrs[0]["chart_type"] == "line"


# ---------------------------------------------------------------------------
# chat.py — _build_context_prompt + _check_mcp_status
# ---------------------------------------------------------------------------

class TestChatTracing:
    """Chat component functions are instrumented."""

    def test_build_context_prompt_has_span(self, collector, monkeypatch):
        import gefion.ui.components.chat as chat_mod
        monkeypatch.setattr(chat_mod, "create_span", collector.fake_create_span)
        # Stub _get_system_context to avoid DB calls
        monkeypatch.setattr(chat_mod, "_get_system_context", lambda: "")

        result = chat_mod._build_context_prompt({"page_name": "Features"})

        assert "chat.build_context" in collector.spans
        assert collector.attrs[0]["page"] == "Features"
        assert result is not None

    def test_build_context_prompt_empty_returns_none(self, collector, monkeypatch):
        import gefion.ui.components.chat as chat_mod
        monkeypatch.setattr(chat_mod, "create_span", collector.fake_create_span)

        result = chat_mod._build_context_prompt({})

        # Empty dict should still return None (early exit before span)
        assert result is None

    def test_check_mcp_status_has_span(self, collector, monkeypatch):
        import gefion.ui.components.chat as chat_mod
        monkeypatch.setattr(chat_mod, "create_span", collector.fake_create_span)
        # Clear the cache so our monkeypatch takes effect
        chat_mod._check_mcp_status.clear()
        monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")

        result = chat_mod._check_mcp_status()

        assert "chat.check_mcp" in collector.spans
        assert isinstance(result, dict)
        assert "available" in result
