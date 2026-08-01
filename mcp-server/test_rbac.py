#!/usr/bin/env python3
"""
Tests for MCP server after role mechanism removal (issue #172).

The developer/operator role gate (``GEFION_MCP_ROLE``) was vestigial: the
toggle was never switched and it guarded nothing dangerous (every
destructive tool stayed available to ``operator``; the only blocked tool
was ``dev_status``). It has been removed. The one valuable piece — the
tools-first behavioral guidance — is now always-on in the MCP server
instructions.

These tests assert the *absence* of the role concept and the *presence*
of the always-on guidance.
"""

import asyncio
import importlib
import json
import os

import pytest
from unittest.mock import patch


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def server_module():
    """Freshly-reloaded server module."""
    import server
    importlib.reload(server)
    return server


class TestNoRoleConcept:
    """The role mechanism is gone: no env read, no module-level state."""

    def test_no_mcp_role_attribute(self, server_module):
        assert not hasattr(server_module, "MCP_ROLE")

    def test_no_operator_blocked_tools(self, server_module):
        assert not hasattr(server_module, "OPERATOR_BLOCKED_TOOLS")

    def test_no_role_info(self, server_module):
        assert not hasattr(server_module, "ROLE_INFO")

    def test_role_env_var_ignored(self):
        """Setting GEFION_MCP_ROLE has no effect; the concept is gone."""
        with patch.dict(os.environ, {"GEFION_MCP_ROLE": "developer"}):
            import server
            importlib.reload(server)
            assert not hasattr(server, "MCP_ROLE")


class TestGetRoleInfoRemoved:
    """The get_role_info tool (schema + handler + dispatch) is gone."""

    def test_get_role_info_not_in_tool_list(self, server_module):
        tools = run_async(server_module.list_tools())
        assert "get_role_info" not in [t.name for t in tools]

    def test_get_role_info_handler_removed(self, server_module):
        assert not hasattr(server_module, "_get_role_info")

    def test_calling_get_role_info_is_unknown(self, server_module):
        result = run_async(server_module.call_tool("get_role_info", {}))
        assert len(result) == 1
        response = json.loads(result[0].text)
        assert response["success"] is False


class TestDevStatusAlwaysAvailable:
    """dev_status was the only role-gated tool; it is now always available."""

    def test_dev_status_in_tool_list(self, server_module):
        tools = run_async(server_module.list_tools())
        assert "dev_status" in [t.name for t in tools]

    def test_dev_status_not_access_denied(self, server_module):
        with patch.object(
            server_module,
            "_dev_status",
            return_value={"success": True},
        ):
            result = run_async(server_module.call_tool("dev_status", {}))
            response = json.loads(result[0].text)
            if "error" in response:
                assert "access denied" not in response["error"].lower()


class TestToolsFirstGuidanceAlwaysOn:
    """The behavioral guidance is unconditional in the server instructions."""

    def test_server_has_instructions(self, server_module):
        assert server_module.app.instructions

    def test_instructions_are_tools_first(self, server_module):
        text = server_module.app.instructions.lower()
        # Use the system's tools; don't reflexively write code.
        assert "tool" in text
        assert "code" in text
