"""
TDD tests for compute_indicators meta-function using plugin architecture.

Tests that compute_indicators can discover and orchestrate indicator plugins
from the database, maintaining backward compatibility with existing API.
"""
import os
import psycopg
import pytest
from datetime import date
from g2.db import schema
from g2.features.indicators import compute_indicators


@pytest.fixture
def db_conn():
    """Create test database connection with indicator plugins."""
    from g2.features.dispatcher import PluginOrchestrator
    PluginOrchestrator._PLUGIN_CACHE.clear()

    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")

            # Create schema
            schema.create_feature_functions_table(conn)

            # Load indicator plugins
            _load_indicator_plugins(conn)

            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def _load_indicator_plugins(conn):
    """Load indicator plugin functions into database."""
    import json
    from pathlib import Path

    plugin_dir = Path(__file__).parent.parent / "feature-functions" / "plugins"
    if not plugin_dir.exists():
        return

    for plugin_file in plugin_dir.glob("indicator_*.json"):
        with open(plugin_file) as f:
            plugin_def = json.load(f)

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO feature_functions (
                    name, version, language, function_body, description,
                    status, enabled, called_by, created_by
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (name, version) DO UPDATE SET
                    function_body = EXCLUDED.function_body,
                    description = EXCLUDED.description,
                    status = EXCLUDED.status,
                    enabled = EXCLUDED.enabled,
                    called_by = EXCLUDED.called_by
            """, (
                plugin_def['name'],
                plugin_def['version'],
                plugin_def['language'],
                plugin_def['function_body'],
                plugin_def.get('description', ''),
                plugin_def.get('status', 'active'),
                plugin_def.get('enabled', True),
                plugin_def.get('called_by'),
                plugin_def.get('created_by', 'test')
            ))


def test_compute_indicators_uses_plugin_architecture(db_conn):
    """Test that compute_indicators discovers and executes plugins from DB."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 31)
    ]

    # Request RSI computation using dict format (from dispatcher)
    indicators = [
        {'type': 'rsi', 'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi', 'period': 14}}
    ]

    results = compute_indicators(price_rows, indicators, db_conn=db_conn)

    # Verify results
    assert len(results) > 0
    assert all('date' in r for r in results)
    assert any('rsi_14' in r for r in results)


def test_compute_indicators_handles_multiple_plugins(db_conn):
    """Test that compute_indicators can execute multiple plugins."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 31)
    ]

    # Request multiple indicators
    indicators = [
        {'type': 'rsi', 'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi', 'period': 14}},
        {'type': 'sma20', 'name': 'indicator_sma_20', 'params': {'indicator': 'sma20', 'period': 20}},
        {'type': 'macd', 'name': 'indicator_macd', 'params': {'indicator': 'macd'}}
    ]

    results = compute_indicators(price_rows, indicators, db_conn=db_conn)

    # Verify results contain all requested indicators
    assert len(results) > 0

    # Check that at least some results have all indicators
    has_rsi = any('rsi_14' in r for r in results)
    has_sma = any('sma_20' in r for r in results)
    has_macd = any('macd' in r for r in results)

    assert has_rsi, "Should compute RSI"
    assert has_sma, "Should compute SMA"
    assert has_macd, "Should compute MACD"


def test_compute_indicators_maintains_backward_compatibility(db_conn):
    """Test that old string-based API still works."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 21)
    ]

    # Use old string-based format
    indicators = ['rsi', 'sma20']

    results = compute_indicators(price_rows, indicators, db_conn=db_conn)

    # Verify results
    assert len(results) > 0
    assert any('rsi_14' in r for r in results)
    assert any('sma_20' in r for r in results)


def test_compute_indicators_handles_empty_data(db_conn):
    """Test that compute_indicators handles empty price data gracefully."""
    results = compute_indicators([], [], db_conn=db_conn)
    assert results == []


def test_compute_indicators_handles_plugin_failures(db_conn):
    """Test that failing plugins don't break other indicators."""
    # Create minimal price data (too little for some indicators)
    price_rows = [
        {'date': date(2025, 1, 1), 'close': 100.0, 'adjusted_close': 100.0}
    ]

    # Request indicators that will fail with minimal data
    indicators = [
        {'type': 'rsi', 'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi', 'period': 14}},
        {'type': 'sma20', 'name': 'indicator_sma_20', 'params': {'indicator': 'sma20', 'period': 20}}
    ]

    # Should not raise exception
    results, failures = compute_indicators(price_rows, indicators, return_failures=True, db_conn=db_conn)

    # Results should be empty or partial (not enough data for meaningful indicators)
    assert isinstance(results, list)
    assert isinstance(failures, list)
