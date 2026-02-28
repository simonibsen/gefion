"""
TDD tests for strategy registry system.

Tests for strategy_registry (implementations) and strategy_configs (parameterized instances).
"""
import os
import pytest
import psycopg
from datetime import date, timedelta
from typing import Dict, Any, List

from g2.db import schema
from g2.config import load_settings


def require_db():
    """Skip test if DB tests are disabled."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")


def create_connection():
    require_db()
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def setup_tables(conn):
    """Setup strategy tables for testing."""
    from g2.db.schema import (
        create_strategy_registry_table,
        create_strategy_configs_table,
    )

    with conn.cursor() as cur:
        # Clean existing
        cur.execute("DROP TABLE IF EXISTS strategy_configs CASCADE;")
        cur.execute("DROP TABLE IF EXISTS strategy_registry CASCADE;")

    # Create tables
    create_strategy_registry_table(conn)
    create_strategy_configs_table(conn)
    yield

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS strategy_configs CASCADE;")
        cur.execute("DROP TABLE IF EXISTS strategy_registry CASCADE;")


class TestStrategyRegistryTable:
    """Tests for strategy_registry table schema."""

    def test_strategy_registry_table_exists(self, conn):
        """strategy_registry table is created."""
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'strategy_registry'
                );
            """)
            assert cur.fetchone()[0] is True

    def test_strategy_registry_has_required_columns(self, conn):
        """strategy_registry has all required columns."""
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'strategy_registry'
                ORDER BY ordinal_position;
            """)
            columns = [row[0] for row in cur.fetchall()]

        required = [
            'id', 'name', 'module_path', 'class_name',
            'default_params', 'param_schema', 'description',
            'tags', 'enabled', 'created_at'
        ]
        for col in required:
            assert col in columns, f"Missing column: {col}"

    def test_insert_strategy_registry(self, conn):
        """Can insert a strategy into the registry."""
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, description, default_params)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy',
                     'Momentum-based strategy', '{"lookback_days": 20}')
                RETURNING id;
            """)
            strategy_id = cur.fetchone()[0]
            assert strategy_id > 0

    def test_unique_name(self, conn):
        """name must be unique."""
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy');
            """)

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO strategy_registry
                        (name, module_path, class_name)
                    VALUES
                        ('momentum', 'g2.strategies.momentum', 'MomentumStrategy');
                """)


