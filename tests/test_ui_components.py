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
            "features.py",
            "ml.py",
            "backtest.py",
            "experiments.py",
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

    def test_dashboard_references_ai_prompts_not_assistant(self, ui_dir):
        """Dashboard should reference AI Prompts, not AI Assistant."""
        content = (ui_dir / "views" / "dashboard.py").read_text()
        assert "AI Prompts" in content
        assert "AI Assistant" not in content

    def test_dashboard_has_cached_market_data(self, ui_dir):
        """Dashboard should cache market overview data."""
        content = (ui_dir / "views" / "dashboard.py").read_text()
        assert "@st.cache_data" in content
        assert "def get_market_movers(" in content
        assert "ttl=" in content

    def test_dashboard_has_cached_insights_data(self, ui_dir):
        """Dashboard should cache g2 insights data."""
        content = (ui_dir / "views" / "dashboard.py").read_text()
        assert "def get_g2_insights(" in content

    def test_dashboard_insights_handles_missing_ml_tables(self, ui_dir):
        """Dashboard insights should handle missing ML tables gracefully.

        quantile_predictions and model_performance may not exist yet.
        """
        content = (ui_dir / "views" / "dashboard.py").read_text()
        # Should have try/except around ML table queries
        assert "# Predictions - table may not exist yet" in content
        assert "# Model performance - table may not exist yet" in content

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

    def test_ml_dataset_list_handles_missing_models_table(self, ui_dir):
        """Dataset listing should handle missing ml_models table gracefully."""
        content = (ui_dir / "views" / "ml.py").read_text()
        # Model counts should be fetched separately so datasets show even without ml_models
        assert "# Get model counts separately" in content
        assert "ml_models may not exist" in content

    def test_ml_train_has_device_detection(self, ui_dir):
        """ML train section should detect and display GPU/CPU device."""
        content = (ui_dir / "views" / "ml.py").read_text()
        # Should have device detection
        assert "_detect_device" in content
        # Should show device status
        assert "GPU Detected" in content
        assert "No GPU Detected" in content
        # Should pass device to training command
        assert '"--device"' in content

    def test_ml_train_shows_algorithm_gpu_support(self, ui_dir):
        """ML train should show which algorithms support GPU."""
        content = (ui_dir / "views" / "ml.py").read_text()
        assert "GPU-accelerated" in content
        assert "CPU-only" in content

    def test_ml_dataset_has_feature_selection(self, ui_dir):
        """ML dataset section should have feature include/exclude options."""
        content = (ui_dir / "views" / "ml.py").read_text()
        assert "--features" in content or "feature_include" in content
        assert "--exclude-features" in content or "feature_exclude" in content

    def test_ml_has_dataset_delete(self, ui_dir):
        """ML view should have dataset delete functionality."""
        content = (ui_dir / "views" / "ml.py").read_text()
        assert "delete" in content.lower() or "Delete" in content

    def test_ml_dataset_build_exports_files(self, ui_dir):
        """ML dataset build should include --export flag to create feature files."""
        content = (ui_dir / "views" / "ml.py").read_text()
        # The dataset build command must include --export for training to work
        assert '"--export"' in content, "Dataset build must include --export flag"

    def test_ml_dataset_build_warns_on_overwrite(self, ui_dir):
        """ML dataset build should warn when overwriting existing dataset."""
        content = (ui_dir / "views" / "ml.py").read_text()
        # Should check if dataset exists and show warning
        assert "already exists" in content.lower(), "Should warn about overwriting"

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

    def test_experiments_has_render_function(self, ui_dir):
        """Experiments view should have render_experiments function."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "def render_experiments(" in content


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

    def test_has_cached_status_data(self, status_module_path):
        """Should have cached function for status data."""
        content = status_module_path.read_text()
        assert "@st.cache_data" in content
        assert "def get_system_stats(" in content

    def test_has_smart_cache_invalidation(self, status_module_path):
        """Should invalidate cache when data date changes."""
        content = status_module_path.read_text()
        assert "def get_latest_data_date(" in content
        assert "get_system_stats.clear()" in content

    def test_ml_table_queries_handle_missing_tables(self, status_module_path):
        """ML table queries should handle missing tables gracefully.

        ml_models and quantile_predictions may not exist yet, so queries
        should fail gracefully and default to 0 instead of crashing.
        """
        content = status_module_path.read_text()
        # Should have individual try/except blocks for ML tables
        assert "# ML tables may not exist yet" in content
        assert content.count("SELECT COUNT(*) FROM ml_models") == 1
        assert content.count("SELECT COUNT(*) FROM quantile_predictions") == 1
        # Both queries should be in their own try/except blocks
        assert "model_count = 0" in content
        assert "prediction_count = 0" in content


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
        # Should skip short lines and non-dict JSON with robust handling
        assert 'len(line) < 3' in content
        # Uses try/except JSONDecodeError and isinstance check instead of startswith
        assert 'JSONDecodeError' in content
        assert 'isinstance(data, dict)' in content


class TestBackgroundProcessPersistence:
    """Test that long-running processes persist across page navigation."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui" / "views"

    def test_data_view_has_process_state_class(self, views_dir):
        """Data view should have ProcessState dataclass for tracking."""
        content = (views_dir / "data.py").read_text()
        assert '@dataclass' in content
        assert 'class ProcessState:' in content
        assert 'is_running: bool' in content
        assert 'completed: bool' in content

    def test_data_view_has_start_background_process(self, views_dir):
        """Data view should have background process launcher."""
        content = (views_dir / "data.py").read_text()
        assert 'def start_background_process(' in content
        assert 'threading.Thread(' in content
        assert 'daemon=True' in content

    def test_data_view_has_stop_process(self, views_dir):
        """Data view should have process stop function."""
        content = (views_dir / "data.py").read_text()
        assert 'def stop_process(' in content
        assert '.terminate()' in content

    def test_data_view_update_uses_background_process(self, views_dir):
        """Data update should use background process, not synchronous."""
        content = (views_dir / "data.py").read_text()
        # Should call start_background_process in render_update_section
        assert 'start_background_process("data_update"' in content

    def test_data_view_has_auto_refresh(self, views_dir):
        """Data view should auto-refresh while process runs."""
        content = (views_dir / "data.py").read_text()
        # Should have refresh mechanism
        assert 'st.rerun()' in content

    def test_process_state_has_performance_metrics(self, views_dir):
        """ProcessState should track performance metrics."""
        content = (views_dir / "data.py").read_text()
        assert 'rate_per_sec' in content
        assert 'eta_seconds' in content
        assert 'successes' in content
        assert 'last_ok_inserted' in content

    def test_process_state_has_output_lines(self, views_dir):
        """ProcessState should store CLI output lines."""
        content = (views_dir / "data.py").read_text()
        assert 'output_lines' in content
        # Should store output in background thread
        assert 'state.output_lines.append' in content

    def test_render_process_status_shows_cli_output(self, views_dir):
        """Process status display should show CLI output log."""
        content = (views_dir / "data.py").read_text()
        assert 'CLI Output' in content
        assert 'st.expander' in content

    def test_render_process_status_uses_getattr_for_backwards_compat(self, views_dir):
        """Process status should use getattr for old session state objects."""
        content = (views_dir / "data.py").read_text()
        # Should handle old session state objects that don't have new fields
        assert "getattr(state, 'rate_per_sec'" in content
        assert "getattr(state, 'output_lines'" in content


