"""
TDD tests for PluginOrchestrator in dispatcher.

Tests the plugin discovery and execution system that enables
meta-functions to discover and execute their plugins from the database.
"""
import os
import psycopg
import pytest
import pandas as pd
from datetime import date
from g2.db import schema
from g2.features.dispatcher import PluginOrchestrator


@pytest.fixture
def db_conn():
    """Create test database connection."""
    # Clear plugin cache before each test
    from g2.features.dispatcher import PluginOrchestrator
    PluginOrchestrator._PLUGIN_CACHE.clear()

    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_plugin_orchestrator_discovers_plugins(db_conn):
    """Test that PluginOrchestrator discovers enabled plugins for a meta-function."""
    schema.create_feature_functions_table(db_conn)

    # Insert meta-function
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled
            ) VALUES (
                'compute_indicators', '1.0', 'python', 'def compute(...): pass', 'active', TRUE
            )
        """)

    # Insert active plugin
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_rsi', '1.0', 'python',
                'def compute(rows, spec, cache=None):\n    return {"rsi": 50.0}',
                'active', TRUE, 'compute_indicators'
            )
        """)

    # Insert inactive plugin (should be ignored)
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_macd', '1.0', 'python',
                'def compute(rows, spec, cache=None):\n    return {"macd": 0.0}',
                'active', FALSE, 'compute_indicators'
            )
        """)

    orchestrator = PluginOrchestrator(db_conn, 'compute_indicators')

    assert 'indicator_rsi' in orchestrator.plugins
    assert 'indicator_macd' not in orchestrator.plugins
    assert len(orchestrator.plugins) == 1


def test_plugin_orchestrator_executes_plugins(db_conn):
    """Test that PluginOrchestrator executes plugins and merges results."""
    schema.create_feature_functions_table(db_conn)

    # Insert plugins
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_rsi', '1.0', 'python',
                'import pandas as pd\ndef compute(df, spec, cache=None):\n    result = df[[\"date\"]].copy()\n    result[\"rsi_14\"] = 50.0\n    return result',
                'active', TRUE, 'compute_indicators'
            )
        """)

    orchestrator = PluginOrchestrator(db_conn, 'compute_indicators')

    # Create test data
    input_df = pd.DataFrame({
        'date': [date(2025, 1, 1), date(2025, 1, 2)],
        'close': [100.0, 101.0]
    })

    # Execute plugin
    spec = {'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi', 'period': 14}}
    result_df = orchestrator.execute_plugins(input_df, [spec], cache={})

    assert 'date' in result_df.columns
    assert 'rsi_14' in result_df.columns
    assert len(result_df) == 2
    assert result_df['rsi_14'].iloc[0] == 50.0


def test_plugin_orchestrator_caches_functions(db_conn):
    """Test that PluginOrchestrator caches loaded functions."""
    schema.create_feature_functions_table(db_conn)

    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_rsi', '1.0', 'python',
                'def compute(rows, spec, cache=None):\n    return {"rsi": 50.0}',
                'active', TRUE, 'compute_indicators'
            )
        """)

    # First call - loads from DB
    orchestrator1 = PluginOrchestrator(db_conn, 'compute_indicators')
    assert 'indicator_rsi' in orchestrator1.plugins

    # Second call - should use cache
    orchestrator2 = PluginOrchestrator(db_conn, 'compute_indicators')
    assert 'indicator_rsi' in orchestrator2.plugins

    # Should be the same function object (cached)
    assert orchestrator1.plugins['indicator_rsi'] is orchestrator2.plugins['indicator_rsi']


def test_plugin_orchestrator_handles_multiple_plugins(db_conn):
    """Test that PluginOrchestrator can execute multiple plugins and merge results."""
    schema.create_feature_functions_table(db_conn)

    # Insert multiple plugins
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_rsi', '1.0', 'python',
                'import pandas as pd\ndef compute(df, spec, cache=None):\n    result = df[[\"date\"]].copy()\n    result[\"rsi_14\"] = 50.0\n    return result',
                'active', TRUE, 'compute_indicators'
            ),
            (
                'indicator_sma', '1.0', 'python',
                'import pandas as pd\ndef compute(df, spec, cache=None):\n    result = df[[\"date\"]].copy()\n    result[\"sma_20\"] = df[\"close\"].rolling(20).mean()\n    return result',
                'active', TRUE, 'compute_indicators'
            )
        """)

    orchestrator = PluginOrchestrator(db_conn, 'compute_indicators')

    # Create test data
    input_df = pd.DataFrame({
        'date': [date(2025, 1, i) for i in range(1, 31)],
        'close': [100.0 + i for i in range(30)]
    })

    # Execute both plugins
    specs = [
        {'name': 'indicator_rsi_14', 'params': {'indicator': 'rsi'}},
        {'name': 'indicator_sma_20', 'params': {'indicator': 'sma20'}}
    ]
    result_df = orchestrator.execute_plugins(input_df, specs, cache={})

    assert 'date' in result_df.columns
    assert 'rsi_14' in result_df.columns
    assert 'sma_20' in result_df.columns
    assert len(result_df) == 30


def test_plugin_orchestrator_empty_plugins(db_conn):
    """Test that PluginOrchestrator handles meta-functions with no plugins."""
    schema.create_feature_functions_table(db_conn)

    # No plugins inserted
    orchestrator = PluginOrchestrator(db_conn, 'compute_indicators')

    assert len(orchestrator.plugins) == 0

    # Executing with no plugins should return empty DataFrame
    input_df = pd.DataFrame({'date': [date(2025, 1, 1)]})
    result_df = orchestrator.execute_plugins(input_df, [], cache={})

    assert len(result_df) == 1
    assert 'date' in result_df.columns