class TestStrategyConfigsTable:
    """Tests for strategy_configs table schema."""

    def test_strategy_configs_table_exists(self, conn):
        """strategy_configs table is created."""
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'strategy_configs'
                );
            """)
            assert cur.fetchone()[0] is True

    def test_strategy_configs_has_required_columns(self, conn):
        """strategy_configs has all required columns."""
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'strategy_configs'
                ORDER BY ordinal_position;
            """)
            columns = [row[0] for row in cur.fetchall()]

        required = [
            'id', 'name', 'strategy_name', 'params', 'description',
            'active', 'created_at', 'updated_at'
        ]
        for col in required:
            assert col in columns, f"Missing column: {col}"

    def test_insert_strategy_config(self, conn):
        """Can insert a strategy config referencing a registry entry."""
        # First insert the registry entry
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy');
            """)

        # Then insert config
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_configs
                    (name, strategy_name, params, description)
                VALUES
                    ('momentum_aggressive', 'momentum',
                     '{"lookback_days": 10, "top_n": 3}',
                     'Aggressive momentum strategy')
                RETURNING id;
            """)
            config_id = cur.fetchone()[0]
            assert config_id > 0

    def test_multiple_configs_same_strategy(self, conn):
        """Multiple configs can reference the same strategy."""
        # Insert registry entry
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy');
            """)

        # Insert two configs with different params
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_configs (name, strategy_name, params)
                VALUES
                    ('momentum_aggressive', 'momentum', '{"lookback_days": 10}'),
                    ('momentum_conservative', 'momentum', '{"lookback_days": 30}');
            """)

        # Verify both exist
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM strategy_configs WHERE strategy_name = 'momentum';")
            assert cur.fetchone()[0] == 2

    def test_foreign_key_constraint(self, conn):
        """strategy_configs.strategy_name must reference strategy_registry.name."""
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO strategy_configs
                        (name, strategy_name, params)
                    VALUES
                        ('nonexistent_config', 'nonexistent_strategy', '{}');
                """)


class TestStrategyDispatcher:
    """Tests for strategy dispatcher (loading strategies from DB)."""

    def test_load_strategy_class(self, conn):
        """Can load a strategy class by name from registry."""
        from g2.strategies.dispatcher import load_strategy_class

        # Insert registry entry
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy', true);
            """)

        strategy_class = load_strategy_class(conn, 'momentum')

        assert strategy_class is not None
        from g2.strategies.momentum import MomentumStrategy
        assert strategy_class == MomentumStrategy

    def test_load_strategy_class_disabled(self, conn):
        """Disabled strategies are not loaded."""
        from g2.strategies.dispatcher import load_strategy_class

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy', false);
            """)

        strategy_class = load_strategy_class(conn, 'momentum')
        assert strategy_class is None

    def test_get_strategy_config(self, conn):
        """Can load a strategy config with merged params."""
        from g2.strategies.dispatcher import get_strategy_config

        # Insert registry entry with default_params
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, default_params, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy',
                     '{"lookback_days": 20, "top_n": 10}', true);
            """)
            # Insert config that overrides some params
            cur.execute("""
                INSERT INTO strategy_configs
                    (name, strategy_name, params, active)
                VALUES
                    ('momentum_aggressive', 'momentum',
                     '{"lookback_days": 10}', true);
            """)

        config = get_strategy_config(conn, 'momentum_aggressive')

        assert config is not None
        assert config['name'] == 'momentum_aggressive'
        assert config['strategy_name'] == 'momentum'
        # lookback_days overridden, top_n from defaults
        assert config['params']['lookback_days'] == 10
        assert config['params']['top_n'] == 10

    def test_get_strategy_configs(self, conn):
        """Can list all active strategy configs."""
        from g2.strategies.dispatcher import get_strategy_configs

        # Insert registry and configs
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy', true);
            """)
            cur.execute("""
                INSERT INTO strategy_configs (name, strategy_name, params, active)
                VALUES
                    ('momentum_aggressive', 'momentum', '{"lookback_days": 10}', true),
                    ('momentum_conservative', 'momentum', '{"lookback_days": 30}', true),
                    ('momentum_disabled', 'momentum', '{}', false);
            """)

        configs = get_strategy_configs(conn)

        assert len(configs) == 2
        names = [c['name'] for c in configs]
        assert 'momentum_aggressive' in names
        assert 'momentum_conservative' in names
        assert 'momentum_disabled' not in names

    def test_instantiate_strategy(self, conn):
        """Can instantiate a strategy from config."""
        from g2.strategies.dispatcher import instantiate_strategy

        # Insert registry and config
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, default_params, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy',
                     '{"lookback_days": 20, "top_n": 10, "rebalance_days": 5}', true);
            """)
            cur.execute("""
                INSERT INTO strategy_configs
                    (name, strategy_name, params, active)
                VALUES
                    ('momentum_test', 'momentum',
                     '{"lookback_days": 15, "top_n": 5}', true);
            """)

        strategy = instantiate_strategy(conn, 'momentum_test')

        assert strategy is not None
        assert strategy.lookback_days == 15
        assert strategy.top_n == 5
        assert strategy.rebalance_days == 5  # From defaults

    def test_create_strategy_config(self, conn):
        """Can create a new strategy config."""
        from g2.strategies.dispatcher import create_strategy_config

        # Insert registry entry
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO strategy_registry
                    (name, module_path, class_name, enabled)
                VALUES
                    ('momentum', 'g2.strategies.momentum', 'MomentumStrategy', true);
            """)

        config_id = create_strategy_config(
            conn,
            name='momentum_custom',
            strategy_name='momentum',
            params={'lookback_days': 25},
            description='Custom momentum config'
        )

        assert config_id > 0

        # Verify it was created
        with conn.cursor() as cur:
            cur.execute("SELECT name, params FROM strategy_configs WHERE id = %s;", (config_id,))
            row = cur.fetchone()
            assert row[0] == 'momentum_custom'
            assert row[1]['lookback_days'] == 25

    def test_create_strategy_config_invalid_strategy(self, conn):
        """Creating config with invalid strategy raises ValueError."""
        from g2.strategies.dispatcher import create_strategy_config

        with pytest.raises(ValueError, match="not found in registry"):
            create_strategy_config(
                conn,
                name='invalid_config',
                strategy_name='nonexistent_strategy',
                params={}
            )


class TestStrategySeedData:
    """Tests for seeding built-in strategies."""

    def test_seed_builtin_strategies(self, conn):
        """seed_builtin_strategies populates both tables."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        seed_builtin_strategies(conn)

        # Check registry is seeded
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM strategy_registry;")
            registry_count = cur.fetchone()[0]
            assert registry_count >= 7, "Should have at least 7 built-in strategies"

        # Check configs are seeded
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM strategy_configs;")
            config_count = cur.fetchone()[0]
            assert config_count >= 7, "Should have at least 7 default configs"

    def test_seed_is_idempotent(self, conn):
        """Seeding multiple times doesn't create duplicates."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        seed_builtin_strategies(conn)
        seed_builtin_strategies(conn)  # Second call

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM strategy_registry WHERE name = 'momentum';")
            count = cur.fetchone()[0]
            assert count == 1, "Should not duplicate on re-seed"

    def test_seeded_strategies_can_be_instantiated(self, conn):
        """Seeded strategies can be loaded and instantiated."""
        from g2.strategies.dispatcher import seed_builtin_strategies, instantiate_strategy

        seed_builtin_strategies(conn)

        # Try instantiating each built-in strategy
        strategy_names = ['momentum', 'mean_reversion', 'ma_crossover', 'breakout',
                          'pairs_trading', 'rsi_divergence', 'volatility_contraction']

        for name in strategy_names:
            strategy = instantiate_strategy(conn, name)
            assert strategy is not None, f"Failed to instantiate {name}"
            assert hasattr(strategy, 'generate_signals'), f"{name} missing generate_signals method"
