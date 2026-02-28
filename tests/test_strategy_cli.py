"""
Integration tests for strategy CLI commands.

These tests run against a real database to verify full functionality.
"""
import json
import os
import pytest
import psycopg
from typer.testing import CliRunner

from g2.cli import app
from g2.config import load_settings
from g2.db.schema import test_db_url


runner = CliRunner(env={"DATABASE_URL": test_db_url()})


def require_db():
    """Skip test if DB tests are disabled."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")


def get_db_url():
    """Get database URL for tests."""
    from g2.db.schema import test_db_url
    return test_db_url()


@pytest.fixture(scope="module")
def db_url():
    """Provide database URL, skip if DB not available."""
    require_db()
    url = get_db_url()
    # Verify connection works
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    return url


@pytest.fixture(autouse=True)
def setup_tables(db_url):
    """Setup strategy tables for testing."""
    from g2.db.schema import (
        create_strategy_registry_table,
        create_strategy_configs_table,
    )

    with psycopg.connect(db_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Clean existing
            cur.execute("DROP TABLE IF EXISTS strategy_configs CASCADE;")
            cur.execute("DROP TABLE IF EXISTS strategy_registry CASCADE;")

        # Create tables
        create_strategy_registry_table(conn)
        create_strategy_configs_table(conn)

    yield

    # Cleanup
    with psycopg.connect(db_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS strategy_configs CASCADE;")
            cur.execute("DROP TABLE IF EXISTS strategy_registry CASCADE;")


class TestStrategyListCommand:
    """Integration tests for strategy list CLI command."""

    def test_strategy_list_empty(self, db_url):
        """strategy list shows empty table when no strategies registered."""
        result = runner.invoke(
            app,
            ["strategy", "list", "--db-url", db_url],
        )
        assert result.exit_code == 0
        assert "Registered Strategies" in result.output

    def test_strategy_list_json_empty(self, db_url):
        """strategy list --json returns empty array when no strategies."""
        result = runner.invoke(
            app,
            ["strategy", "list", "--db-url", db_url, "--json"],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["strategies"] == []

    def test_strategy_list_after_seed(self, db_url):
        """strategy list shows seeded strategies."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        # Seed strategies
        with psycopg.connect(db_url) as conn:
            seed_builtin_strategies(conn)

        result = runner.invoke(
            app,
            ["strategy", "list", "--db-url", db_url, "--json"],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        strategies = output["strategies"]

        # Should have all 7 built-in strategies
        assert len(strategies) >= 7

        # Verify structure
        names = [s["name"] for s in strategies]
        assert "momentum" in names
        assert "mean_reversion" in names
        assert "breakout" in names

        # Verify fields present
        momentum = next(s for s in strategies if s["name"] == "momentum")
        assert "description" in momentum
        assert "tags" in momentum
        assert "default_params" in momentum
        assert momentum["default_params"]["lookback_days"] == 20


class TestStrategyConfigsCommand:
    """Integration tests for strategy configs CLI command."""

    def test_strategy_configs_empty(self, db_url):
        """strategy configs shows empty when no configs exist."""
        result = runner.invoke(
            app,
            ["strategy", "configs", "--db-url", db_url, "--json"],
        )
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["configs"] == []

    def test_strategy_configs_after_seed(self, db_url):
        """strategy configs shows seeded default configs."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        with psycopg.connect(db_url) as conn:
            seed_builtin_strategies(conn)

        result = runner.invoke(
            app,
            ["strategy", "configs", "--db-url", db_url, "--json"],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        configs = output["configs"]

        # Should have default configs
        assert len(configs) >= 7

        # Verify structure
        names = [c["name"] for c in configs]
        assert "momentum" in names

        # Verify merged params (defaults from registry)
        momentum_config = next(c for c in configs if c["name"] == "momentum")
        assert momentum_config["strategy_name"] == "momentum"
        assert "params" in momentum_config


class TestStrategyCreateConfigCommand:
    """Integration tests for strategy create-config CLI command."""

    def test_create_config_requires_name(self, db_url):
        """create-config requires --name option."""
        result = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--strategy", "momentum",
                "--db-url", db_url,
            ],
        )
        assert result.exit_code != 0

    def test_create_config_requires_strategy(self, db_url):
        """create-config requires --strategy option."""
        result = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "my_config",
                "--db-url", db_url,
            ],
        )
        assert result.exit_code != 0

    def test_create_config_invalid_strategy(self, db_url):
        """create-config fails for strategy not in registry."""
        result = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "bad_config",
                "--strategy", "nonexistent_strategy",
                "--db-url", db_url,
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_create_config_success(self, db_url):
        """create-config creates a new config in the database."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        # Seed strategies first (need registry entry)
        with psycopg.connect(db_url) as conn:
            seed_builtin_strategies(conn)

        result = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "momentum_aggressive",
                "--strategy", "momentum",
                "--params", '{"lookback_days": 10, "top_n": 5}',
                "--description", "Aggressive momentum config",
                "--db-url", db_url,
                "--json",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["name"] == "momentum_aggressive"
        assert output["strategy"] == "momentum"
        assert "id" in output
        assert output["id"] > 0

        # Verify in database
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, strategy_name, params, description FROM strategy_configs WHERE name = %s",
                    ("momentum_aggressive",),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "momentum_aggressive"
                assert row[1] == "momentum"
                assert row[2]["lookback_days"] == 10
                assert row[2]["top_n"] == 5
                assert row[3] == "Aggressive momentum config"

    def test_create_config_appears_in_list(self, db_url):
        """Created config appears in strategy configs list."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        with psycopg.connect(db_url) as conn:
            seed_builtin_strategies(conn)

        # Create a custom config
        runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "custom_breakout",
                "--strategy", "breakout",
                "--params", '{"lookback_days": 30}',
                "--db-url", db_url,
            ],
        )

        # List configs
        result = runner.invoke(
            app,
            ["strategy", "configs", "--db-url", db_url, "--json"],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        names = [c["name"] for c in output["configs"]]
        assert "custom_breakout" in names

        # Verify merged params
        custom = next(c for c in output["configs"] if c["name"] == "custom_breakout")
        assert custom["params"]["lookback_days"] == 30
        # volume_threshold should come from defaults
        assert custom["params"]["volume_threshold"] == 1.5

    def test_create_config_duplicate_name_fails(self, db_url):
        """create-config fails if name already exists."""
        from g2.strategies.dispatcher import seed_builtin_strategies

        with psycopg.connect(db_url) as conn:
            seed_builtin_strategies(conn)

        # Create first config
        result1 = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "unique_config",
                "--strategy", "momentum",
                "--db-url", db_url,
            ],
        )
        assert result1.exit_code == 0

        # Try to create duplicate
        result2 = runner.invoke(
            app,
            [
                "strategy", "create-config",
                "--name", "unique_config",
                "--strategy", "momentum",
                "--db-url", db_url,
            ],
        )
        assert result2.exit_code != 0


class TestStrategyCommandHelp:
    """Tests for CLI help text."""

    def test_strategy_help(self):
        """strategy --help shows available commands."""
        result = runner.invoke(app, ["strategy", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "configs" in result.output
        assert "create-config" in result.output

    def test_strategy_list_help(self):
        """strategy list --help shows options."""
        result = runner.invoke(app, ["strategy", "list", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--db-url" in result.output

    def test_strategy_create_config_help(self):
        """strategy create-config --help shows required options."""
        result = runner.invoke(app, ["strategy", "create-config", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--strategy" in result.output
        assert "--params" in result.output
        assert "--description" in result.output
