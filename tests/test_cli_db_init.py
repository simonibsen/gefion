"""
Tests for db-init CLI command feature seeding functionality.

Note: These tests focus on the seeding logic, not full schema initialization,
since schema init requires an empty database which isn't guaranteed in test suites.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
import pytest
from pathlib import Path
from typer.testing import CliRunner
from gefion import cli
from gefion.cli import import_functions_from_directory, import_definitions_from_directory
from gefion.cli_helpers import db_connection, init_schema_tables
from gefion.db.schema import test_db_url


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


runner = CliRunner(env={"DATABASE_URL": test_db_url()})


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    import psycopg
    url = test_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def clean_feature_tables(db_conn):
    """Ensure feature tables exist before test.

    Note: We don't delete existing data because:
    1. computed_features has FK references to feature_definitions
    2. The import functions use upsert (ON CONFLICT DO UPDATE), so existing data is fine
    """
    with db_conn.cursor() as cur:
        # Ensure tables exist before trying to use them
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'feature_functions'
            )
        """)
        if not cur.fetchone()[0]:
            # Tables don't exist - run schema init to create them
            init_schema_tables(db_conn, ["feature_functions", "feature_definitions"])

        # Only delete rows NOT referenced by computed_features
        cur.execute("""
            DELETE FROM feature_definitions
            WHERE id NOT IN (SELECT DISTINCT feature_id FROM computed_features WHERE feature_id IS NOT NULL)
        """)
        cur.execute("DELETE FROM feature_functions WHERE true")
    yield


class TestFeatureSeeding:
    """Tests for feature seeding functionality used by db-init."""

    def test_import_functions_from_directory(self, db_conn, clean_feature_tables):
        """import_functions_from_directory should load JSON files into feature_functions table."""
        # Get the feature-functions directory
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        fx_dir = package_dir / "feature-functions"

        # Verify it exists
        assert fx_dir.exists(), f"feature-functions directory not found at {fx_dir}"

        # Import functions
        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_functions"])
            count = import_functions_from_directory(conn, fx_dir, None)

        assert count > 0, "Expected to import at least one function"

        # Verify in database
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            db_count = cur.fetchone()[0]
            assert db_count == count

    def test_import_definitions_from_directory(self, db_conn, clean_feature_tables):
        """import_definitions_from_directory should load JSON files into feature_definitions table."""
        # Get the feature-definitions directory
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        def_dir = package_dir / "feature-definitions"

        # Verify it exists
        assert def_dir.exists(), f"feature-definitions directory not found at {def_dir}"

        # Import definitions
        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_definitions", "computed_features"])
            count = import_definitions_from_directory(conn, def_dir, None)

        assert count > 0, "Expected to import at least one definition"

        # Verify in database
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_definitions")
            db_count = cur.fetchone()[0]
            assert db_count == count

    def test_seeds_expected_indicator_functions(self, db_conn, clean_feature_tables):
        """Seeding should include expected indicator functions."""
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        fx_dir = package_dir / "feature-functions"

        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_functions"])
            import_functions_from_directory(conn, fx_dir, None)

        # Check for expected functions
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

    def test_seeds_expected_feature_definitions(self, db_conn, clean_feature_tables):
        """Seeding should include expected feature definitions."""
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        def_dir = package_dir / "feature-definitions"

        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_definitions", "computed_features"])
            import_definitions_from_directory(conn, def_dir, None)

        # Check for expected definitions
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

    def test_import_is_idempotent(self, db_conn, clean_feature_tables):
        """Running import twice should not cause errors or duplicates."""
        import gefion
        package_dir = Path(gefion.__file__).parent.parent.parent
        fx_dir = package_dir / "feature-functions"

        # First import
        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_functions"])
            count1 = import_functions_from_directory(conn, fx_dir, None)

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            db_count1 = cur.fetchone()[0]

        # Second import
        with db_connection(test_db_url()) as conn:
            count2 = import_functions_from_directory(conn, fx_dir, None)

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions")
            db_count2 = cur.fetchone()[0]

        # Should have same count (upsert, not insert)
        assert db_count1 == db_count2
        assert count1 == count2

    def test_import_from_nonexistent_directory(self, db_conn):
        """Import should return 0 for nonexistent directory."""
        with db_connection(test_db_url()) as conn:
            init_schema_tables(conn, ["feature_functions"])
            count = import_functions_from_directory(conn, Path("/nonexistent/path"), None)

        assert count == 0
