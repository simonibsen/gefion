"""Tests that MCP server uses unified predictions table."""
import pytest


class TestMCPServerUnifiedPredictions:
    """Test that MCP server references unified predictions table."""

    def test_query_predictions_uses_unified_table(self):
        """_query_predictions should query from 'predictions' not 'quantile_predictions'."""
        import importlib.util
        import inspect

        spec = importlib.util.spec_from_file_location(
            "mcp_server", "mcp-server/server.py"
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pytest.skip("Cannot import mcp-server/server.py (missing deps)")

        source = inspect.getsource(mod._query_predictions)
        assert "FROM predictions " in source or "FROM predictions\n" in source
        assert "quantile_predictions" not in source

    def test_tool_descriptions_use_unified_table(self):
        """Tool descriptions should reference 'predictions' table."""
        import pathlib

        server_src = pathlib.Path("mcp-server/server.py").read_text()
        # ml_predict tool description should reference predictions table
        assert "Stores results in predictions table" in server_src
        assert "Stores results in quantile_predictions table" not in server_src

    def test_query_predictions_supports_prediction_type(self):
        """query_predictions tool should have prediction_type parameter."""
        import pathlib

        server_src = pathlib.Path("mcp-server/server.py").read_text()
        assert "prediction_type" in server_src
