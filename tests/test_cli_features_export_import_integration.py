"""
Integration tests for the improved features-fx-export and features-fx-import CLI commands.

Requires ENABLE_DB_TESTS=1 to run.
"""
import json
import os
import psycopg
import pytest
from pathlib import Path
from typer.testing import CliRunner
from g2 import cli
from g2.cli import _upsert_feature_function
from g2.config import load_settings
from g2.db import schema


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL from environment or settings."""
    settings = load_settings()
    return os.environ.get("DATABASE_URL", settings.database_url)


runner = CliRunner()


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        # Ensure table exists before cleanup
        schema.create_feature_functions_table(conn)
        # Clean up before tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_functions
                WHERE name LIKE 'cli_test_%'
            """)
        yield conn
        # Clean up after tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_functions
                WHERE name LIKE 'cli_test_%'
            """)


@pytest.fixture
def setup_test_data(db_conn):
    """Insert test functions into database."""
    schema.create_feature_functions_table(db_conn)

    test_functions = [
        {
            "name": "cli_test_func1",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs):\n    return [{'value': 1}]",
            "description": "Test function 1",
            "status": "active",
            "enabled": True,
            "created_by": "test",
        },
        {
            "name": "cli_test_func2",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs):\n    return [{'value': 2}]",
            "description": "Test function 2",
            "status": "active",
            "enabled": True,
            "created_by": "test",
        },
    ]

    for func in test_functions:
        _upsert_feature_function(db_conn, func)

    return test_functions


def test_export_with_default_directory(setup_test_data, tmp_path, monkeypatch):
    """Test that features-fx-export uses default 'feature-functions' directory."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-fx-export"])

    assert result.exit_code == 0
    assert "Exported" in result.stdout

    # Check default directory was created
    default_dir = tmp_path / "feature-functions"
    assert default_dir.exists()

    # Check files exist
    assert (default_dir / "cli_test_func1_v1.0.json").exists()
    assert (default_dir / "cli_test_func2_v1.0.json").exists()


def test_export_with_custom_directory(setup_test_data, tmp_path):
    """Test that features-fx-export works with custom directory."""
    export_dir = tmp_path / "custom-export"

    result = runner.invoke(cli.app, ["feat-fx-export", "--dir", str(export_dir)])

    assert result.exit_code == 0
    assert "Exported" in result.stdout
    assert export_dir.exists()
    assert (export_dir / "cli_test_func1_v1.0.json").exists()


def test_export_filtered_functions(setup_test_data, tmp_path, monkeypatch):
    """Test exporting specific functions only."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-fx-export", "--functions", "cli_test_func1"])

    assert result.exit_code == 0
    assert "Exported 1" in result.stdout

    default_dir = tmp_path / "feature-functions"
    assert (default_dir / "cli_test_func1_v1.0.json").exists()
    assert not (default_dir / "cli_test_func2_v1.0.json").exists()


def test_import_with_default_directory(db_conn, tmp_path, monkeypatch):
    """Test that features-import uses default 'feature-functions' directory."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_functions_table(db_conn)

    # Create default directory with test files
    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir()

    test_func = {
        "name": "cli_test_imported",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "cli_test_imported_v1.0.json").write_text(json.dumps(test_func, indent=2))

    result = runner.invoke(cli.app, ["feat-fx-import"])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout

    # Verify in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions WHERE name = %s", ("cli_test_imported",))
        assert cur.fetchone() is not None


def test_import_with_custom_directory(db_conn, tmp_path):
    """Test features-import with custom directory."""
    schema.create_feature_functions_table(db_conn)

    import_dir = tmp_path / "custom-import"
    import_dir.mkdir()

    test_func = {
        "name": "cli_test_custom_imported",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (import_dir / "cli_test_custom_imported_v1.0.json").write_text(json.dumps(test_func, indent=2))

    result = runner.invoke(cli.app, ["feat-fx-import", "--dir", str(import_dir)])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout


def test_import_filtered_functions(db_conn, tmp_path, monkeypatch):
    """Test importing specific functions only."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_functions_table(db_conn)

    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir()

    func1 = {
        "name": "cli_test_import1",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    func2 = {
        "name": "cli_test_import2",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "cli_test_import1_v1.0.json").write_text(json.dumps(func1, indent=2))
    (func_dir / "cli_test_import2_v1.0.json").write_text(json.dumps(func2, indent=2))

    # Import only func1
    result = runner.invoke(cli.app, ["feat-fx-import", "--functions", "cli_test_import1"])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout

    # Verify only func1 is in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions WHERE name LIKE 'cli_test_import%' ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cli_test_import1"


def test_import_is_idempotent_via_cli(db_conn, tmp_path, monkeypatch):
    """Test that running import twice doesn't cause errors."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_functions_table(db_conn)

    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir()

    test_func = {
        "name": "cli_test_idempotent",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "cli_test_idempotent_v1.0.json").write_text(json.dumps(test_func, indent=2))

    # First import
    result1 = runner.invoke(cli.app, ["feat-fx-import"])
    assert result1.exit_code == 0
    assert "Imported 1" in result1.stdout

    # Second import - should succeed
    result2 = runner.invoke(cli.app, ["feat-fx-import"])
    assert result2.exit_code == 0
    assert "Imported 1" in result2.stdout

    # Should still only have 1 function
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_functions WHERE name = %s", ("cli_test_idempotent",))
        count = cur.fetchone()[0]
        assert count == 1


def test_export_import_roundtrip(setup_test_data, db_conn, tmp_path, monkeypatch):
    """Test that exporting and then importing preserves all data."""
    monkeypatch.chdir(tmp_path)

    # Export only test functions
    result_export = runner.invoke(cli.app, ["feat-fx-export", "--functions", "cli_test_func1,cli_test_func2"])
    assert result_export.exit_code == 0

    # Clear database
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'cli_test_%'")

    # Verify it's empty
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_functions WHERE name LIKE 'cli_test_%'")
        assert cur.fetchone()[0] == 0

    # Import
    result_import = runner.invoke(cli.app, ["feat-fx-import"])
    assert result_import.exit_code == 0
    assert "Imported 2" in result_import.stdout

    # Verify data is back
    with db_conn.cursor() as cur:
        cur.execute("SELECT name, version, description FROM feature_functions WHERE name LIKE 'cli_test_%' ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "cli_test_func1"
        assert rows[0][1] == "1.0"
        assert rows[0][2] == "Test function 1"
        assert rows[1][0] == "cli_test_func2"


def test_import_from_nonexistent_directory(tmp_path, monkeypatch):
    """Test import gracefully handles nonexistent directory."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-fx-import", "--dir", "nonexistent"])

    assert result.exit_code == 0
    assert "No functions found" in result.stdout
