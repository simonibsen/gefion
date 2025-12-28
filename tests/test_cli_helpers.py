"""
Tests for CLI helper functions.

These helpers consolidate repeated patterns across CLI commands.
"""
import os
import pytest
import psycopg
from datetime import date
from g2.cli_helpers import (
    parse_comma_separated,
    upsert_feature_function,
    setup_progress_reporter,
    validate_date_range,
    db_connection,
    init_schema_tables,
)
from g2.config import load_settings
from g2.db import schema


def get_db_url():
    """Get database URL from environment or settings."""
    settings = load_settings()
    return os.environ.get("DATABASE_URL", settings.database_url)


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
    url = get_db_url()
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


class TestSetupProgressReporter:
    """Test progress reporter setup helper."""

    def test_setup_basic_reporter(self):
        """Test basic progress reporter setup."""
        reporter, live = setup_progress_reporter(
            total=100,
            progress=True,
            json_output=False,
            mode="api"
        )

        assert reporter.total == 100
        assert reporter.mode == "api"
        assert reporter.enabled is True
        # Live context is created when progress=True and json_output=False
        assert live is not None

    def test_setup_with_json_output_no_live(self):
        """Test that JSON output suppresses live display."""
        reporter, live = setup_progress_reporter(
            total=50,
            progress=True,
            json_output=True,
            mode="local"
        )

        assert reporter.total == 50
        assert reporter.mode == "local"
        # Live should be None when json_output=True
        assert live is None

    def test_setup_with_progress_disabled(self):
        """Test that disabled progress suppresses live display."""
        reporter, live = setup_progress_reporter(
            total=50,
            progress=False,
            json_output=False,
            mode="api"
        )

        assert reporter.total == 50
        # Live should be None when progress=False
        assert live is None

    def test_setup_with_kwargs(self):
        """Test that additional kwargs are set as attributes if they exist."""
        reporter, live = setup_progress_reporter(
            total=200,
            progress=False,
            json_output=False,
            mode="dispatcher",
            # These attributes exist on ProgressReporter
            enabled=True
        )

        assert reporter.mode == "dispatcher"
        # The helper only sets attributes that already exist on the reporter
        # Test that enabled was set
        assert reporter.enabled is True


class TestValidateDateRange:
    """Test date range validation helper."""

    def test_validate_both_dates_provided(self):
        """Test validation with both before and after dates."""
        before, after = validate_date_range(
            before="2024-01-15",
            after="2024-01-01"
        )

        assert before == date(2024, 1, 15)
        assert after == date(2024, 1, 1)

    def test_validate_only_before(self):
        """Test validation with only before date."""
        before, after = validate_date_range(
            before="2024-01-15",
            after=None
        )

        assert before == date(2024, 1, 15)
        assert after is None

    def test_validate_only_after(self):
        """Test validation with only after date."""
        before, after = validate_date_range(
            before=None,
            after="2024-01-01"
        )

        assert before is None
        assert after == date(2024, 1, 1)

    def test_validate_both_none_allowed(self):
        """Test that both None is allowed when allow_both_missing=True."""
        before, after = validate_date_range(
            before=None,
            after=None,
            allow_both_missing=True
        )

        assert before is None
        assert after is None

    def test_validate_both_none_not_allowed(self):
        """Test that both None raises error when allow_both_missing=False."""
        with pytest.raises(ValueError, match="At least one date required"):
            validate_date_range(
                before=None,
                after=None,
                allow_both_missing=False
            )

    def test_validate_invalid_date_format(self):
        """Test that invalid date format raises error."""
        with pytest.raises(ValueError, match="Invalid date format"):
            validate_date_range(
                before="not-a-date",
                after=None
            )

    def test_validate_empty_string_treated_as_none(self):
        """Test that empty strings are treated as None."""
        before, after = validate_date_range(
            before="",
            after="",
            allow_both_missing=True
        )

        assert before is None
        assert after is None


class TestDBConnection:
    """Test database connection helper."""

    def test_connection_with_default_url(self):
        """Test connection using default DATABASE_URL."""
        with db_connection(None) as conn:
            assert conn is not None
            assert not conn.closed
            # Verify autocommit is on by default
            assert conn.autocommit is True

    def test_connection_with_custom_url(self):
        """Test connection with custom URL."""
        url = get_db_url()
        with db_connection(url) as conn:
            assert conn is not None
            assert not conn.closed

    def test_connection_closes_on_exit(self):
        """Test that connection closes when exiting context."""
        url = get_db_url()
        with db_connection(url) as conn:
            assert not conn.closed
        # Connection should be closed after exiting context
        assert conn.closed

    def test_connection_with_autocommit_disabled(self):
        """Test connection with autocommit disabled."""
        url = get_db_url()
        with db_connection(url, autocommit=False) as conn:
            assert conn.autocommit is False


class TestInitSchemaTables:
    """Test schema initialization helper."""

    def test_init_single_table(self):
        """Test initializing a single table."""
        url = get_db_url()
        with db_connection(url) as conn:
            init_schema_tables(conn, ["stocks"])
            # Verify stocks table exists
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'stocks'
                    )
                """)
                exists = cur.fetchone()[0]
                assert exists is True

    def test_init_multiple_tables(self):
        """Test initializing multiple tables."""
        url = get_db_url()
        with db_connection(url) as conn:
            init_schema_tables(conn, ["stocks", "feature_functions"])
            # Verify both tables exist
            with conn.cursor() as cur:
                for table in ["stocks", "feature_functions"]:
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = %s
                        )
                    """, (table,))
                    exists = cur.fetchone()[0]
                    assert exists is True, f"Table {table} should exist"

    def test_init_with_empty_list(self):
        """Test that empty list doesn't cause errors."""
        url = get_db_url()
        with db_connection(url) as conn:
            # Should not raise any errors
            init_schema_tables(conn, [])

    def test_init_with_unknown_table_raises_error(self):
        """Test that unknown table name raises appropriate error."""
        url = get_db_url()
        with db_connection(url) as conn:
            with pytest.raises(ValueError, match="Unknown table"):
                init_schema_tables(conn, ["nonexistent_table_xyz"])

    def test_init_ml_tables_calls_creators(self, monkeypatch):
        """Test that ML table names are supported by init_schema_tables()."""
        from g2.db import schema as schema_mod

        called: list[str] = []

        monkeypatch.setattr(schema_mod, "create_ml_datasets_table", lambda conn: called.append("ml_datasets"))
        monkeypatch.setattr(schema_mod, "create_ml_runs_table", lambda conn: called.append("ml_runs"))
        monkeypatch.setattr(schema_mod, "create_ml_models_table", lambda conn: called.append("ml_models"))
        monkeypatch.setattr(schema_mod, "create_quantile_predictions_table", lambda conn: called.append("quantile_predictions"))
        monkeypatch.setattr(schema_mod, "create_prediction_outcomes_table", lambda conn: called.append("prediction_outcomes"))
        monkeypatch.setattr(schema_mod, "create_model_performance_table", lambda conn: called.append("model_performance"))
        monkeypatch.setattr(schema_mod, "create_trend_class_predictions_table", lambda conn: called.append("trend_class_predictions"))

        init_schema_tables(object(), [
            "ml_datasets",
            "ml_runs",
            "ml_models",
            "quantile_predictions",
            "prediction_outcomes",
            "model_performance",
            "trend_class_predictions",
        ])

        assert called == [
            "ml_datasets",
            "ml_runs",
            "ml_models",
            "quantile_predictions",
            "prediction_outcomes",
            "model_performance",
            "trend_class_predictions",
        ]
