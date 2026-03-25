"""Tests for UI components.

These tests verify the UI structure and CLI command without requiring
Streamlit runtime or database connections.
"""

import sys
import pytest
from pathlib import Path


class TestUIStructure:
    """Test that all UI files exist with correct structure."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui"

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

    def test_data_logs_background_process_errors(self, ui_dir):
        """Data view should log errors via log_ui_error when background process fails."""
        content = (ui_dir / "views" / "data.py").read_text()
        assert "log_ui_error" in content, "data.py should call log_ui_error on failure"

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

    def test_ui_errors_module_exists(self, ui_dir):
        """UI errors module should exist with error logging functions."""
        errors_file = ui_dir / "errors.py"
        assert errors_file.exists(), "ui/errors.py not found"

        content = errors_file.read_text()
        assert "def log_ui_error(" in content
        assert "def read_session_errors(" in content
        assert "def clear_errors(" in content

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

    def test_documentation_has_search_function(self, ui_dir):
        """Documentation view should have search_docs function."""
        content = (ui_dir / "views" / "documentation.py").read_text()
        assert "def search_docs(" in content

    def test_documentation_search_finds_results(self):
        """Documentation search should find results for common terms."""
        from gefion.ui.views.documentation import search_docs

        # Search for a term that should exist in docs
        results = search_docs("quantile")
        assert len(results) > 0

        # Each result should have (doc_name, section, line, context, filename, score)
        doc_name, section, line, context, filename, score = results[0]
        assert isinstance(doc_name, str)
        assert isinstance(section, str)
        assert isinstance(filename, str)
        assert isinstance(score, int)
        assert score > 0  # Should have positive relevance score
        assert "quantile" in line.lower() or "quantile" in context.lower()

    def test_documentation_search_empty_for_nonsense(self):
        """Documentation search should return empty for nonsense queries."""
        from gefion.ui.views.documentation import search_docs

        results = search_docs("xyzzy123nonsense")
        assert len(results) == 0

    def test_documentation_search_requires_min_length(self):
        """Documentation search should require minimum query length."""
        from gefion.ui.views.documentation import search_docs

        # Single character should return no results
        results = search_docs("a")
        assert len(results) == 0

    def test_experiments_has_render_function(self, ui_dir):
        """Experiments view should have render_experiments function."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "def render_experiments(" in content


class TestUILaunchCommand:
    """Test CLI UI launch command."""

    def test_ui_command_exists(self):
        """The ui command should be registered with correct help."""
        from gefion.cli import app
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
        from gefion.cli import app
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["ui", "--help"])

        assert "gefion ui" in result.output

    def test_launch_ui_prints_error_summary(self, tmp_path):
        """launch_ui should print error summary when errors were logged during session."""
        from gefion.cli import app
        from typer.testing import CliRunner
        from unittest.mock import patch

        error_file = tmp_path / "ui_errors.jsonl"

        def mock_subprocess_run(cmd, **kwargs):
            """Simulate UI run that logs errors."""
            import json
            from datetime import datetime, timezone
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "background_process",
                "message": "Process exited with code 1",
                "context": {"key": "data_update", "returncode": 1},
            }
            error_file.write_text(json.dumps(entry) + "\n")

        runner = CliRunner()
        with patch("gefion.ui.errors._error_file", return_value=error_file):
            with patch("subprocess.run", side_effect=mock_subprocess_run):
                result = runner.invoke(app, ["ui"])

        assert "UI Session Errors" in result.output
        assert "(background_process)" in result.output
        assert "Process exited with code 1" in result.output

    def test_launch_ui_no_summary_when_no_errors(self, tmp_path):
        """launch_ui should not print summary when no errors occurred."""
        from gefion.cli import app
        from typer.testing import CliRunner
        from unittest.mock import patch

        error_file = tmp_path / "ui_errors.jsonl"

        runner = CliRunner()
        with patch("gefion.ui.errors._error_file", return_value=error_file):
            with patch("subprocess.run"):
                result = runner.invoke(app, ["ui"])

        assert "UI Session Errors" not in result.output


