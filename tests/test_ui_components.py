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

    def test_all_ui_views_compile(self, ui_dir):
        """Every UI view module must be valid Python (no syntax errors)."""
        import py_compile
        views_dir = ui_dir / "views"
        for py_file in sorted(views_dir.glob("*.py")):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"Syntax error in {py_file.name}: {e}")

    def test_all_ui_components_compile(self, ui_dir):
        """Every UI component module must be valid Python (no syntax errors)."""
        import py_compile
        components_dir = ui_dir / "components"
        for py_file in sorted(components_dir.glob("*.py")):
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"Syntax error in {py_file.name}: {e}")

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
        assert "def get_gefion_insights(" in content

    def test_dashboard_insights_handles_missing_ml_tables(self, ui_dir):
        """Dashboard insights should handle missing ML tables gracefully.

        quantile_predictions and model_performance may not exist yet.
        """
        content = (ui_dir / "views" / "dashboard.py").read_text()
        # Should have try/except around ML table queries
        assert "# Predictions - table may not exist yet" in content or "predictions WHERE prediction_type" in content
        assert "# Model performance - table may not exist yet" in content

    def test_ml_view_predictions_supports_both_types(self, ui_dir):
        """ML view predictions page must support both quantile and trend_class types."""
        content = (ui_dir / "views" / "ml.py").read_text()
        # Must have a type filter/toggle
        assert "pred_filter_type" in content
        # Must query both prediction types
        assert "prediction_type = 'quantile'" in content or "prediction_values->>'q10'" in content
        assert "predicted_class" in content

    def test_charts_has_render_function(self, ui_dir):
        """Charts view should have render_charts function."""
        content = (ui_dir / "views" / "charts.py").read_text()
        assert "def render_charts(" in content

    def test_charts_uses_d3_renderers(self, ui_dir):
        """Charts view should import from gefion.charts.d3.renderers, not gefion.charts.renderers."""
        content = (ui_dir / "views" / "charts.py").read_text()
        assert "from gefion.charts.d3.renderers import" in content, (
            "charts.py must import from gefion.charts.d3.renderers"
        )
        # Should NOT import chart creators from the old Plotly renderers
        assert "from gefion.charts.renderers import create_" not in content, (
            "charts.py must not import create_* from gefion.charts.renderers (Plotly)"
        )

    def test_charts_uses_components_html_not_plotly(self, ui_dir):
        """Charts view should use components.html() instead of st.plotly_chart()."""
        content = (ui_dir / "views" / "charts.py").read_text()
        assert "st.plotly_chart(" not in content, (
            "charts.py must not use st.plotly_chart (use components.html for D3)"
        )
        assert "components.html(" in content, (
            "charts.py must use components.html() to render D3 HTML charts"
        )
        assert "import streamlit.components.v1 as components" in content, (
            "charts.py must import streamlit.components.v1 as components"
        )

    def test_experiments_view_renders_trials_chart(self, ui_dir):
        """Experiment results should render the D3 trials scatter inline."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "create_experiment_trials" in content, (
            "experiments.py must render the trials chart in the results section"
        )
        assert "fetch_experiment_trials_for_chart" in content

    def test_experiments_view_renders_heatmap_when_applicable(self, ui_dir):
        """Two-parameter experiments should get a sensitivity heatmap."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "create_experiment_heatmap" in content
        assert "build_heatmap_data" in content

    def test_experiments_view_renders_fdr_chart_in_cycles(self, ui_dir):
        """Cycle details should render the FDR summary chart."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "create_experiment_fdr" in content
        assert "fetch_cycle_fdr_for_chart" in content

    def test_experiments_view_uses_components_html(self, ui_dir):
        """Experiment charts must embed D3 HTML via components.html()."""
        content = (ui_dir / "views" / "experiments.py").read_text()
        assert "import streamlit.components.v1 as components" in content
        assert "components.html(" in content

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


class TestGetPageContext:
    """Test that all view files with get_page_context() define the function correctly."""

    VIEWS_WITH_PAGE_CONTEXT = [
        "ml.py",
        "dashboard.py",
        "data.py",
        "features.py",
        "charts.py",
        "backtest.py",
        "experiments.py",
    ]

    EXPECTED_PAGE_NAMES = {
        "ml.py": "ML Pipeline",
        "dashboard.py": "Dashboard",
        "data.py": "Data Management",
        "features.py": "Features",
        "charts.py": "Charts",
        "backtest.py": "Backtesting",
        "experiments.py": "Experiments",
    }

    @pytest.fixture
    def views_dir(self):
        """Get the views source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    @pytest.mark.parametrize("view_file", VIEWS_WITH_PAGE_CONTEXT)
    def test_view_has_get_page_context_function(self, views_dir, view_file):
        """Each target view should define get_page_context()."""
        content = (views_dir / view_file).read_text()
        assert "def get_page_context(" in content, (
            f"{view_file} must define get_page_context()"
        )

    @pytest.mark.parametrize("view_file", VIEWS_WITH_PAGE_CONTEXT)
    def test_get_page_context_returns_dict_with_required_keys(self, views_dir, view_file):
        """get_page_context() must return a dict with page_name and summary."""
        content = (views_dir / view_file).read_text()
        assert '"page_name"' in content or "'page_name'" in content, (
            f"{view_file} get_page_context must set page_name"
        )
        assert '"summary"' in content or "'summary'" in content, (
            f"{view_file} get_page_context must set summary"
        )

    @pytest.mark.parametrize("view_file", VIEWS_WITH_PAGE_CONTEXT)
    def test_get_page_context_has_correct_page_name(self, views_dir, view_file):
        """get_page_context() must return the expected page_name."""
        content = (views_dir / view_file).read_text()
        expected = self.EXPECTED_PAGE_NAMES[view_file]
        assert expected in content, (
            f"{view_file} get_page_context must include page_name '{expected}'"
        )

    @pytest.mark.parametrize("view_file", [
        v for v in VIEWS_WITH_PAGE_CONTEXT if v != "charts.py"
    ])
    def test_get_page_context_has_try_except(self, views_dir, view_file):
        """Views with DB queries must wrap them in try/except (directly or via cached helper)."""
        content = (views_dir / view_file).read_text()
        # Check the whole file — DB logic may be in a cached helper called by get_page_context
        assert "try:" in content and ("except Exception:" in content or "except Exception as" in content), (
            f"{view_file} must have try/except around DB queries (in get_page_context or its cached helper)"
        )

    @pytest.mark.parametrize("view_file", [
        v for v in VIEWS_WITH_PAGE_CONTEXT if v != "charts.py"
    ])
    def test_get_page_context_uses_get_connection(self, views_dir, view_file):
        """Views with DB queries must use get_connection (directly or via cached helper)."""
        content = (views_dir / view_file).read_text()
        # Check whole file — DB logic may be in cached helper
        assert "get_connection" in content, (
            f"{view_file} get_page_context must use get_connection for DB access"
        )
        return  # Skip function-body extraction below
        idx = content.index("def get_page_context():")
        rest = content[idx:]
        next_def = rest.find("\ndef ")
        if next_def == -1:
            func_body = rest
        else:
            func_body = rest[:next_def]
        assert "get_connection" in func_body, (
            f"{view_file} get_page_context must use get_connection for DB access"
        )

    def test_get_page_context_defined_before_render_function(self, views_dir):
        """get_page_context() should be defined before the main render function."""
        for view_file in self.VIEWS_WITH_PAGE_CONTEXT:
            content = (views_dir / view_file).read_text()
            ctx_pos = content.index("def get_page_context(")
            # Find the first render_ function
            render_pos = content.find("def render_")
            if render_pos != -1:
                assert ctx_pos < render_pos, (
                    f"{view_file}: get_page_context must be before render functions"
                )