class TestFeaturesView:
    """Test the Features management view."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "g2" / "ui"

    def test_features_view_exists(self, ui_dir):
        """Features view file should exist."""
        features_file = ui_dir / "views" / "features.py"
        assert features_file.exists()

    def test_features_has_render_function(self, ui_dir):
        """Features view should have render_features function."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "def render_features(" in content

    def test_features_has_three_tabs(self, ui_dir):
        """Features view should have Definitions, Functions, and Coverage tabs."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "Definitions" in content
        assert "Functions" in content
        assert "Coverage" in content
        assert "st.tabs(" in content

    def test_features_has_definitions_section(self, ui_dir):
        """Features view should have definitions management."""
        content = (ui_dir / "views" / "features.py").read_text()
        # Should have list, add, delete capabilities
        assert "render_definitions_tab" in content
        assert "feature_definitions" in content.lower()

    def test_features_has_functions_section(self, ui_dir):
        """Features view should have functions display."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "render_functions_tab" in content
        assert "feature_functions" in content.lower()

    def test_features_has_coverage_section(self, ui_dir):
        """Features view should have coverage/data section."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "render_coverage_tab" in content

    def test_features_in_navigation(self, ui_dir):
        """Features should be in the app navigation."""
        content = (ui_dir / "app.py").read_text()
        assert "Features" in content
        assert "render_features" in content

    def test_features_shows_cli_commands(self, ui_dir):
        """Features view should show equivalent CLI commands."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert 'st.code(' in content
        assert 'language="bash"' in content

    def test_features_has_edit_definition(self, ui_dir):
        """Features view should have edit definition capability."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "render_edit_definition" in content
        # Should write to JSON file
        assert "feature-definitions" in content

    def test_features_has_edit_function(self, ui_dir):
        """Features view should have edit function capability."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "render_edit_function" in content
        # Should write to JSON file
        assert "feature-functions" in content

    def test_features_has_save_to_json(self, ui_dir):
        """Features view should save edits to JSON files."""
        content = (ui_dir / "views" / "features.py").read_text()
        # Should have JSON file write functionality
        assert "save_definition_to_json" in content or "export" in content.lower()
        assert "json.dumps" in content
