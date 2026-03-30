"""Tests for autonomous experiment framework MCP tools."""
import pathlib

import pytest


SERVER_PATH = pathlib.Path("mcp-server/server.py")


def _read_server_source() -> str:
    return SERVER_PATH.read_text()


class TestExperimentDiscoverTool:
    """Test experiment_discover tool definition and handler."""

    def test_tool_definition_exists(self):
        src = _read_server_source()
        assert 'name="experiment_discover"' in src

    def test_tool_description(self):
        src = _read_server_source()
        assert "Discover available data sources and experiment opportunities" in src

    def test_handler_dispatch(self):
        src = _read_server_source()
        assert 'name == "experiment_discover"' in src

    def test_handler_function_exists(self):
        src = _read_server_source()
        assert "async def _experiment_discover(" in src

    def test_handler_uses_correct_cli_command(self):
        src = _read_server_source()
        assert '"experiment", "discover"' in src

    def test_handler_passes_json_flag(self):
        """Handler should pass --json to the CLI."""
        src = _read_server_source()
        # Find the discover handler and check it has --json
        idx = src.index("async def _experiment_discover(")
        handler_block = src[idx:idx + 500]
        assert '"--json"' in handler_block


class TestExperimentCycleStartTool:
    """Test experiment_cycle_start tool definition and handler."""

    def test_tool_definition_exists(self):
        src = _read_server_source()
        assert 'name="experiment_cycle_start"' in src

    def test_tool_description(self):
        src = _read_server_source()
        assert "Start a new experiment cycle" in src

    def test_has_optional_name_param(self):
        src = _read_server_source()
        # The name param should exist in the cycle_start tool definition
        idx = src.index('name="experiment_cycle_start"')
        block = src[idx:idx + 800]
        assert '"name"' in block

    def test_has_fdr_rate_param(self):
        src = _read_server_source()
        idx = src.index('name="experiment_cycle_start"')
        block = src[idx:idx + 800]
        assert '"fdr_rate"' in block

    def test_has_holdout_weeks_param(self):
        src = _read_server_source()
        idx = src.index('name="experiment_cycle_start"')
        block = src[idx:idx + 800]
        assert '"holdout_weeks"' in block

    def test_has_max_experiments_param(self):
        src = _read_server_source()
        idx = src.index('name="experiment_cycle_start"')
        block = src[idx:idx + 800]
        assert '"max_experiments"' in block

    def test_handler_dispatch(self):
        src = _read_server_source()
        assert 'name == "experiment_cycle_start"' in src

    def test_handler_function_exists(self):
        src = _read_server_source()
        assert "async def _experiment_cycle_start(" in src

    def test_handler_uses_correct_cli_command(self):
        src = _read_server_source()
        assert '"experiment", "cycle-start"' in src

    def test_handler_passes_json_flag(self):
        src = _read_server_source()
        idx = src.index("async def _experiment_cycle_start(")
        handler_block = src[idx:idx + 800]
        assert '"--json"' in handler_block


class TestExperimentCycleStatusTool:
    """Test experiment_cycle_status tool definition and handler."""

    def test_tool_definition_exists(self):
        src = _read_server_source()
        assert 'name="experiment_cycle_status"' in src

    def test_tool_description(self):
        src = _read_server_source()
        assert "Get status of an experiment cycle" in src

    def test_cycle_id_required(self):
        src = _read_server_source()
        idx = src.index('name="experiment_cycle_status"')
        block = src[idx:idx + 500]
        assert '"cycle_id"' in block
        assert '"required"' in block

    def test_handler_dispatch(self):
        src = _read_server_source()
        assert 'name == "experiment_cycle_status"' in src

    def test_handler_function_exists(self):
        src = _read_server_source()
        assert "async def _experiment_cycle_status(" in src

    def test_handler_uses_correct_cli_command(self):
        src = _read_server_source()
        assert '"experiment", "cycle-status"' in src

    def test_handler_passes_json_flag(self):
        src = _read_server_source()
        idx = src.index("async def _experiment_cycle_status(")
        handler_block = src[idx:idx + 500]
        assert '"--json"' in handler_block


class TestPrinciplesListTool:
    """Test principles_list tool definition and handler."""

    def test_tool_definition_exists(self):
        src = _read_server_source()
        assert 'name="principles_list"' in src

    def test_tool_description(self):
        src = _read_server_source()
        assert "List principles from the quantitative finance catalog" in src

    def test_has_domain_param(self):
        src = _read_server_source()
        idx = src.index('name="principles_list"')
        block = src[idx:idx + 800]
        assert '"domain"' in block

    def test_has_experiment_type_param(self):
        src = _read_server_source()
        idx = src.index('name="principles_list"')
        block = src[idx:idx + 800]
        assert '"experiment_type"' in block

    def test_has_status_param(self):
        src = _read_server_source()
        idx = src.index('name="principles_list"')
        block = src[idx:idx + 800]
        assert '"status"' in block

    def test_handler_dispatch(self):
        src = _read_server_source()
        assert 'name == "principles_list"' in src

    def test_handler_function_exists(self):
        src = _read_server_source()
        assert "async def _principles_list(" in src

    def test_handler_uses_correct_cli_command(self):
        src = _read_server_source()
        assert '"principles", "list"' in src

    def test_handler_passes_json_flag(self):
        src = _read_server_source()
        idx = src.index("async def _principles_list(")
        handler_block = src[idx:idx + 500]
        assert '"--json"' in handler_block


class TestPrinciplesSuggestTool:
    """Test principles_suggest tool definition and handler."""

    def test_tool_definition_exists(self):
        src = _read_server_source()
        assert 'name="principles_suggest"' in src

    def test_tool_description(self):
        src = _read_server_source()
        assert "Suggest experiments based on principles" in src

    def test_has_experiment_type_param(self):
        src = _read_server_source()
        idx = src.index('name="principles_suggest"')
        block = src[idx:idx + 800]
        assert '"experiment_type"' in block

    def test_handler_dispatch(self):
        src = _read_server_source()
        assert 'name == "principles_suggest"' in src

    def test_handler_function_exists(self):
        src = _read_server_source()
        assert "async def _principles_suggest(" in src

    def test_handler_uses_correct_cli_command(self):
        src = _read_server_source()
        assert '"principles", "suggest"' in src

    def test_handler_passes_json_flag(self):
        src = _read_server_source()
        idx = src.index("async def _principles_suggest(")
        handler_block = src[idx:idx + 500]
        assert '"--json"' in handler_block
