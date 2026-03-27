"""Tests for page context functions across all UI views."""
import py_compile
from pathlib import Path

import pytest

UI_DIR = Path(__file__).parent.parent / "src" / "gefion" / "ui"

# Views that should have get_page_context()
DATA_VIEWS = [
    "dashboard",
    "ml",
    "data",
    "features",
    "charts",
    "backtest",
    "experiments",
]

# Views that don't need context (static/config pages)
STATIC_VIEWS = ["documentation", "settings", "assistant"]


class TestPageContextFunctions:
    """Every data view must export get_page_context()."""

    def test_all_data_views_have_get_page_context(self):
        """Each view with dynamic data must define get_page_context()."""
        for view in DATA_VIEWS:
            content = (UI_DIR / "views" / f"{view}.py").read_text()
            assert f"def get_page_context(" in content, (
                f"{view}.py missing get_page_context() function"
            )

    def test_page_context_returns_dict_with_page_name(self):
        """get_page_context() must return a dict with at least page_name."""
        for view in DATA_VIEWS:
            mod = __import__(f"gefion.ui.views.{view}", fromlist=["get_page_context"])
            get_ctx = getattr(mod, "get_page_context", None)
            assert get_ctx is not None, f"{view} has no get_page_context"
            result = get_ctx()
            assert isinstance(result, dict), f"{view}.get_page_context() must return dict"
            assert "page_name" in result, f"{view}.get_page_context() must include page_name"

    def test_static_views_do_not_have_get_page_context(self):
        """Static views should NOT define get_page_context (no useful context)."""
        for view in STATIC_VIEWS:
            content = (UI_DIR / "views" / f"{view}.py").read_text()
            assert "def get_page_context(" not in content, (
                f"{view}.py should not have get_page_context — it's a static/config page"
            )

    def test_page_context_never_raises(self):
        """get_page_context() must never raise, even without DB access."""
        for view in DATA_VIEWS:
            mod = __import__(f"gefion.ui.views.{view}", fromlist=["get_page_context"])
            # Should work even if DB is unavailable
            result = mod.get_page_context()
            assert isinstance(result, dict)


class TestAppContextIntegration:
    """Each view must render the chat widget itself (not app.py)."""

    def test_views_call_render_chat_widget(self):
        """Data views must call render_chat_widget inside their render function."""
        for view in DATA_VIEWS:
            content = (UI_DIR / "views" / f"{view}.py").read_text()
            assert "render_chat_widget" in content, (
                f"{view}.py must call render_chat_widget"
            )

    def test_assistant_does_not_have_chat_widget(self):
        """AI Actions page has its own full assistant, no chat widget."""
        content = (UI_DIR / "views" / "assistant.py").read_text()
        assert "render_chat_widget" not in content
