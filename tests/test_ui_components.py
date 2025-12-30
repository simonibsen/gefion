"""Tests for UI components.

These tests verify the UI structure and CLI command without requiring
Streamlit runtime or database connections.
"""

import pytest
from pathlib import Path


class TestUIStructure:
    """Test that all UI files exist with correct structure."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui"

    def test_ui_package_exists(self, ui_dir):
        """UI package should exist."""
        assert ui_dir.exists()
        assert (ui_dir / "__init__.py").exists()

    def test_ui_app_exists(self, ui_dir):
        """Main app.py should exist."""
        app_file = ui_dir / "app.py"
        assert app_file.exists()

        content = app_file.read_text()
        assert "import streamlit as st" in content
        assert "st.set_page_config" in content

    def test_ui_components_exist(self, ui_dir):
        """Component modules should exist."""
        components_dir = ui_dir / "components"
        assert components_dir.exists()
        assert (components_dir / "__init__.py").exists()
        assert (components_dir / "database.py").exists()
        assert (components_dir / "status.py").exists()

    def test_ui_views_exist(self, ui_dir):
        """All view modules should exist."""
        views_dir = ui_dir / "views"
        expected_views = [
            "__init__.py",
            "dashboard.py",
            "charts.py",
            "assistant.py",
            "data.py",
            "ml.py",
            "backtest.py",
            "documentation.py",
            "settings.py",
        ]

        assert views_dir.exists()
        for view in expected_views:
            assert (views_dir / view).exists(), f"View {view} not found"

    def test_dashboard_has_render_function(self, ui_dir):
        """Dashboard view should have render_dashboard function."""
        content = (ui_dir / "views" / "dashboard.py").read_text()
        assert "def render_dashboard(" in content

    def test_charts_has_render_function(self, ui_dir):
        """Charts view should have render_charts function."""
        content = (ui_dir / "views" / "charts.py").read_text()
        assert "def render_charts(" in content

    def test_assistant_has_render_function(self, ui_dir):
        """Assistant view should have render_assistant function."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "def render_assistant(" in content

    def test_data_has_render_function(self, ui_dir):
        """Data view should have render_data function."""
        content = (ui_dir / "views" / "data.py").read_text()
        assert "def render_data(" in content

    def test_ml_has_render_function(self, ui_dir):
        """ML view should have render_ml function."""
        content = (ui_dir / "views" / "ml.py").read_text()
        assert "def render_ml(" in content

    def test_backtest_has_render_function(self, ui_dir):
        """Backtest view should have render_backtest function."""
        content = (ui_dir / "views" / "backtest.py").read_text()
        assert "def render_backtest(" in content

    def test_settings_has_render_function(self, ui_dir):
        """Settings view should have render_settings function."""
        content = (ui_dir / "views" / "settings.py").read_text()
        assert "def render_settings(" in content

    def test_documentation_has_render_function(self, ui_dir):
        """Documentation view should have render_docs function."""
        content = (ui_dir / "views" / "documentation.py").read_text()
        assert "def render_docs(" in content


class TestUILaunchCommand:
    """Test CLI UI launch command."""

    def test_ui_command_exists(self):
        """The ui command should be registered with correct help."""
        from g2.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["ui", "--help"])

        assert result.exit_code == 0
        assert "Launch the Streamlit web UI" in result.output
        assert "--port" in result.output
        assert "--host" in result.output
        assert "--no-browser" in result.output

    def test_ui_command_has_examples(self):
        """The ui command help should include examples."""
        from g2.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["ui", "--help"])

        assert "g2 ui" in result.output


