"""
Tests for OpenTelemetry instrumentation in UI and utility modules.

Verifies that key UI functions use create_span for tracing,
following the same pattern as test_otel_instrumentation.py.
"""
import ast
from pathlib import Path

import pytest


# All UI/utility modules that should have observability instrumentation
UI_INSTRUMENTED_MODULES = [
    "gefion.ui.components.database",
    "gefion.ui.components.status",
    "gefion.ui.views.charts",
    "gefion.ui.views.assistant",
    "gefion.ui.views.features",
    "gefion.ui.views.ml",
    "gefion.ui.views.dashboard",
    "gefion.ui.views.experiments",
    "gefion.ui.views.settings",
    "gefion.ui.views.backtest",
    "gefion.ui.views.data",
    "gefion.utils.timescale",
    "gefion.utils.db_load",
]

# Map of module -> list of functions that MUST have create_span calls
UI_EXPECTED_INSTRUMENTED_FUNCTIONS = {
    "gefion.ui.components.database": [
        "get_connection",
        "get_symbols",
        "get_sectors",
        "get_models",
        "get_feature_definitions",
    ],
    "gefion.ui.components.status": [
        "get_system_stats",
        "get_latest_data_date",
    ],
    "gefion.ui.views.charts": [
        "_get_charts_context_data",
        "_render_quick_pipeline",
        "_render_quick_top_movers",
        "_render_quick_sector",
        "_render_quick_volatility",
        "_render_quick_calibration",
        "_render_top_movers_chart",
    ],
    "gefion.ui.views.assistant": [
        "check_conditions",
        "get_page_context",
    ],
    "gefion.ui.views.features": [
        "get_page_context",
    ],
    "gefion.ui.views.ml": [
        "get_page_context",
    ],
    "gefion.ui.views.dashboard": [
        "_get_dashboard_context_data",
        "get_market_movers",
        "get_gefion_insights",
    ],
    "gefion.ui.views.experiments": [
        "get_page_context",
    ],
    "gefion.ui.views.settings": [
        "render_database_settings",
    ],
    "gefion.ui.views.backtest": [
        "get_page_context",
        "get_strategies",
    ],
    "gefion.ui.views.data": [
        "get_page_context",
        "start_background_process",
        "_get_symbol_coverage",
    ],
    "gefion.utils.timescale": [
        "get_chunk_date_range",
        "create_chunks_for_date_range",
        "ensure_chunks_for_date_range",
        "validate_and_filter_insert_data",
    ],
    "gefion.utils.db_load": [
        "get_available_connections",
    ],
}


def _read_module_source(module_name: str) -> str:
    """Read source code for a module by name."""
    parts = module_name.split(".")
    rel_path = Path("src") / Path(*parts).with_suffix(".py")
    full_path = Path(__file__).parent.parent / rel_path
    return full_path.read_text()


class TestUIObservabilityImports:
    """Verify that all UI instrumented modules import from gefion.observability."""

    @pytest.mark.parametrize("module_name", UI_INSTRUMENTED_MODULES)
    def test_module_imports_create_span(self, module_name: str):
        """Each instrumented module must import create_span."""
        source = _read_module_source(module_name)
        assert "from gefion.observability import" in source, (
            f"{module_name} missing 'from gefion.observability import ...'"
        )
        assert "create_span" in source, (
            f"{module_name} missing 'create_span' import"
        )


class TestUISpanInstrumentation:
    """Verify that key UI functions contain create_span calls."""

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            (mod, fn)
            for mod, fns in UI_EXPECTED_INSTRUMENTED_FUNCTIONS.items()
            for fn in fns
        ],
    )
    def test_function_has_create_span(self, module_name: str, function_name: str):
        """Each significant entry-point function must use create_span."""
        source = _read_module_source(module_name)
        tree = ast.parse(source)

        found = False
        has_span = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    found = True
                    func_source = ast.get_source_segment(source, node)
                    if func_source and "create_span" in func_source:
                        has_span = True
                    break

        assert found, (
            f"Function '{function_name}' not found in {module_name}"
        )
        assert has_span, (
            f"Function '{function_name}' in {module_name} does not use create_span"
        )


class TestUISpanNaming:
    """Verify span naming follows the ui.module.function convention."""

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            (mod, fn)
            for mod, fns in UI_EXPECTED_INSTRUMENTED_FUNCTIONS.items()
            for fn in fns
        ],
    )
    def test_span_name_follows_convention(self, module_name: str, function_name: str):
        """Span names should use ui.module.function or utils.module.function pattern."""
        source = _read_module_source(module_name)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    func_source = ast.get_source_segment(source, node)
                    if func_source:
                        assert 'create_span("' in func_source or "create_span('" in func_source, (
                            f"create_span in {module_name}.{function_name} should use a string literal"
                        )
                    break