class TestChatWidgetIntegration:
    """Test that all view pages call render_chat_widget after their title."""

    # Views that should have a chat widget call (all except assistant.py)
    VIEWS_WITH_CHAT_WIDGET = [
        "dashboard.py",
        "ml.py",
        "data.py",
        "features.py",
        "charts.py",
        "backtest.py",
        "experiments.py",
        "settings.py",
        "documentation.py",
    ]

    @pytest.fixture
    def views_dir(self):
        """Get the views source directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    @pytest.mark.parametrize("view_file", VIEWS_WITH_CHAT_WIDGET)
    def test_view_imports_render_chat_widget(self, views_dir, view_file):
        """Each view must import render_chat_widget from the chat component."""
        content = (views_dir / view_file).read_text()
        assert "from gefion.ui.components.chat import render_chat_widget" in content, (
            f"{view_file} must import render_chat_widget"
        )

    @pytest.mark.parametrize("view_file", VIEWS_WITH_CHAT_WIDGET)
    def test_view_calls_render_chat_widget(self, views_dir, view_file):
        """Each view must call render_chat_widget exactly once."""
        content = (views_dir / view_file).read_text()
        count = content.count("render_chat_widget(")
        assert count == 1, (
            f"{view_file} must call render_chat_widget exactly once, found {count}"
        )

    @pytest.mark.parametrize("view_file", VIEWS_WITH_CHAT_WIDGET)
    def test_chat_widget_after_title(self, views_dir, view_file):
        """render_chat_widget call must appear after the first st.markdown title."""
        content = (views_dir / view_file).read_text()
        title_pos = content.find('st.markdown("# ')
        chat_pos = content.find("render_chat_widget(")
        assert title_pos != -1, f"{view_file} must have a title"
        assert chat_pos != -1, f"{view_file} must call render_chat_widget"
        assert chat_pos > title_pos, (
            f"{view_file}: render_chat_widget must appear after the title"
        )

    def test_assistant_has_chat_widget(self, views_dir):
        """System Operations page uses Ask Gefion like all other pages."""
        content = (views_dir / "assistant.py").read_text()
        assert "render_chat_widget(" in content


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

    def test_uses_hypertable_aware_row_counts(self, status_module_path):
        """Row counts must sum chunk stats, not parent n_live_tup (always 0 for TimescaleDB)."""
        content = status_module_path.read_text()
        # Should use hypertable_approx_row_count or sum across chunks
        assert "hypertable_approx_row_count" in content or "chunks" in content, (
            "Status must use TimescaleDB-aware row counts (parent n_live_tup is always 0)"
        )

    def test_no_max_date_on_hypertables(self, status_module_path):
        """Must not use MAX(date)/MIN(date) on hypertables — use ORDER BY LIMIT 1 instead."""
        content = status_module_path.read_text()
        # MAX(date) FROM stock_ohlcv scans all chunks (1s+), ORDER BY date DESC LIMIT 1 uses index (29ms)
        assert "MAX(date) FROM stock_ohlcv" not in content, (
            "Use ORDER BY date DESC LIMIT 1 instead of MAX(date) on hypertables"
        )

    def test_ml_table_queries_handle_missing_tables(self, status_module_path):
        """ML table queries should handle missing tables gracefully.

        ml_models and predictions may not exist yet, so queries
        should fail gracefully and default to 0 instead of crashing.
        """
        content = status_module_path.read_text()
        # Should have individual try/except blocks for ML tables
        assert "# ML tables may not exist yet" in content
        assert "ml_models" in content
        assert "predictions" in content
        # Both queries should be in their own try/except blocks
        assert "model_count = 0" in content
        assert "prediction_count = 0" in content


class TestCLICommandDisplay:
    """Test that UI views display equivalent CLI commands."""

    @pytest.fixture
    def views_dir(self):
        """Get the views directory."""
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_data_update_no_raw_timeframe_options(self, views_dir):
        """Data update should not expose raw API timeframe options (compact/full)."""
        content = (views_dir / "data.py").read_text()
        # Should not have a selectbox with compact/full — these are API internals
        assert '"compact"' not in content or 'Force full re-download' in content, (
            "Raw AlphaVantage timeframe options should not be in the main UI"
        )

    def test_data_update_has_advanced_options(self, views_dir):
        """Data update should have an Advanced expander for power-user options."""
        content = (views_dir / "data.py").read_text()
        assert 'Advanced' in content
        assert 'full re-download' in content.lower() or 'full history' in content.lower()

    def test_data_update_no_orphaned_caption_above_expander(self, views_dir):
        """The update form must not have a floating caption next to an expander.

        A bare st.caption beside an st.expander creates ambiguous ? icons and
        chevrons that look like they belong together. The caption text should
        be inside the info block or removed.
        """
        content = (views_dir / "data.py").read_text()
        # The caption about "topped up" should not exist as a standalone element
        # — it creates visual clutter next to the expander
        import re
        pattern = r'st\.caption\([^)]*topped up[^)]*\)'
        assert not re.search(pattern, content, re.DOTALL), (
            "Floating st.caption about 'topped up' creates ambiguous UI — "
            "merge the text into the existing st.info block"
        )

    def test_data_view_shows_cli_command(self, views_dir):
        """Data view should display equivalent CLI commands."""
        content = (views_dir / "data.py").read_text()
        # Should show CLI command for data update
        assert 'st.code(' in content
        assert 'language="bash"' in content
        assert 'gefion data-update' in content or 'gefion", "data-update' in content

    def test_cli_data_update_has_since_param(self):
        """CLI data-update must accept a --since option for date lower bound."""
        import inspect
        from gefion.cli import update_all
        sig = inspect.signature(update_all)
        assert "since" in sig.parameters, (
            "data-update CLI must have --since parameter for date lower bound"
        )

    def test_ui_data_update_passes_since_to_cli(self, views_dir):
        """UI data update must pass --since to CLI command when set."""
        content = (views_dir / "data.py").read_text()
        assert "--since" in content, (
            "UI data update must pass --since to CLI command"
        )

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

    def test_process_state_has_completed_at(self, views_dir):
        """ProcessState should track completion time for auto-clearing stale states."""
        content = (views_dir / "data.py").read_text()
        assert 'completed_at' in content

    def test_stale_process_state_auto_cleared(self, views_dir):
        """Completed process states should be auto-cleared after a timeout."""
        content = (views_dir / "data.py").read_text()
        # get_process_state should auto-clear stale completed states
        assert 'completed_at' in content
        # Should check elapsed time since completion
        assert 'auto-clear' in content.lower() or 'stale' in content.lower()

    def test_render_process_status_shows_cli_output(self, views_dir):
        """Process status display should show CLI output log."""
        content = (views_dir / "data.py").read_text()
        assert 'Event Log' in content
        assert 'st.expander' in content

    def test_render_process_status_uses_getattr_for_backwards_compat(self, views_dir):
        """Process status should use getattr for old session state objects."""
        content = (views_dir / "data.py").read_text()
        # Should handle old session state objects that don't have new fields
        assert "getattr(state, 'rate_per_sec'" in content
        assert "getattr(state, 'output_lines'" in content


class TestCullStatusRenderer:
    """Test the cull status renderer handles per-table progress."""

    @pytest.fixture
    def views_dir(self):
        return Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views"

    def test_cull_status_handles_progress_events(self, views_dir):
        """Cull status renderer should display per-table progress lines."""
        content = (views_dir / "data.py").read_text()
        # Should handle "phase" key in JSON progress events
        assert '"phase"' in content or "'phase'" in content
        # Should show table name being processed
        assert 'culling' in content.lower() or 'processing' in content.lower() or 'deleting' in content.lower()

    def test_cull_status_shows_vacuum_phase(self, views_dir):
        """Cull status should show vacuum phase."""
        content = (views_dir / "data.py").read_text()
        assert 'vacuum' in content.lower() or 'Vacuum' in content

    def test_cull_status_shows_summary_when_no_events(self, views_dir):
        """Cull complete must show a fallback when no structured progress events are available.

        The renderer must handle the case where output_lines exist but none
        match the expected JSON structure (no 'phase' keys), showing raw output
        or a 'no data found' message instead of an empty expander.
        """
        content = (views_dir / "data.py").read_text()
        import re
        # After the progress_events/final_result parsing, there must be a
        # fallback path that shows something when both are empty
        # Look for: handles case where no progress_events AND no final_result
        assert re.search(
            r'not\s+progress_events.*not\s+final_result|'
            r'not\s+final_result.*not\s+progress_events|'
            r'No data|no rows deleted',
            content,
            re.DOTALL,
        ), (
            "_render_cull_status must have a fallback for when no structured "
            "events or results are captured (empty expander body)"
        )


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


class TestExperimentsUITabs:
    """Test experiments page has Discovery and Cycles tabs."""

    @pytest.fixture
    def experiments_content(self):
        return (Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views" / "experiments.py").read_text()

    def test_has_discovery_tab(self, experiments_content):
        """Experiments page must have a Discovery tab."""
        assert "Discovery" in experiments_content
        assert "render_discovery_section" in experiments_content

    def test_has_cycles_tab(self, experiments_content):
        """Experiments page must have a Cycles tab."""
        assert "Cycles" in experiments_content
        assert "render_cycles_section" in experiments_content

    def test_has_four_tabs(self, experiments_content):
        """Experiments page should have 4 tabs: Discovery, Experiments, Results, Cycles."""
        assert "tab1, tab2, tab3, tab4" in experiments_content

    def test_propose_in_discovery(self, experiments_content):
        """Propose form should be inside the Discovery tab, not a separate tab."""
        assert "Manual Experiment" in experiments_content
        assert "render_propose_section" in experiments_content

    def test_list_has_run_button_for_approved(self, experiments_content):
        """List tab should have Run button for approved experiments."""
        assert "Ready to Run" in experiments_content
        assert 'run_{exp[0]}' in experiments_content or "run_" in experiments_content

    def test_propose_supports_all_experiment_types(self, experiments_content):
        """Propose form must support all experiment types, not just strategy_params."""
        assert "hyperparameter" in experiments_content
        assert "model_comparison" in experiments_content
        assert "feature_engineering" in experiments_content
        assert "feature_selection" in experiments_content
        assert "label_engineering" in experiments_content

    def test_type_filter_includes_all_types(self, experiments_content):
        """List type filter must include all experiment types."""
        assert "model_comparison" in experiments_content
        assert "feature_engineering" in experiments_content
        assert "label_engineering" in experiments_content


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

    def test_assistant_has_chat_widget_entry(self, ui_dir):
        """System Operations should use Ask Gefion for chat input."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "render_chat_widget" in content

    def test_assistant_has_mcp_tool_mapping(self, ui_dir):
        """Assistant should have access to MCP tool map (via import from chat component)."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "MCP_TOOL_MAP" in content
        # Shared module should include key MCP tools
        chat_content = (ui_dir / "components" / "chat.py").read_text()
        assert "data_update" in chat_content
        assert "ml_train" in chat_content
        assert "system_status" in chat_content

    def test_assistant_has_parse_input_function(self, ui_dir):
        """Assistant should have parse_command_input (via import from chat component)."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "parse_command_input" in content

    def test_assistant_has_ai_prompt_mode(self, ui_dir):
        """Chat component should support sending natural language to Claude via claude -p."""
        content = (ui_dir / "components" / "chat.py").read_text()
        # Should use claude CLI for AI prompts
        assert "claude" in content
        assert '"-p"' in content
        # Should have operator context so LLM doesn't do dev operations
        assert "append-system-prompt" in content
        # Should restrict to MCP tools only
        assert "allowedTools" in content

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

    def test_assistant_has_suggested_actions(self, ui_dir):
        """System Operations page should have suggested actions section."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "Suggested Actions" in content
        assert "check_conditions" in content
        assert "render_action_card" in content

    def test_assistant_uses_ask_gefion(self, ui_dir):
        """System Operations page must use the Ask Gefion chat widget."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        assert "render_chat_widget" in content, "Must use Ask Gefion chat widget"

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
        """Every CLI command in MCP_TOOL_MAP (in chat component) must be a real gefion CLI command."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "gefion.cli", "--help"],
            capture_output=True, text=True
        )
        help_text = result.stdout

        content = (ui_dir / "components" / "chat.py").read_text()
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
        content = (ui_dir / "components" / "chat.py").read_text()
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
        content = (ui_dir / "components" / "chat.py").read_text()
        assert "ai_session_active" in content or "session_key" in content, "Must track AI session state"
        assert '"--continue"' in content, "Must add --continue flag for subsequent prompts"

    def test_parse_stream_json_event_exists(self, ui_dir):
        """A function to parse stream-json events must exist."""
        content = (ui_dir / "components" / "chat.py").read_text()
        assert "parse_stream_event" in content, "Must have parse_stream_event function"

    def test_sidebar_system_operations_position(self, ui_dir):
        """System Operations must be the second item in the sidebar PAGES list."""
        content = (ui_dir / "app.py").read_text()
        import re
        tuples = re.findall(r'\("([^"]+)",\s*":[^"]+:"\)', content)
        assert len(tuples) >= 2, "PAGES must have at least 2 entries"
        assert "System Operations" in tuples[1], (
            f"System Operations must be second in PAGES, got '{tuples[1]}'"
        )

    def test_assistant_renamed_to_system_operations(self, ui_dir):
        """The page routing must use 'System Operations'."""
        content = (ui_dir / "app.py").read_text()
        assert "System Operations" in content

    def test_assistant_chat_before_proactive_actions(self, ui_dir):
        """Ask Gefion must appear before proactive action cards in assistant.py."""
        content = (ui_dir / "views" / "assistant.py").read_text()
        render_start = content.find("def render_assistant")
        assert render_start > 0, "render_assistant must exist"
        body = content[render_start:]
        chat_pos = body.find("render_chat_widget")
        conditions_pos = body.find("check_conditions()")
        assert chat_pos > 0 and conditions_pos > 0, "Both chat widget and conditions call must exist"
        assert chat_pos < conditions_pos, (
            "Ask Gefion must appear before proactive actions (check_conditions)"
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
                    "name": "mcp__gefion__health_check",
                    "input": {"verbose": True},
                }]
            }
        })
        result = parse_stream_event(event)
        assert result is not None
        assert result["type"] == "tool_use"
        assert result["tool"] == "mcp__gefion__health_check"

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


class TestChartsPageLayout:
    """Tests for the three-section Charts page layout."""

    @pytest.fixture
    def charts_content(self):
        charts_path = Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views" / "charts.py"
        return charts_path.read_text()

    def test_has_render_suggested_charts(self, charts_content):
        """Charts page must have _render_suggested_charts helper."""
        assert "def _render_suggested_charts(" in charts_content

    def test_has_render_quick_charts(self, charts_content):
        """Charts page must have _render_quick_charts helper."""
        assert "def _render_quick_charts(" in charts_content

    def test_has_render_custom_chart_selector(self, charts_content):
        """Charts page must have _render_custom_chart_selector helper."""
        assert "def _render_custom_chart_selector(" in charts_content

    def test_render_charts_calls_three_sections(self, charts_content):
        """render_charts must call all three section helpers."""
        assert "_render_suggested_charts()" in charts_content
        assert "_render_quick_charts()" in charts_content
        assert "_render_custom_chart_selector()" in charts_content

    def test_custom_chart_in_expander(self, charts_content):
        """Custom chart selector should be inside an expander."""
        assert 'st.expander("Custom Chart"' in charts_content

    def test_suggested_charts_uses_suggestions_module(self, charts_content):
        """Suggested charts section should import from suggestions module."""
        assert "from gefion.charts.d3.suggestions import suggest_visualization" in charts_content

    def test_quick_charts_has_buttons(self, charts_content):
        """Quick charts section should have named quick chart buttons."""
        assert "Sector Heatmap" in charts_content
        assert "Pipeline Health" in charts_content
        assert "Top Movers" in charts_content

    def test_preserves_existing_render_functions(self, charts_content):
        """All existing render_* functions must still be present."""
        expected = [
            "def render_price_chart(",
            "def render_comparison_chart(",
            "def render_correlation_chart(",
            "def render_volatility_chart(",
            "def render_drawdown_chart(",
            "def render_rolling_chart(",
            "def render_sector_chart(",
            "def render_calibration_chart(",
            "def render_pred_vs_actual_chart(",
            "def render_confusion_matrix_chart(",
            "def render_accuracy_chart(",
            "def render_pipeline_health_chart(",
            "def render_portfolio_chart(",
        ]
        for sig in expected:
            assert sig in charts_content, f"Missing: {sig}"

    def test_compiles_cleanly(self):
        """Charts module must compile without syntax errors."""
        import py_compile
        charts_path = Path(__file__).parent.parent / "src" / "gefion" / "ui" / "views" / "charts.py"
        py_compile.compile(str(charts_path), doraise=True)

    def test_page_context_has_enriched_fields(self):
        """get_page_context should return enriched context with data_age key."""
        from gefion.ui.views.charts import get_page_context
        ctx = get_page_context()
        assert isinstance(ctx, dict)
        assert ctx["page_name"] == "Charts"
        # Should have summary
        assert "summary" in ctx
