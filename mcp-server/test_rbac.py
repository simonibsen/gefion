#!/usr/bin/env python3
"""
Tests for MCP Role-Based Access Control (RBAC).

TDD tests - written before implementation.
"""

import asyncio
import json
import os
import pytest
from unittest.mock import patch, MagicMock


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestRoleConfiguration:
    """Test role detection from environment variable."""

    def test_default_role_is_operator(self):
        """When GEFION_MCP_ROLE is not set, default to operator (safer default)."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove GEFION_MCP_ROLE if present
            os.environ.pop('GEFION_MCP_ROLE', None)

            # Import after env is set
            import importlib
            import server
            importlib.reload(server)

            assert server.MCP_ROLE == "operator"

    def test_operator_role_from_env(self):
        """When GEFION_MCP_ROLE=operator, role should be operator."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            assert server.MCP_ROLE == "operator"

    def test_developer_role_from_env(self):
        """When GEFION_MCP_ROLE=developer, role should be developer."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'developer'}):
            import importlib
            import server
            importlib.reload(server)

            assert server.MCP_ROLE == "developer"


class TestToolFiltering:
    """Test tool filtering based on role."""

    def test_dev_status_available_for_developer(self):
        """Developer role should see dev_status tool."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'developer'}):
            import importlib
            import server
            importlib.reload(server)

            tools = run_async(server.list_tools())
            tool_names = [t.name for t in tools]

            assert "dev_status" in tool_names

    def test_dev_status_blocked_for_operator(self):
        """Operator role should NOT see dev_status tool."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            tools = run_async(server.list_tools())
            tool_names = [t.name for t in tools]

            assert "dev_status" not in tool_names

    def test_query_database_available_for_operator(self):
        """Operator role should see query_database tool."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            tools = run_async(server.list_tools())
            tool_names = [t.name for t in tools]

            assert "query_database" in tool_names

    def test_all_operational_tools_available_for_operator(self):
        """Operator role should see all operational tools."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            tools = run_async(server.list_tools())
            tool_names = [t.name for t in tools]

            # All these should be available to operator
            expected_tools = [
                "ml_dataset_build", "ml_train", "ml_predict", "ml_eval",
                "ml_train_classifier", "ml_predict_classifier",
                "query_predictions", "query_model_performance",
                "data_update", "features_list", "cross_sectional_compute",
                "query_database",
                "span_check", "trace_search", "trace_detail", "trace_compare",
                "system_status", "health_check", "docker_status",
                "strategy_list", "strategy_configs", "strategy_create_config",
                "get_role_info",  # New tool
            ]

            for tool in expected_tools:
                assert tool in tool_names, f"Expected {tool} to be available for operator"


class TestAccessDenied:
    """Test access denied for blocked tools (defense in depth)."""

    def test_dev_status_call_denied_for_operator(self):
        """Calling dev_status as operator should return access denied."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            result = run_async(server.call_tool("dev_status", {}))

            # Should return TextContent with error
            assert len(result) == 1
            response = json.loads(result[0].text)

            assert response["success"] is False
            assert "access denied" in response["error"].lower() or "not available" in response["error"].lower()

    def test_dev_status_call_allowed_for_developer(self):
        """Calling dev_status as developer should work."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'developer'}):
            import importlib
            import server
            importlib.reload(server)

            # Mock the _dev_status function to avoid file system dependencies
            with patch.object(server, '_dev_status', return_value={"success": True, "role": "developer"}):
                result = run_async(server.call_tool("dev_status", {}))

                assert len(result) == 1
                response = json.loads(result[0].text)

                # Should not be access denied
                if "error" in response:
                    assert "access denied" not in response["error"].lower()


class TestGetRoleInfo:
    """Test get_role_info tool."""

    def test_get_role_info_returns_operator(self):
        """get_role_info should return operator role and guidelines."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'operator'}):
            import importlib
            import server
            importlib.reload(server)

            result = run_async(server.call_tool("get_role_info", {}))

            assert len(result) == 1
            response = json.loads(result[0].text)

            assert response["success"] is True
            assert response["role"] == "operator"
            assert "description" in response
            assert "guidelines" in response
            assert isinstance(response["guidelines"], list)
            # Should include guidance about not modifying code
            guidelines_text = " ".join(response["guidelines"]).lower()
            assert "code" in guidelines_text or "source" in guidelines_text

    def test_get_role_info_returns_developer(self):
        """get_role_info should return developer role."""
        with patch.dict(os.environ, {'GEFION_MCP_ROLE': 'developer'}):
            import importlib
            import server
            importlib.reload(server)

            result = run_async(server.call_tool("get_role_info", {}))

            assert len(result) == 1
            response = json.loads(result[0].text)

            assert response["success"] is True
            assert response["role"] == "developer"

    def test_get_role_info_available_in_tool_list(self):
        """get_role_info should appear in tool list for both roles."""
        for role in ["developer", "operator"]:
            with patch.dict(os.environ, {'GEFION_MCP_ROLE': role}):
                import importlib
                import server
                importlib.reload(server)

                tools = run_async(server.list_tools())
                tool_names = [t.name for t in tools]

                assert "get_role_info" in tool_names, f"get_role_info should be in tool list for {role}"
