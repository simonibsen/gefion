"""Tests for experiment CLI commands.

Validates that CLI commands exist and have correct signatures.
"""
import pytest


class TestExperimentCLICommands:
    """Verify experiment CLI commands are registered."""

    def test_discover_command_registered(self):
        """experiment discover command should exist."""
        from gefion.cli import experiment_app
        command_names = [cmd.name for cmd in experiment_app.registered_commands]
        assert "discover" in command_names

    def test_cycle_start_command_registered(self):
        """experiment cycle-start command should exist."""
        from gefion.cli import experiment_app
        command_names = [cmd.name for cmd in experiment_app.registered_commands]
        assert "cycle-start" in command_names

    def test_cycle_status_command_registered(self):
        """experiment cycle-status command should exist."""
        from gefion.cli import experiment_app
        command_names = [cmd.name for cmd in experiment_app.registered_commands]
        assert "cycle-status" in command_names

    def test_cycle_list_command_registered(self):
        """experiment cycle-list command should exist."""
        from gefion.cli import experiment_app
        command_names = [cmd.name for cmd in experiment_app.registered_commands]
        assert "cycle-list" in command_names

    def test_propose_accepts_principle_option(self):
        """experiment propose should accept --principle option."""
        from gefion.cli import experiment_app
        # Find the propose command
        propose = [cmd for cmd in experiment_app.registered_commands if cmd.name == "propose"]
        assert len(propose) == 1
        # Check the callback has principle parameter
        import inspect
        sig = inspect.signature(propose[0].callback)
        assert "principle" in sig.parameters

    def test_propose_accepts_null_hypothesis_option(self):
        """experiment propose should accept --null-hypothesis option."""
        from gefion.cli import experiment_app
        propose = [cmd for cmd in experiment_app.registered_commands if cmd.name == "propose"]
        import inspect
        sig = inspect.signature(propose[0].callback)
        assert "hypothesis" in sig.parameters

    def test_propose_accepts_cycle_option(self):
        """experiment propose should accept --cycle option."""
        from gefion.cli import experiment_app
        propose = [cmd for cmd in experiment_app.registered_commands if cmd.name == "propose"]
        import inspect
        sig = inspect.signature(propose[0].callback)
        assert "cycle" in sig.parameters


class TestPrinciplesCLICommands:
    """Verify principles CLI commands are registered."""

    def test_principles_app_exists(self):
        """principles command group should exist."""
        from gefion.cli import principles_app
        assert principles_app is not None

    def test_list_command_registered(self):
        """principles list command should exist."""
        from gefion.cli import principles_app
        command_names = [cmd.name for cmd in principles_app.registered_commands]
        assert "list" in command_names

    def test_show_command_registered(self):
        """principles show command should exist."""
        from gefion.cli import principles_app
        command_names = [cmd.name for cmd in principles_app.registered_commands]
        assert "show" in command_names

    def test_suggest_command_registered(self):
        """principles suggest command should exist."""
        from gefion.cli import principles_app
        command_names = [cmd.name for cmd in principles_app.registered_commands]
        assert "suggest" in command_names


class TestDataRegistry:
    """Verify data registry loads correctly."""

    def test_registry_loads(self):
        """Registry YAML should load as a list of dicts."""
        from gefion.experiments.discovery import load_registry
        registry = load_registry()
        assert isinstance(registry, list)
        assert len(registry) > 0

    def test_registry_has_stock_ohlcv(self):
        """Registry should include stock_ohlcv."""
        from gefion.experiments.discovery import load_registry
        registry = load_registry()
        tables = [r["table"] for r in registry]
        assert "stock_ohlcv" in tables

    def test_registry_entries_have_required_fields(self):
        """Each registry entry should have id, table, description, columns."""
        from gefion.experiments.discovery import load_registry
        registry = load_registry()
        for entry in registry:
            assert "id" in entry, f"Missing 'id' in registry entry"
            assert "table" in entry, f"Missing 'table' in {entry.get('id', '?')}"
            assert "description" in entry, f"Missing 'description' in {entry.get('id', '?')}"
            assert "columns" in entry, f"Missing 'columns' in {entry.get('id', '?')}"
