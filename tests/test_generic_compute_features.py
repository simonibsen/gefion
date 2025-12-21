"""
TDD tests for generic compute_features() meta-function.

Tests that a single generic function can handle all feature types by:
- Reading source_tables/source_columns from feature specs
- Using PluginOrchestrator to discover and execute plugins
- Supporting shared computation caching
- Supporting precompute hooks for optimizations
"""
import os
import psycopg
import pytest
from datetime import date
from g2.db import schema
from g2.features.dispatcher import compute_features_generic


@pytest.fixture
def db_conn():
    """Create test database connection with feature functions."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")

    try:
        from g2.features.dispatcher import PluginOrchestrator
        PluginOrchestrator._PLUGIN_CACHE.clear()

        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")

            # Create schema
            schema.create_feature_functions_table(conn)

            # Load feature function plugins
            _load_feature_function_plugins(conn)

            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def _load_feature_function_plugins(conn):
    """Load feature function plugins from JSON files."""
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


def test_compute_features_single_source_column(db_conn):
    """Test generic function with single source column (RSI use case)."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 31)
    ]

    # Feature spec with single source column
    specs = [{
        'type': 'rsi',
        'name': 'indicator_rsi_14',
        'params': {'indicator': 'rsi', 'period': 14},
        'source_tables': ['stock_ohlcv'],
        'source_columns': ['close']
    }]

    results = compute_features_generic(price_rows, specs, db_conn=db_conn)

    # Verify results
    assert len(results) > 0
    assert all('date' in r for r in results)
    assert any('rsi_14' in r for r in results)


def test_compute_features_multiple_source_columns(db_conn):
    """Test generic function with multiple source columns (ADX use case)."""
    # Create price data with high/low/close
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 31)
    ]

    # Feature spec with multiple source columns
    specs = [{
        'type': 'adx',
        'name': 'indicator_adx_14',
        'params': {'indicator': 'adx', 'period': 14},
        'source_tables': ['stock_ohlcv'],
        'source_columns': ['high', 'low', 'close']
    }]

    results = compute_features_generic(price_rows, specs, db_conn=db_conn)

    # Verify results
    assert len(results) > 0
    assert all('date' in r for r in results)
    assert any('adx_14' in r for r in results)


def test_compute_features_handles_empty_data(db_conn):
    """Test that generic function handles empty data gracefully."""
    results = compute_features_generic([], [], db_conn=db_conn)
    assert results == []


def test_compute_features_handles_missing_columns(db_conn):
    """Test that generic function handles missing columns gracefully."""
    # Create price data WITHOUT high/low columns
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i}
        for i in range(1, 10)
    ]

    # Request ADX (requires high/low/close)
    specs = [{
        'type': 'adx',
        'name': 'indicator_adx_14',
        'params': {'indicator': 'adx', 'period': 14},
        'source_tables': ['stock_ohlcv'],
        'source_columns': ['high', 'low', 'close']
    }]

    # Should not raise exception, just return empty or partial results
    results = compute_features_generic(price_rows, specs, db_conn=db_conn)
    assert isinstance(results, list)


def test_compute_features_with_multiple_features(db_conn):
    """Test that generic function can compute multiple features in one call."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 31)
    ]

    # Multiple feature specs
    specs = [
        {
            'type': 'rsi',
            'name': 'indicator_rsi_14',
            'params': {'indicator': 'rsi', 'period': 14},
            'source_tables': ['stock_ohlcv'],
            'source_columns': ['close']
        },
        {
            'type': 'sma20',
            'name': 'indicator_sma_20',
            'params': {'indicator': 'sma20', 'period': 20},
            'source_tables': ['stock_ohlcv'],
            'source_columns': ['close']
        },
        {
            'type': 'macd',
            'name': 'indicator_macd',
            'params': {'indicator': 'macd'},
            'source_tables': ['stock_ohlcv'],
            'source_columns': ['close']
        }
    ]

    results = compute_features_generic(price_rows, specs, db_conn=db_conn)

    # Verify results contain all requested features
    assert len(results) > 0

    # Check that at least some results have each feature
    has_rsi = any('rsi_14' in r for r in results)
    has_sma = any('sma_20' in r for r in results)
    has_macd = any('macd' in r for r in results)

    assert has_rsi, "Should compute RSI"
    assert has_sma, "Should compute SMA"
    assert has_macd, "Should compute MACD"


def test_compute_features_with_failures(db_conn):
    """Test that return_failures option works."""
    # Create minimal price data (too little for most indicators)
    price_rows = [
        {'date': date(2025, 1, 1), 'close': 100.0, 'adjusted_close': 100.0}
    ]

    # Request indicators that will fail with minimal data
    specs = [
        {
            'type': 'rsi',
            'name': 'indicator_rsi_14',
            'params': {'indicator': 'rsi', 'period': 14},
            'source_tables': ['stock_ohlcv'],
            'source_columns': ['close']
        },
        {
            'type': 'sma20',
            'name': 'indicator_sma_20',
            'params': {'indicator': 'sma20', 'period': 20},
            'source_tables': ['stock_ohlcv'],
            'source_columns': ['close']
        }
    ]

    # Should not raise exception
    results, failures = compute_features_generic(price_rows, specs, return_failures=True, db_conn=db_conn)

    # Results should be empty or partial (not enough data for meaningful indicators)
    assert isinstance(results, list)
    assert isinstance(failures, list)


def test_compute_features_backward_compatible_with_string_specs(db_conn):
    """Test that generic function works with old string-based specs."""
    # Create price data
    price_rows = [
        {'date': date(2025, 1, i), 'close': 100.0 + i, 'adjusted_close': 100.0 + i,
         'high': 101.0 + i, 'low': 99.0 + i, 'volume': 1000000}
        for i in range(1, 21)
    ]

    # Old string-based format (for backward compatibility)
    specs = ['rsi', 'sma20']

    results = compute_features_generic(price_rows, specs, db_conn=db_conn)

    # Verify results
    assert len(results) > 0
    assert any('rsi_14' in r for r in results)
    assert any('sma_20' in r for r in results)


def test_compute_features_requires_db_conn(db_conn):
    """Test that compute_features raises error if db_conn is None."""
    price_rows = [
        {'date': date(2025, 1, 1), 'close': 100.0, 'adjusted_close': 100.0}
    ]
    specs = [{'type': 'rsi', 'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi', 'period': 14}}]

    with pytest.raises(ValueError, match="db_conn is required"):
        compute_features_generic(price_rows, specs, db_conn=None)