class TestDatabaseHelperStructure:
    """Test database helper module structure without imports."""

    @pytest.fixture
    def db_module_path(self):
        """Get database module path."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "components" / "database.py"

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

    def test_get_models_uses_algorithm_column(self, db_module_path):
        """get_models should query 'algorithm' column, not 'model_type'."""
        content = db_module_path.read_text()
        # Should use 'algorithm' column (actual column name)
        assert "algorithm" in content
        # Should NOT use 'model_type' (doesn't exist in schema)
        assert "model_type" not in content

    def test_has_get_feature_definitions(self, db_module_path):
        """Should have get_feature_definitions function."""
        content = db_module_path.read_text()
        assert "def get_feature_definitions(" in content

    def test_connection_handles_oid_errors(self, db_module_path):
        """Connection should auto-clear pool on OID/connection errors."""
        content = db_module_path.read_text()
        # Should detect OID errors and clear pool
        assert '"oid"' in content.lower() or "'oid'" in content.lower()
        assert "get_db_pool.clear()" in content
        # Should catch exceptions and handle connection errors
        assert "except Exception" in content

    def test_connection_handles_bad_connections(self, db_module_path):
        """Connection should handle BAD connection state gracefully."""
        content = db_module_path.read_text()
        # Should detect bad connection state
        assert '"bad"' in content.lower() or "'bad'" in content.lower()
        # putconn should be wrapped in try/except
        assert "pool.putconn(conn)" in content


class TestBacktestCompareMLSupport:
    """Tests for ML strategy support in backtest compare."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui"

    def test_compare_section_has_ml_model_selection(self, ui_dir):
        """Compare section should have ML model selection when ML strategies selected."""
        content = (ui_dir / "views" / "backtest.py").read_text()
        # Should detect ML strategies
        assert "ml_strategies" in content
        # Should have model selection for compare
        assert "cmp_ml_select" in content or "cmp_model_name" in content

    def test_compare_passes_ml_params_to_cli(self, ui_dir):
        """Compare should pass ML parameters to CLI command."""
        content = (ui_dir / "views" / "backtest.py").read_text()
        # Should add model params to command
        assert "--model-name" in content
        assert "--model-version" in content


