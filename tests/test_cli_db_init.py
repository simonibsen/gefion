"""
Tests for db-init CLI command, including feature seeding.
"""
import os
import pytest
from typer.testing import CliRunner
from g2 import cli


runner = CliRunner(env={"DATABASE_URL": "postgresql://g2:g2pass@localhost:6432/g2"})


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    import psycopg
    url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def clean_feature_tables(db_conn):
    """Clear feature tables before/after test."""
    with db_conn.cursor() as cur:
        cur.execute("TRUNCATE feature_functions, feature_definitions CASCADE")
    yield
    # Don't truncate after - leave seeded data for other tests


class TestDbInitSeeding:
    """Tests for db-init feature seeding."""

    def test_db_init_seeds_feature_functions(self, db_conn, clean_feature_tables):
        """db-init should seed feature functions from feature-functions/ directory."""
        # Verify tables are empty
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            assert cur.fetchone()[0] == 0

        # Run db-init
        result = runner.invoke(cli.app, ["db-init"])

        assert result.exit_code == 0
        assert "Database initialized successfully" in result.stdout
        assert "Seeded" in result.stdout
        assert "feature function" in result.stdout

        # Verify functions were seeded
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            count = cur.fetchone()[0]
            assert count > 0, "Expected feature functions to be seeded"

    def test_db_init_seeds_feature_definitions(self, db_conn, clean_feature_tables):
        """db-init should seed feature definitions from feature-definitions/ directory."""
        # Verify tables are empty
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_definitions")
            assert cur.fetchone()[0] == 0

        # Run db-init
        result = runner.invoke(cli.app, ["db-init"])

        assert result.exit_code == 0
        assert "feature definition" in result.stdout

        # Verify definitions were seeded
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_definitions")
            count = cur.fetchone()[0]
            assert count > 0, "Expected feature definitions to be seeded"

    def test_db_init_seeds_expected_indicators(self, db_conn, clean_feature_tables):
        """db-init should seed the expected built-in indicators."""
        result = runner.invoke(cli.app, ["db-init"])
        assert result.exit_code == 0

        # Check for specific expected functions
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT name FROM feature_functions
                WHERE name IN ('indicator_rsi', 'indicator_macd', 'indicator_bb', 'indicator_sma')
                ORDER BY name
            """)
            functions = [row[0] for row in cur.fetchall()]

        assert "indicator_rsi" in functions
        assert "indicator_macd" in functions
        assert "indicator_bb" in functions
        assert "indicator_sma" in functions

    def test_db_init_seeds_expected_definitions(self, db_conn, clean_feature_tables):
        """db-init should seed specific feature definitions like indicator_rsi_14."""
        result = runner.invoke(cli.app, ["db-init"])
        assert result.exit_code == 0

        # Check for specific expected definitions
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT name FROM feature_definitions
                WHERE name IN ('indicator_rsi_14', 'indicator_sma_20', 'indicator_macd')
                ORDER BY name
            """)
            definitions = [row[0] for row in cur.fetchall()]

        assert "indicator_rsi_14" in definitions
        assert "indicator_sma_20" in definitions
        assert "indicator_macd" in definitions

    def test_db_init_is_idempotent(self, db_conn, clean_feature_tables):
        """Running db-init twice should not cause errors or duplicate data."""
        # First run
        result1 = runner.invoke(cli.app, ["db-init"])
        assert result1.exit_code == 0

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            count1 = cur.fetchone()[0]

        # Second run
        result2 = runner.invoke(cli.app, ["db-init"])
        assert result2.exit_code == 0

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            count2 = cur.fetchone()[0]

        # Should have same count (upsert, not insert)
        assert count1 == count2

    def test_db_init_json_output_includes_seeding(self, db_conn, clean_feature_tables):
        """db-init --json should report seeding in output."""
        result = runner.invoke(cli.app, ["db-init", "--json"])

        assert result.exit_code == 0
        # JSON output should still mention seeding
        assert "Seeded" in result.stdout or "feature" in result.stdout.lower()
