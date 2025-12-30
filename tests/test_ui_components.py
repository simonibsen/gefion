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

    def test_ui_pages_exist(self, ui_dir):
        """All page modules should exist."""
        pages_dir = ui_dir / "pages"
        expected_pages = [
            "__init__.py",
            "dashboard.py",
            "charts.py",
            "assistant.py",
            "data.py",
            "ml.py",
            "backtest.py",
            "settings.py",
        ]

        assert pages_dir.exists()
        for page in expected_pages:
            assert (pages_dir / page).exists(), f"Page {page} not found"

    def test_dashboard_has_render_function(self, ui_dir):
        """Dashboard page should have render_dashboard function."""
        content = (ui_dir / "pages" / "dashboard.py").read_text()
        assert "def render_dashboard(" in content

    def test_charts_has_render_function(self, ui_dir):
        """Charts page should have render_charts function."""
        content = (ui_dir / "pages" / "charts.py").read_text()
        assert "def render_charts(" in content

    def test_assistant_has_render_function(self, ui_dir):
        """Assistant page should have render_assistant function."""
        content = (ui_dir / "pages" / "assistant.py").read_text()
        assert "def render_assistant(" in content
        assert "Claude Code" in content  # Claude Code integration

    def test_data_has_render_function(self, ui_dir):
        """Data page should have render_data function."""
        content = (ui_dir / "pages" / "data.py").read_text()
        assert "def render_data(" in content

    def test_ml_has_render_function(self, ui_dir):
        """ML page should have render_ml function."""
        content = (ui_dir / "pages" / "ml.py").read_text()
        assert "def render_ml(" in content

    def test_backtest_has_render_function(self, ui_dir):
        """Backtest page should have render_backtest function."""
        content = (ui_dir / "pages" / "backtest.py").read_text()
        assert "def render_backtest(" in content

    def test_settings_has_render_function(self, ui_dir):
        """Settings page should have render_settings function."""
        content = (ui_dir / "pages" / "settings.py").read_text()
        assert "def render_settings(" in content


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