class TestDatabaseHelperStructure:
    """Test database helper module structure without imports."""

    @pytest.fixture
    def db_module_path(self):
        """Get database module path."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "components" / "database.py"

    def test_has_get_db_pool(self, db_module_path):
        """Should have get_db_pool function."""
        content = db_module_path.read_text()
        assert "def get_db_pool(" in content
        assert "@st.cache_resource" in content

    def test_has_get_connection(self, db_module_path):
        """Should have get_connection context manager."""
        content = db_module_path.read_text()
        assert "def get_connection(" in content
        assert "@contextmanager" in content

    def test_has_get_symbols(self, db_module_path):
        """Should have get_symbols function."""
        content = db_module_path.read_text()
        assert "def get_symbols(" in content

    def test_has_get_sectors(self, db_module_path):
        """Should have get_sectors function."""
        content = db_module_path.read_text()
        assert "def get_sectors(" in content

    def test_has_get_models(self, db_module_path):
        """Should have get_models function."""
        content = db_module_path.read_text()
        assert "def get_models(" in content

    def test_has_get_feature_definitions(self, db_module_path):
        """Should have get_feature_definitions function."""
        content = db_module_path.read_text()
        assert "def get_feature_definitions(" in content


class TestStatusComponentStructure:
    """Test status component module structure."""

    @pytest.fixture
    def status_module_path(self):
        """Get status module path."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "components" / "status.py"

    def test_has_render_quick_status(self, status_module_path):
        """Should have render_quick_status function."""
        content = status_module_path.read_text()
        assert "def render_quick_status(" in content

    def test_has_render_system_status(self, status_module_path):
        """Should have render_system_status function."""
        content = status_module_path.read_text()
        assert "def render_system_status(" in content


class TestCLICommandDisplay:
    """Test that UI views display equivalent CLI commands."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "views"

    def test_data_view_shows_cli_command(self, views_dir):
        """Data view should display equivalent CLI commands."""
        content = (views_dir / "data.py").read_text()
        # Should show CLI command for data update
        assert 'st.code(' in content
        assert 'language="bash"' in content
        assert 'g2 data-update' in content or 'g2", "data-update' in content

    def test_ml_view_shows_cli_commands(self, views_dir):
        """ML view should display equivalent CLI commands for all operations."""
        content = (views_dir / "ml.py").read_text()
        # Should show CLI commands
        assert 'st.code(' in content
        assert 'language="bash"' in content
        # Should have commands for major operations
        assert 'ml dataset-build' in content or 'ml", "dataset-build' in content
        assert 'ml train' in content or 'ml", "train' in content
        assert 'ml predict' in content or 'ml", "predict' in content
        assert 'ml eval' in content or 'ml", "eval' in content

    def test_backtest_view_shows_cli_commands(self, views_dir):
        """Backtest view should display equivalent CLI commands."""
        content = (views_dir / "backtest.py").read_text()
        # Should show CLI command
        assert 'st.code(' in content
        assert 'language="bash"' in content
        assert 'backtest run' in content or 'backtest", "run' in content
        assert 'backtest compare' in content or 'backtest", "compare' in content


class TestStreamingProgress:
    """Test that views use streaming progress where appropriate."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "views"

    def test_data_view_uses_streaming(self, views_dir):
        """Data view should use subprocess.Popen for streaming output."""
        content = (views_dir / "data.py").read_text()
        assert 'subprocess.Popen(' in content
        assert 'st.status(' in content
        assert 'process.stdout' in content

    def test_ml_view_uses_streaming(self, views_dir):
        """ML view should use subprocess.Popen for streaming output."""
        content = (views_dir / "ml.py").read_text()
        assert 'subprocess.Popen(' in content
        assert 'st.status(' in content
        assert 'process.stdout' in content

    def test_backtest_view_uses_status(self, views_dir):
        """Backtest view should use st.status for progress."""
        content = (views_dir / "backtest.py").read_text()
        assert 'st.status(' in content


class TestJSONParsingRobustness:
    """Test that JSON parsing handles non-dict responses safely."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "views"

    def test_data_view_checks_isinstance_dict(self, views_dir):
        """Data view should check if parsed JSON is a dict before using .get()."""
        content = (views_dir / "data.py").read_text()
        # Should have isinstance check to handle JSON strings
        assert 'isinstance(data, dict)' in content

    def test_ml_view_checks_isinstance_dict(self, views_dir):
        """ML view should check if parsed JSON is a dict before using .get()."""
        content = (views_dir / "ml.py").read_text()
        # Should have isinstance check to handle JSON strings
        assert 'isinstance(data, dict)' in content

    def test_data_view_filters_json_fragments(self, views_dir):
        """Data view should filter out JSON fragments like } from display."""
        content = (views_dir / "data.py").read_text()
        # Should skip short lines and JSON bracket lines
        assert 'len(line) < 3' in content
        assert "startswith(('{', '}'" in content