class TestStatusComponentStructure:
    """Test status component module structure."""

    @pytest.fixture
    def status_module_path(self):
        """Get status module path."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "components" / "status.py"

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
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_data_view_shows_cli_command(self, views_dir):
        """Data view should display equivalent CLI commands."""
        content = (views_dir / "data.py").read_text()
        # Should show CLI command for data update
        assert 'st.code(' in content
        assert 'language="bash"' in content
        assert 'gefion data-update' in content or 'gefion", "data-update' in content

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
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

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
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

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
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

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
        return Path(__file__).parent.parent / "src" / "gefion" / "ui"

    def test_features_view_exists(self, ui_dir):
        """Features view file should exist."""
        features_file = ui_dir / "views" / "features.py"
        assert features_file.exists()

    def test_features_has_render_function(self, ui_dir):
        """Features view should have render_features function."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "def render_features(" in content

    def test_features_has_four_tabs(self, ui_dir):
        """Features view should have Definitions, Functions, Coverage, and Compute tabs."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "Definitions" in content
        assert "Functions" in content
        assert "Coverage" in content
        assert "Compute" in content
        assert "st.tabs(" in content
        # Should unpack 4 tabs
        assert "tab1, tab2, tab3, tab4" in content

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

    def test_features_has_compute_tab_render_function(self, ui_dir):
        """Features view should have render_compute_tab function."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "def render_compute_tab(" in content

    def test_features_compute_tab_has_symbol_input(self, ui_dir):
        """Compute tab should have a symbol text input."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "compute_symbols" in content or "feat_symbols" in content

    def test_features_compute_tab_has_all_features_option(self, ui_dir):
        """Compute tab should have all-features checkbox."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "all_features" in content or "All Features" in content
        assert "all-features" in content

    def test_features_compute_tab_has_mode_selection(self, ui_dir):
        """Compute tab should have incremental/full mode selection."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "Incremental" in content
        assert "Full" in content

    def test_features_compute_tab_shows_cli_command(self, ui_dir):
        """Compute tab should show equivalent feat-compute CLI command."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "feat-compute" in content

    def test_features_compute_tab_uses_background_process(self, ui_dir):
        """Compute tab should import and use background process from data.py."""
        content = (ui_dir / "views" / "features.py").read_text()
        assert "start_background_process" in content
        assert "render_process_status" in content
        assert "get_process_state" in content

    def test_features_compute_tab_disables_while_running(self, ui_dir):
        """Compute tab should hide form controls while process is running."""
        content = (ui_dir / "views" / "features.py").read_text()
        # Should check state and return early when running/completed
        assert "state.is_running" in content
        assert "state.completed" in content


class TestBacktestStrategies:
    """Test backtest view strategy support."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_backtest_loads_strategies_from_database(self, views_dir):
        """Backtest should load strategies from database, not hardcoded list."""
        content = (views_dir / "backtest.py").read_text()
        assert "def get_strategies(" in content
        assert "strategy_registry" in content

    def test_backtest_has_momentum_parameters(self, views_dir):
        """Backtest should have momentum strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "momentum"' in content
        assert "Lookback Days" in content
        assert "Top N" in content
        assert "Rebalance" in content

    def test_backtest_has_mean_reversion_parameters(self, views_dir):
        """Backtest should have mean_reversion strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "mean_reversion"' in content
        assert "RSI Oversold" in content
        assert "RSI Overbought" in content

    def test_backtest_has_ma_crossover_parameters(self, views_dir):
        """Backtest should have ma_crossover strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "ma_crossover"' in content
        assert "Fast MA Period" in content or "Fast Period" in content
        assert "Slow MA Period" in content or "Slow Period" in content

    def test_backtest_has_breakout_parameters(self, views_dir):
        """Backtest should have breakout strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "breakout"' in content
        assert "Volume Threshold" in content

    def test_backtest_has_pairs_trading_parameters(self, views_dir):
        """Backtest should have pairs_trading strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "pairs_trading"' in content
        assert "Entry Z-Score" in content
        assert "Exit Z-Score" in content

    def test_backtest_has_rsi_divergence_parameters(self, views_dir):
        """Backtest should have rsi_divergence strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "rsi_divergence"' in content
        assert "RSI Period" in content
        assert "Divergence Lookback" in content

    def test_backtest_has_volatility_contraction_parameters(self, views_dir):
        """Backtest should have volatility_contraction strategy parameters."""
        content = (views_dir / "backtest.py").read_text()
        assert 'strategy == "volatility_contraction"' in content
        assert "Bollinger Period" in content
        assert "Squeeze Threshold" in content
        assert "Expansion Threshold" in content

    def test_backtest_cli_command_format(self, views_dir):
        """Backtest should format CLI command correctly without truncation bugs."""
        content = (views_dir / "backtest.py").read_text()
        # Should use cmd[3:] to skip [python, -m, gefion.cli] - not buggy string slicing
        assert "cli_args = cmd[3:]" in content
        # Should join with space and prefix with g2
        assert '"gefion {' in content and "' '.join(cli_args)" in content

    def test_backtest_validates_empty_symbols(self, views_dir):
        """Backtest should validate that symbols are selected."""
        content = (views_dir / "backtest.py").read_text()
        # Should warn if no symbols selected
        assert "Please select at least one symbol" in content
        # Should stop execution if trying to run without symbols
        assert "st.stop()" in content

    def test_backtest_mean_reversion_has_max_positions(self, views_dir):
        """Mean reversion strategy should have max_positions parameter."""
        content = (views_dir / "backtest.py").read_text()
        assert '"--max-positions"' in content
        assert "Max Positions" in content

    def test_backtest_compare_uses_database_strategies(self, views_dir):
        """Backtest compare section should load strategies from database."""
        content = (views_dir / "backtest.py").read_text()
        # Compare section should use get_strategies()
        assert content.count("get_strategies()") >= 2  # Run + Compare sections


class TestStrategyConfigsUI:
    """Test Strategy Configs section in Backtesting UI."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_backtest_has_strategy_configs_section(self, views_dir):
        """Backtest view should have Strategy Configs section."""
        content = (views_dir / "backtest.py").read_text()
        assert "Strategy Configs" in content
        assert "render_strategy_configs" in content or "_render_configs_section" in content

    def test_strategy_configs_lists_existing_configs(self, views_dir):
        """Strategy Configs section should list existing configs."""
        content = (views_dir / "backtest.py").read_text()
        # Should query strategy_configs table
        assert "strategy_configs" in content
        # Should display config name, strategy, and params
        assert "config" in content.lower()

    def test_strategy_configs_has_create_form(self, views_dir):
        """Strategy Configs section should have create config form."""
        content = (views_dir / "backtest.py").read_text()
        # Should have form inputs for creating config
        assert "Create" in content or "New Config" in content
        # Should have name input
        assert "config_name" in content or "new_config" in content

    def test_strategy_configs_has_unregister_option(self, views_dir):
        """Strategy Configs section should have unregister option."""
        content = (views_dir / "backtest.py").read_text()
        # Should have unregister functionality (not delete - strategies are code)
        assert "Unregister" in content
        # Should explain the difference
        assert "underlying strategy" in content.lower()

    def test_strategy_configs_shows_cli_command(self, views_dir):
        """Strategy Configs section should show equivalent CLI command."""
        content = (views_dir / "backtest.py").read_text()
        # Should show CLI command for creating config
        assert "strategy create-config" in content

    def test_strategy_configs_has_parameter_reference(self, views_dir):
        """Strategy Configs section should have parameter reference for each strategy."""
        content = (views_dir / "backtest.py").read_text()
        # Should have parameter reference function
        assert "get_strategy_params_reference" in content
        # Should include theory for strategies
        assert "theory" in content.lower()
        # Should have example JSON
        assert "Example JSON" in content

    def test_strategy_configs_explains_strategies_vs_configs(self, views_dir):
        """Strategy Configs section should explain the difference between strategies and configs."""
        content = (views_dir / "backtest.py").read_text()
        # Should explain strategies are code
        assert "Python classes" in content or "code" in content.lower()
        # Should explain configs are database records
        assert "database" in content.lower()


class TestExperimentsUIQuery:
    """Test that experiments UI queries database correctly."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui"

    def test_experiments_queries_search_method_from_config_jsonb(self, ui_dir):
        """Experiments list query should extract search_method from config JSONB.

        The search_method is stored in the config JSONB column, not as a
        separate column. The UI must use config->>'search_method' syntax.
        """
        content = (ui_dir / "views" / "experiments.py").read_text()

        # Should NOT have bare search_method in SELECT (that's the bug)
        # The fix is to use config->>'search_method'
        assert "config->>'search_method'" in content, (
            "experiments.py should query config->>'search_method' not bare search_method"
        )

    def test_experiments_list_section_has_render_function(self, ui_dir):
        """Experiments view should have render_list_section function."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "def render_list_section(" in content


class TestActionDashboard:
    """Test the Action Dashboard (formerly AI Prompts page)."""

    @pytest.fixture
    def ui_dir(self):
        """Get the UI source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui"

    def test_assistant_has_check_conditions_function(self, ui_dir):
        """Assistant should have check_conditions function for evaluating system state."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "def check_conditions(" in content

    def test_assistant_has_free_form_command_entry(self, ui_dir):
        """Assistant should have text input for natural language and CLI commands."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "st.text_area(" in content or "st.text_input(" in content
        assert "freeform" in content

    def test_assistant_has_mcp_tool_mapping(self, ui_dir):
        """Assistant should map MCP tool names to CLI commands."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "MCP_TOOL_MAP" in content
        # Should include key MCP tools
        assert "data_update" in content
        assert "ml_train" in content
        assert "system_status" in content

    def test_assistant_has_parse_input_function(self, ui_dir):
        """Assistant should parse both MCP tool names and CLI commands."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "def parse_command_input(" in content

    def test_assistant_has_ai_prompt_mode(self, ui_dir):
        """Assistant should support sending natural language to Claude via claude -p."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        # Should use claude CLI for AI prompts
        assert "claude" in content
        assert "--print" in content or '"-p"' in content
        # Should have operator context so LLM doesn't do dev operations
        assert "append-system-prompt" in content
        # Should restrict to MCP tools only
        assert "allowed-tools" in content or "allowedTools" in content

    def test_assistant_uses_background_process(self, ui_dir):
        """Assistant should import and use background process infrastructure."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "start_background_process" in content
        assert "render_process_status" in content

    def test_assistant_handles_parse_errors(self, ui_dir):
        """Assistant should show parse errors to user, not silently swallow them."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "st.error(" in content

    def test_assistant_logs_condition_check_failures(self, ui_dir):
        """Condition check failures should be logged, not silently swallowed."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "import logging" in content
        assert "logger" in content

    def test_assistant_shows_cli_commands(self, ui_dir):
        """Assistant should show equivalent CLI commands with st.code."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert 'st.code(' in content
        assert 'language="bash"' in content

    def test_assistant_has_action_cards(self, ui_dir):
        """Assistant should have render_action_card function for displaying actions."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "def render_action_card(" in content

    def test_assistant_has_cached_conditions(self, ui_dir):
        """Assistant should use @st.cache_data for condition queries."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "@st.cache_data" in content
        assert "def check_conditions(" in content

    def test_assistant_builds_action_list(self, ui_dir):
        """Assistant should build a list of actions and always show at least 4."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "def build_actions(" in content
        # Should have proactive suggestions, not just problem-detection
        assert "proactive" in content.lower() or "Proactive" in content

    def test_assistant_actions_have_reasoning(self, ui_dir):
        """Each action card should include reasoning for why it's recommended."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "reason" in content.lower()
        # render_action_card should accept a reason parameter
        assert "reason" in content

    def test_assistant_has_freeform_output_renderer(self, ui_dir):
        """Assistant should have render_freeform_output for plain-text display.

        The freeform section (Ask AI / Run Command) should NOT use
        render_process_status — that shows data-update metrics (progress,
        inserted, errors, ETA) which don't apply to AI prompts or general
        CLI output. Instead, render_freeform_output shows plain text.
        """
        content = (ui_dir / "views" / "assistant.py").read_text()
        # Must have the dedicated renderer
        assert "def render_freeform_output(" in content
        # Freeform section must call it (not render_process_status)
        # The function should use st.markdown for plain text output
        assert "st.markdown(" in content
        # Should store mode in session state for renderer to use
        assert "freeform_mode" in content

    def test_assistant_freeform_run_not_blocked_by_completed(self, ui_dir):
        """Run button must be available even after a previous command completes.

        If the freeform state is completed, the user should be able to type
        a new command and click Run without having to Clear first. The Run
        button must NOT be guarded by `elif` after checking completed state.
        """
        content = (ui_dir / "views" / "assistant.py").read_text()
        # The run button logic must check "not is_running" rather than
        # being in an elif that's unreachable when completed=True
        assert "not freeform_state.is_running" in content

    def test_assistant_freeform_strips_claudecode_env(self, ui_dir):
        """Freeform command must strip CLAUDECODE env var.

        When g2 ui is launched from Claude Code, the CLAUDECODE env var is set.
        claude -p refuses to run inside another Claude Code session. The env
        passed to start_background_process must remove this variable.
        """
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "CLAUDECODE" in content, "Must handle CLAUDECODE env var for nested sessions"

    def test_assistant_freeform_uses_form(self, ui_dir):
        """Freeform input and Run button must be wrapped in a st.form.

        Without a form, st.text_input doesn't send its value until the user
        presses Enter. Wrapping in a form lets the submit button commit
        the input value in one action.
        """
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "st.form(" in content, "Freeform section must use st.form"
        assert "st.form_submit_button(" in content, "Must use form_submit_button inside form"

    def test_assistant_freeform_has_auto_refresh(self, ui_dir):
        """Freeform output must auto-refresh while the process is running.

        Without st.rerun(), Streamlit won't update the display and the
        user sees 'Thinking...' forever with no output.
        """
        content = (ui_dir / "views" / "assistant.py").read_text()
        # render_freeform_output must trigger a rerun while running
        # Look for the auto-refresh pattern near the freeform output code
        assert "st.rerun()" in content
        # The rerun should be conditional on is_running inside render_freeform_output
        # Check that there's a sleep before rerun (to avoid tight loop)
        assert "time.sleep(" in content

    def test_mcp_tool_map_references_valid_cli_commands(self, ui_dir):
        """Every CLI command in MCP_TOOL_MAP must be a real gefion CLI command.

        system-status and health-check do not exist — the real commands
        are 'health' and 'db-health'.
        """
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "gefion.cli", "--help"],
            capture_output=True, text=True
        )
        help_text = result.stdout

        content = (ui_dir / "views" / "assistant.py").read_text()
        # Extract just the MCP_TOOL_MAP block
        import re
        map_match = re.search(r'MCP_TOOL_MAP\s*=\s*\{(.+?)\}', content, re.DOTALL)
        assert map_match, "MCP_TOOL_MAP not found"
        map_block = map_match.group(1)
        # Pattern: ("some-command", "description")
        entries = re.findall(r'\("([^"]+)",\s*"[^"]*"\)', map_block)
        assert entries, "No entries found in MCP_TOOL_MAP"
        for cmd in entries:
            # Multi-word commands like "ml train" — check the first part
            base = cmd.split()[0]
            assert base in help_text, (
                f"MCP_TOOL_MAP references '{cmd}' but '{base}' is not a gefion CLI command"
            )

    def test_proactive_actions_use_valid_cli_commands(self, ui_dir):
        """Proactive action cli_cmd values must reference real g2 commands."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "gefion.cli", "--help"],
            capture_output=True, text=True
        )
        help_text = result.stdout

        content = (ui_dir / "views" / "assistant.py").read_text()
        import re
        # Pattern: cli_cmd="gefion some-command"
        cmds = re.findall(r'cli_cmd="gefion\s+([^"]+)"', content)
        for cmd in cmds:
            base = cmd.split()[0]
            assert base in help_text, (
                f"Proactive action uses 'g2 {cmd}' but '{base}' is not a gefion CLI command"
            )

    def test_assistant_renders_conversation_history(self, ui_dir):
        """Assistant view must read and render conversation history."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "read_exchanges" in content, "Must call read_exchanges() to load history"
        assert "history" in content.lower(), "Must render conversation history"

    def test_assistant_appends_exchange_on_completion(self, ui_dir):
        """Assistant view must append exchange after command completes."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "append_exchange" in content, "Must call append_exchange() after command completes"

    def test_assistant_has_clear_history_button(self, ui_dir):
        """Assistant view must have a Clear History action."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "clear_history" in content, "Must call clear_history() for reset"

    def test_assistant_shows_error_indicator(self, ui_dir):
        """Assistant view must display session error count from error module."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "read_session_errors" in content, "Must read errors from gefion.ui.errors"

    def test_assistant_has_expandable_error_list(self, ui_dir):
        """Assistant view must have an expander or section for listing session errors."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        # Should have an expander or container that shows error details
        assert "error" in content.lower() and "expander" in content.lower(), (
            "Must have an expandable section for error details"
        )

    def test_ai_prompt_uses_stream_json(self, ui_dir):
        """AI prompts must use --output-format stream-json --verbose for transparency."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "stream-json" in content, "AI command must use --output-format stream-json"
        assert "--verbose" in content, "AI command must use --verbose for stream-json"

    def test_assistant_has_work_toggle(self, ui_dir):
        """Assistant view must have a way to show AI work (tool calls) during execution."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "work_events" in content.lower() or "tool_calls" in content.lower() or "work" in content.lower(), (
            "Must track and display AI work events"
        )

    def test_ai_prompt_supports_continue(self, ui_dir):
        """AI prompts must support --continue for multi-turn conversation context."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "ai_session_active" in content, "Must track AI session state"
        assert '"--continue"' in content, "Must add --continue flag for subsequent prompts"

    def test_parse_stream_json_event_exists(self, ui_dir):
        """A function to parse stream-json events must exist."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "parse_stream_event" in content, "Must have parse_stream_event function"

    def test_sidebar_ai_actions_position(self, ui_dir):
        """AI Actions must be the second item in the sidebar PAGES list."""
        content = (ui_dir / "app.py").read_text()
        # Find the PAGES list and extract page labels (first element of each tuple)
        import re
        # Match tuples like ("Label", ":material/icon:")
        tuples = re.findall(r'\("([^"]+)",\s*":[^"]+:"\)', content)
        if not tuples:
            # Fallback: old format with plain strings
            pages_match = re.search(r'PAGES\s*=\s*\[(.*?)\]', content, re.DOTALL)
            assert pages_match, "PAGES list not found in app.py"
            tuples = re.findall(r'"([^"]+)"', pages_match.group(1))
        assert len(tuples) >= 2, "PAGES must have at least 2 entries"
        assert "AI Actions" in tuples[1], (
            f"AI Actions must be second in PAGES, got '{tuples[1]}'"
        )

    def test_assistant_renamed_to_ai_actions(self, ui_dir):
        """The page routing must use 'AI Actions' not 'AI Prompts'."""
        content = (ui_dir / "app.py").read_text()
        assert "AI Actions" in content, "app.py must reference 'AI Actions'"

    def test_assistant_input_before_proactive_actions(self, ui_dir):
        """Chat input must appear before proactive action cards in assistant.py."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        # Look within render_assistant function body only
        render_start = content.find("def render_assistant")
        assert render_start > 0, "render_assistant must exist"
        body = content[render_start:]
        form_pos = body.find("freeform_form")
        # Find the call to check_conditions(), not the function definition
        conditions_pos = body.find("check_conditions()")
        assert form_pos > 0 and conditions_pos > 0, "Both form and conditions call must exist"
        assert form_pos < conditions_pos, (
            "Chat input (freeform_form) must appear before proactive actions (check_conditions)"
        )


class TestParseStreamEvent:
    """Test stream-json event parsing for AI transparency."""

    def test_parse_tool_use_event(self):
        """Tool use events should be parsed with tool name and input."""
        from gefion.ui.views.assistant import parse_stream_event
        import json
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "mcp__g2__health_check",
                    "input": {"verbose": True},
                }]
            }
        })
        result = parse_stream_event(event)
        assert result is not None
        assert result["type"] == "tool_use"
        assert result["tool"] == "mcp__g2__health_check"

    def test_parse_text_event(self):
        """Text events should return the text content."""
        from gefion.ui.views.assistant import parse_stream_event
        import json
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello world"}]
            }
        })
        result = parse_stream_event(event)
        assert result is not None
        assert result["type"] == "text"
        assert result["text"] == "Hello world"

    def test_parse_result_event(self):
        """Result events should include result text and metadata."""
        from gefion.ui.views.assistant import parse_stream_event
        import json
        event = json.dumps({
            "type": "result",
            "result": "Final answer",
            "duration_ms": 1500,
            "total_cost_usd": 0.05,
        })
        result = parse_stream_event(event)
        assert result is not None
        assert result["type"] == "result"
        assert result["result"] == "Final answer"

    def test_parse_invalid_json(self):
        """Invalid JSON should return None."""
        from gefion.ui.views.assistant import parse_stream_event
        assert parse_stream_event("not json") is None
        assert parse_stream_event("") is None


class TestConversationHistory:
    """Test conversation history persistence module."""

    @pytest.fixture
    def ui_dir(self):
        return Path("src/gefion/ui")

    def test_history_module_exists(self, ui_dir):
        """history.py must exist with required functions."""
        history_file = ui_dir / "history.py"
        assert history_file.exists(), "src/gefion/ui/history.py must exist"
        content = history_file.read_text()
        assert "def append_exchange(" in content
        assert "def read_exchanges(" in content
        assert "def clear_history(" in content
        assert "MAX_EXCHANGES" in content

    @pytest.fixture
    def tmp_history(self, tmp_path, monkeypatch):
        """Provide a temporary history file path and patch the module to use it."""
        history_file = tmp_path / "ai_history.jsonl"
        import gefion.ui.history as hist_mod
        monkeypatch.setattr(hist_mod, "HISTORY_FILE", history_file)
        return history_file

    def test_history_append_exchange(self, tmp_history):
        """append_exchange writes an Exchange record to JSONL."""
        import json
        from gefion.ui.history import append_exchange

        append_exchange(
            prompt="g2 health",
            mode="cli",
            response="All systems healthy",
            success=True,
            duration_sec=1.2,
        )

        lines = tmp_history.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["prompt"] == "g2 health"
        assert record["mode"] == "cli"
        assert record["response"] == "All systems healthy"
        assert record["success"] is True
        assert record["duration_sec"] == 1.2
        assert "timestamp" in record

    def test_history_read_exchanges(self, tmp_history):
        """read_exchanges returns list of Exchange dicts from JSONL."""
        from gefion.ui.history import append_exchange, read_exchanges

        append_exchange("prompt 1", "ai", "response 1", True, 0.5)
        append_exchange("prompt 2", "cli", "response 2", False, 1.0)

        exchanges = read_exchanges()
        assert len(exchanges) == 2
        assert exchanges[0]["prompt"] == "prompt 1"
        assert exchanges[1]["prompt"] == "prompt 2"

    def test_history_clear(self, tmp_history):
        """clear_history removes all history and returns empty list."""
        from gefion.ui.history import append_exchange, clear_history, read_exchanges

        append_exchange("prompt", "ai", "response", True, 0.5)
        assert len(read_exchanges()) == 1

        clear_history()
        assert len(read_exchanges()) == 0
        # File should not exist or be empty
        assert not tmp_history.exists() or tmp_history.read_text().strip() == ""

    def test_history_max_100_exchanges(self, tmp_history, monkeypatch):
        """Appending beyond MAX_EXCHANGES truncates oldest entries."""
        import gefion.ui.history as hist_mod
        monkeypatch.setattr(hist_mod, "MAX_EXCHANGES", 5)  # Use small cap for test speed

        from gefion.ui.history import append_exchange, read_exchanges

        for i in range(7):
            append_exchange(f"prompt {i}", "cli", f"response {i}", True, 0.1)

        exchanges = read_exchanges()
        assert len(exchanges) == 5
        # Oldest two (0, 1) should be gone; newest (2-6) remain
        assert exchanges[0]["prompt"] == "prompt 2"
        assert exchanges[-1]["prompt"] == "prompt 6"


class TestMLAdvancedFeatures:
    """Test ML view advanced features."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_ml_has_feature_importance(self, views_dir):
        """ML view should have feature importance functionality."""
        content = (views_dir / "ml.py").read_text()
        assert "_render_feature_importance" in content
        assert "feature-importance" in content
        assert "Top K" in content or "top_k" in content

    def test_ml_has_hyperparameter_tuning(self, views_dir):
        """ML view should have hyperparameter tuning section."""
        content = (views_dir / "ml.py").read_text()
        assert "_render_tune_section" in content or "Hyperparameter Tuning" in content
        assert "tune" in content.lower()
        assert "n-trials" in content or "n_trials" in content

    def test_ml_tune_has_optuna_params(self, views_dir):
        """ML tune section should have Optuna parameters."""
        content = (views_dir / "ml.py").read_text()
        assert "Number of Trials" in content
        assert "Timeout" in content
        assert "CV" in content or "cv-splits" in content or "cross-validation" in content.lower()

    def test_ml_inspect_shows_performance_metrics(self, views_dir):
        """Model inspection should show performance metrics."""
        content = (views_dir / "ml.py").read_text()
        assert "Performance Metrics" in content
        assert "q10_calibration" in content or "Q10 Cal" in content
        assert "q50_calibration" in content or "Q50 Cal" in content
        assert "q90_calibration" in content or "Q90 Cal" in content

    def test_ml_train_supports_ensemble(self, views_dir):
        """ML train should support ensemble model type."""
        content = (views_dir / "ml.py").read_text()
        assert "Ensemble" in content
        assert "train-ensemble" in content
        assert "ensemble_algos" in content or "algorithms" in content.lower()

    def test_ml_predict_uses_correct_command_by_type(self, views_dir):
        """ML predict should use correct command based on model type."""
        content = (views_dir / "ml.py").read_text()
        assert "predict-ensemble" in content
        assert "predict-classifier" in content
        assert "predict_cmd" in content or "model_type" in content
