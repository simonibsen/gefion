"""Tests for the contextual chat component."""
import py_compile
from pathlib import Path

import pytest


UI_DIR = Path(__file__).parent.parent / "src" / "gefion" / "ui"


class TestChatComponentModule:
    """Chat component must exist and compile."""

    def test_chat_module_exists(self):
        """components/chat.py must exist."""
        assert (UI_DIR / "components" / "chat.py").exists()

    def test_chat_module_compiles(self):
        """components/chat.py must be valid Python."""
        path = UI_DIR / "components" / "chat.py"
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            pytest.fail(f"Syntax error: {e}")

    def test_exports_parse_command_input(self):
        """parse_command_input must be importable from chat module."""
        from gefion.ui.components.chat import parse_command_input
        assert callable(parse_command_input)

    def test_exports_render_chat_widget(self):
        """render_chat_widget must be importable from chat module."""
        from gefion.ui.components.chat import render_chat_widget
        assert callable(render_chat_widget)

    def test_exports_mcp_tool_map(self):
        """MCP_TOOL_MAP must be importable from chat module."""
        from gefion.ui.components.chat import MCP_TOOL_MAP
        assert isinstance(MCP_TOOL_MAP, dict)
        assert len(MCP_TOOL_MAP) > 10

    def test_exports_parse_stream_event(self):
        """parse_stream_event must be importable from chat module."""
        from gefion.ui.components.chat import parse_stream_event
        assert callable(parse_stream_event)


class TestParseCommandInput:
    """parse_command_input routing logic."""

    def test_natural_language_routes_to_ai(self):
        from gefion.ui.components.chat import parse_command_input
        cmd, display, mode = parse_command_input("what does margin mean?")
        assert mode == "ai"
        assert "claude" in cmd[0]

    def test_cli_command_routes_to_cli(self):
        from gefion.ui.components.chat import parse_command_input
        cmd, display, mode = parse_command_input("gefion ml predict-list")
        assert mode == "cli"
        assert "ml" in cmd
        assert "predict-list" in cmd

    def test_mcp_tool_name_routes_to_mcp(self):
        from gefion.ui.components.chat import parse_command_input
        cmd, display, mode = parse_command_input("data_update --exchange NASDAQ")
        assert mode == "mcp"

    def test_empty_input(self):
        from gefion.ui.components.chat import parse_command_input
        cmd, display, mode = parse_command_input("")
        assert cmd == []
        assert mode == ""


class TestParseStreamEvent:
    """Stream-JSON parsing."""

    def test_parses_text_event(self):
        import json
        from gefion.ui.components.chat import parse_stream_event
        event = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        })
        result = parse_stream_event(event)
        assert result["type"] == "text"
        assert result["text"] == "hello"

    def test_returns_none_for_invalid_json(self):
        from gefion.ui.components.chat import parse_stream_event
        assert parse_stream_event("not json") is None

    def test_parses_result_event(self):
        import json
        from gefion.ui.components.chat import parse_stream_event
        event = json.dumps({
            "type": "result",
            "result": "done",
            "duration_ms": 500,
            "total_cost_usd": 0.01,
        })
        result = parse_stream_event(event)
        assert result["type"] == "result"


class TestChatWidgetRendering:
    """Chat widget rendering requirements."""

    def test_chat_renders_form_with_input(self):
        content = (UI_DIR / "components" / "chat.py").read_text()
        assert "st.form(" in content
        assert "st.text_input(" in content
        assert "Ask about this page" in content

    def test_chat_has_page_context_param(self):
        """render_chat_widget must accept a page_context parameter."""
        import inspect
        from gefion.ui.components.chat import render_chat_widget
        sig = inspect.signature(render_chat_widget)
        assert "page_context" in sig.parameters


class TestAssistantStillWorks:
    """assistant.py must still import and compile after extraction."""

    def test_assistant_compiles(self):
        path = UI_DIR / "views" / "assistant.py"
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            pytest.fail(f"Syntax error: {e}")

    def test_assistant_imports_from_chat(self):
        """assistant.py should import shared logic from components.chat."""
        content = (UI_DIR / "views" / "assistant.py").read_text()
        assert "from gefion.ui.components.chat import" in content
