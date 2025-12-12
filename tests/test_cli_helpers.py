"""
Tests for CLI helper functions.

These helpers consolidate repeated patterns across CLI commands.
"""
import os
import pytest
import psycopg
from g2.cli_helpers import parse_comma_separated, upsert_feature_function
from g2.db import schema


class TestParseCommaSeparated:
    """Test comma-separated string parsing helper."""

    def test_parse_simple_list(self):
        """Test parsing a simple comma-separated list."""
        result = parse_comma_separated("foo,bar,baz")
        assert result == ["foo", "bar", "baz"]

    def test_parse_with_spaces(self):
        """Test that extra spaces are stripped."""
        result = parse_comma_separated("  foo  ,  bar  ,  baz  ")
        assert result == ["foo", "bar", "baz"]

    def test_parse_with_empty_values(self):
        """Test that empty values are filtered out."""
        result = parse_comma_separated("foo,,bar,  ,baz")
        assert result == ["foo", "bar", "baz"]

    def test_parse_none_returns_none(self):
        """Test that None input returns None."""
        result = parse_comma_separated(None)
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None."""
        result = parse_comma_separated("")
        assert result is None

    def test_parse_with_lowercase(self):
        """Test lowercase conversion."""
        result = parse_comma_separated("FOO,Bar,baz", lowercase=True)
        assert result == ["foo", "bar", "baz"]

    def test_parse_required_with_values(self):
        """Test required flag with valid values."""
        result = parse_comma_separated("foo,bar", required=True)
        assert result == ["foo", "bar"]

    def test_parse_required_with_none_raises(self):
        """Test required flag with None raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated(None, required=True)

    def test_parse_required_with_empty_raises(self):
        """Test required flag with empty string raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated("", required=True)

    def test_parse_required_with_only_empty_values_raises(self):
        """Test required flag with only empty values raises ValueError."""
        with pytest.raises(ValueError, match="At least one value required"):
            parse_comma_separated("  ,  ,  ", required=True)

    def test_parse_single_value(self):
        """Test parsing a single value."""
        result = parse_comma_separated("foo")
        assert result == ["foo"]

    def test_parse_preserves_case_by_default(self):
        """Test that case is preserved by default."""
        result = parse_comma_separated("Foo,BAR,baz")
        assert result == ["Foo", "BAR", "baz"]


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        # Clean up before tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_functions
                WHERE name LIKE 'helper_test_%'
            """)
        yield conn
        # Clean up after tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_functions
                WHERE name LIKE 'helper_test_%'
            """)


class TestUpsertFeatureFunction:
    """Test feature function upsert helper."""

    def test_insert_new_function(self, db_conn):
        """Test inserting a new feature function."""
        schema.create_feature_functions_table(db_conn)

        payload = {
            "name": "helper_test_func1",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs): return []",
            "description": "Test function",
            "status": "active",
            "enabled": True,
        }

        func_id = upsert_feature_function(db_conn, payload, return_id=True)

        # Verify it was inserted
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT name, version, description FROM feature_functions WHERE id = %s",
                (func_id,)
            )
            row = cur.fetchone()
            assert row[0] == "helper_test_func1"
            assert row[1] == "1.0"
            assert row[2] == "Test function"

    def test_update_existing_function(self, db_conn):
        """Test updating an existing feature function."""
        schema.create_feature_functions_table(db_conn)

        # Insert initial version
        payload1 = {
            "name": "helper_test_func2",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs): return []",
            "description": "Original description",
            "status": "active",
            "enabled": True,
        }
        func_id1 = upsert_feature_function(db_conn, payload1, return_id=True)

        # Update with new description
        payload2 = {
            "name": "helper_test_func2",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs): return [{'value': 42}]",
            "description": "Updated description",
            "status": "active",
            "enabled": True,
        }
        func_id2 = upsert_feature_function(db_conn, payload2, return_id=True)

        # Should be same ID (update not insert)
        assert func_id1 == func_id2

        # Verify it was updated
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT description, function_body FROM feature_functions WHERE id = %s",
                (func_id2,)
            )
            row = cur.fetchone()
            assert row[0] == "Updated description"
            assert "'value': 42" in row[1]

    def test_upsert_without_return_id(self, db_conn):
        """Test upsert without requesting ID."""
        schema.create_feature_functions_table(db_conn)

        payload = {
            "name": "helper_test_func3",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs): return []",
            "status": "active",
            "enabled": True,
        }

        result = upsert_feature_function(db_conn, payload, return_id=False)
        assert result is None

        # Verify it was still inserted
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM feature_functions WHERE name = %s",
                ("helper_test_func3",)
            )
            row = cur.fetchone()
            assert row is not None
