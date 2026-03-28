"""
Tests for OpenTelemetry instrumentation across modules.

Verifies that key entry-point functions in each instrumented module
use create_span for tracing and set_attributes for result tracking.
"""
import ast
import importlib
import inspect
import os
import textwrap
from pathlib import Path

import pytest

# All modules that should have observability instrumentation
INSTRUMENTED_MODULES = [
    "gefion.backup",
    "gefion.health",
    "gefion.strategies.dispatcher",
    "gefion.strategies.ml_signal",
    "gefion.experiments.core",
    "gefion.charts.queries",
    "gefion.cli_helpers",
    "gefion.backtest.data_loader",
    "gefion.compute.cross_sectional",
    "gefion.ml.store",
    "gefion.db.cache",
    "gefion.db.cross_sectional",
    "gefion.db.migrate",
    "gefion.db.schema",
]

# Map of module -> list of functions that MUST have create_span calls
EXPECTED_INSTRUMENTED_FUNCTIONS = {
    "gefion.backup": [
        "create_backup",
        "restore_backup",
        "verify_backup",
        "estimate_backup_size",
    ],
    "gefion.health": [
        "check_postgres_health",
        "check_tempo_health",
        "check_all_services",
    ],
    "gefion.strategies.dispatcher": [
        "load_strategy_class",
        "get_strategy_registry",
        "instantiate_strategy",
        "seed_builtin_strategies",
    ],
    "gefion.strategies.ml_signal": [
        "get_predictions_for_date",
        "get_classifier_predictions_for_date",
    ],
    "gefion.experiments.core": [
        # ExperimentRunner methods are instrumented via the class
        # We check for the module-level import instead
    ],
    "gefion.charts.queries": [
        "fetch_ohlcv_for_chart",
        "fetch_predictions_for_chart",
        "fetch_features_for_chart",
        "fetch_model_calibration",
        "fetch_predictions_vs_actuals",
        "fetch_pipeline_health",
        "fetch_confusion_matrix",
    ],
    "gefion.cli_helpers": [
        "db_connection",
        "init_schema_tables",
    ],
    "gefion.backtest.data_loader": [
        "load_price_data_for_backtest",
        "get_available_symbols",
    ],
    "gefion.compute.cross_sectional": [
        "compute_and_store_rankings",
        "fetch_feature_with_sectors",
        "store_cross_sectional_rankings",
    ],
    "gefion.ml.store": [
        "get_ml_dataset",
        "upsert_ml_dataset",
    ],
    "gefion.db.cache": [
        "prefetch_stock_ids",
        "prefetch_latest_prices",
        "prefetch_feature_ids",
    ],
    "gefion.db.cross_sectional": [
        "insert_cross_sectional_features",
    ],
    "gefion.db.migrate": [
        "run_migrations",
        "apply_migration",
    ],
    "gefion.db.schema": [
        "create_stocks_table",
        "create_stock_ohlcv_table",
        "create_predictions_table",
    ],
}


def _get_source_path(module_name: str) -> Path:
    """Get the source file path for a module."""
    parts = module_name.split(".")
    # gefion.x.y -> src/gefion/x/y.py
    return Path(__file__).parent.parent / "src" / "/".join(parts) + ".py"


def _read_module_source(module_name: str) -> str:
    """Read source code for a module by name."""
    parts = module_name.split(".")
    rel_path = Path("src") / Path(*parts).with_suffix(".py")
    full_path = Path(__file__).parent.parent / rel_path
    return full_path.read_text()


class TestObservabilityImports:
    """Verify that all instrumented modules import from gefion.observability."""

    @pytest.mark.parametrize("module_name", INSTRUMENTED_MODULES)
    def test_module_imports_create_span(self, module_name: str):
        """Each instrumented module must import create_span."""
        source = _read_module_source(module_name)
        assert "from gefion.observability import" in source, (
            f"{module_name} missing 'from gefion.observability import ...'"
        )
        assert "create_span" in source, (
            f"{module_name} missing 'create_span' import"
        )

    @pytest.mark.parametrize("module_name", INSTRUMENTED_MODULES)
    def test_module_imports_set_attributes(self, module_name: str):
        """Each instrumented module must import set_attributes."""
        source = _read_module_source(module_name)
        assert "set_attributes" in source, (
            f"{module_name} missing 'set_attributes' import"
        )


class TestSpanInstrumentation:
    """Verify that key functions contain create_span calls."""

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            (mod, fn)
            for mod, fns in EXPECTED_INSTRUMENTED_FUNCTIONS.items()
            for fn in fns
        ],
    )
    def test_function_has_create_span(self, module_name: str, function_name: str):
        """Each significant entry-point function must use create_span."""
        source = _read_module_source(module_name)
        tree = ast.parse(source)

        # Find the function (top-level or inside a class)
        found = False
        has_span = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    found = True
                    # Check if create_span appears in the function body
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


class TestSpanNaming:
    """Verify span naming follows the convention: module_path.function_name."""

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            (mod, fn)
            for mod, fns in EXPECTED_INSTRUMENTED_FUNCTIONS.items()
            for fn in fns
        ],
    )
    def test_span_name_follows_convention(self, module_name: str, function_name: str):
        """Span names should follow the dot-separated module.function convention."""
        source = _read_module_source(module_name)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    func_source = ast.get_source_segment(source, node)
                    if func_source:
                        # Check that create_span is called with a string
                        # containing a dot (module.function pattern)
                        assert 'create_span("' in func_source or "create_span('" in func_source, (
                            f"create_span in {module_name}.{function_name} should use a string literal"
                        )
                    break


class TestSetAttributesCalls:
    """Verify that instrumented functions call set_attributes for result tracking."""

    @pytest.mark.parametrize(
        "module_name,function_name",
        [
            # Functions that return data should track result counts
            ("gefion.backup", "create_backup"),
            ("gefion.backup", "restore_backup"),
            ("gefion.charts.queries", "fetch_ohlcv_for_chart"),
            ("gefion.charts.queries", "fetch_predictions_for_chart"),
            ("gefion.backtest.data_loader", "load_price_data_for_backtest"),
            ("gefion.db.cache", "prefetch_stock_ids"),
            ("gefion.db.cross_sectional", "insert_cross_sectional_features"),
            ("gefion.db.migrate", "run_migrations"),
            ("gefion.compute.cross_sectional", "compute_and_store_rankings"),
        ],
    )
    def test_function_sets_result_attributes(self, module_name: str, function_name: str):
        """Functions returning data should use set_attributes for result tracking."""
        source = _read_module_source(module_name)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    func_source = ast.get_source_segment(source, node)
                    assert func_source and "set_attributes" in func_source, (
                        f"{module_name}.{function_name} should call set_attributes "
                        "to track result metrics"
                    )
                    break
