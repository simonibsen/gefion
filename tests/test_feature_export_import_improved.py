"""
Tests for improved feature import/export functionality.

Requirements:
1. Export defaults to 'feature-functions/' directory
2. One file per feature function (named <function_name>_<version>.json)
3. Export all by default, or filter to specific functions
4. Import defaults to 'feature-functions/' directory
5. Import all JSON files or specific ones
6. Imports are idempotent
"""
import json
import os
import pytest
import psycopg
from pathlib import Path
from g2.cli import (
    _export_feature_functions,
    _export_feature_definitions,
    _upsert_feature_function,
)
from g2.db import schema


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
                WHERE name LIKE 'test_%'
                   OR name LIKE 'imported_%'
                   OR name LIKE 'func%'
                   OR name LIKE 'valid_%'
            """)
        yield conn
        # Clean up after tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_functions
                WHERE name LIKE 'test_%'
                   OR name LIKE 'imported_%'
                   OR name LIKE 'func%'
                   OR name LIKE 'valid_%'
            """)


@pytest.fixture
def setup_test_functions(db_conn):
    """Create test feature functions in the database."""
    schema.create_feature_functions_table(db_conn)
    schema.create_feature_definitions_table(db_conn)

    # Insert test functions
    test_functions = [
        {
            "name": "test_indicator_1",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs):\n    return [{'date': r['date'], 'value': 1} for r in rows]",
            "status": "active",
            "enabled": True,
            "created_by": "test",
        },
        {
            "name": "test_indicator_2",
            "version": "1.0",
            "language": "python",
            "function_body": "def compute(rows, specs):\n    return [{'date': r['date'], 'value': 2} for r in rows]",
            "status": "active",
            "enabled": True,
            "created_by": "test",
        },
        {
            "name": "test_indicator_1",
            "version": "2.0",
            "language": "python",
            "function_body": "def compute(rows, specs):\n    return [{'date': r['date'], 'value': 1.5} for r in rows]",
            "status": "active",
            "enabled": True,
            "created_by": "test",
        },
    ]

    for func in test_functions:
        _upsert_feature_function(db_conn, func)

    db_conn.commit()
    return test_functions


def test_export_to_individual_files(db_conn, setup_test_functions, tmp_path):
    """Test exporting functions to individual JSON files."""
    from g2.cli import export_functions_to_directory

    export_dir = tmp_path / "feature-functions"

    # Export all functions
    exported_count = export_functions_to_directory(db_conn, export_dir)

    # Should export 3 functions (2 unique names × versions)
    assert exported_count == 3

    # Check individual files exist
    assert (export_dir / "test_indicator_1_v1.0.json").exists()
    assert (export_dir / "test_indicator_1_v2.0.json").exists()
    assert (export_dir / "test_indicator_2_v1.0.json").exists()

    # Verify file contents
    func1 = json.loads((export_dir / "test_indicator_1_v1.0.json").read_text())
    assert func1["name"] == "test_indicator_1"
    assert func1["version"] == "1.0"
    assert func1["language"] == "python"
    assert "def compute" in func1["function_body"]


def test_export_filtered_functions(db_conn, setup_test_functions, tmp_path):
    """Test exporting only specific functions."""
    from g2.cli import export_functions_to_directory

    export_dir = tmp_path / "feature-functions"

    # Export only test_indicator_1
    exported_count = export_functions_to_directory(
        db_conn, export_dir, function_names=["test_indicator_1"]
    )

    # Should export 2 versions of test_indicator_1
    assert exported_count == 2

    # Check correct files exist
    assert (export_dir / "test_indicator_1_v1.0.json").exists()
    assert (export_dir / "test_indicator_1_v2.0.json").exists()
    assert not (export_dir / "test_indicator_2_v1.0.json").exists()


def test_import_from_directory(db_conn, tmp_path):
    """Test importing all functions from a directory."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    # Create test files
    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir(parents=True)

    test_func = {
        "name": "imported_func",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs):\n    return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "imported_func_v1.0.json").write_text(json.dumps(test_func, indent=2))

    # Import all functions
    imported_count = import_functions_from_directory(db_conn, func_dir)

    assert imported_count == 1

    # Verify it's in the database
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT name, version FROM feature_functions WHERE name = %s",
            ("imported_func",)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "imported_func"
        assert row[1] == "1.0"


def test_import_specific_functions(db_conn, tmp_path):
    """Test importing only specific functions from a directory."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    # Create test files
    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir(parents=True)

    func1 = {
        "name": "func1",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    func2 = {
        "name": "func2",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "func1_v1.0.json").write_text(json.dumps(func1, indent=2))
    (func_dir / "func2_v1.0.json").write_text(json.dumps(func2, indent=2))

    # Import only func1
    imported_count = import_functions_from_directory(
        db_conn, func_dir, function_names=["func1"]
    )

    assert imported_count == 1

    # Verify only func1 is imported
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "func1"


def test_import_is_idempotent(db_conn, tmp_path):
    """Test that importing the same function twice doesn't cause errors."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir(parents=True)

    test_func = {
        "name": "test_func",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "test_func_v1.0.json").write_text(json.dumps(test_func, indent=2))

    # Import once
    count1 = import_functions_from_directory(db_conn, func_dir)
    assert count1 == 1

    # Import again - should be idempotent
    count2 = import_functions_from_directory(db_conn, func_dir)
    assert count2 == 1

    # Should still only have 1 function in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_functions WHERE name = %s", ("test_func",))
        count = cur.fetchone()[0]
        assert count == 1


def test_import_updates_existing_function(db_conn, tmp_path):
    """Test that importing updates an existing function (same name+version)."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir(parents=True)

    # Import original version
    original_func = {
        "name": "test_func",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "description": "Original description",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "test_func_v1.0.json").write_text(json.dumps(original_func, indent=2))
    import_functions_from_directory(db_conn, func_dir)

    # Update the file
    updated_func = {
        "name": "test_func",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return [{'value': 42}]",
        "description": "Updated description",
        "status": "active",
        "enabled": True,
    }

    (func_dir / "test_func_v1.0.json").write_text(json.dumps(updated_func, indent=2))

    # Import again
    import_functions_from_directory(db_conn, func_dir)

    # Verify it was updated
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT description, function_body FROM feature_functions WHERE name = %s AND version = %s",
            ("test_func", "1.0")
        )
        row = cur.fetchone()
        assert row[0] == "Updated description"
        assert "'value': 42" in row[1]


def test_export_creates_directory_if_not_exists(db_conn, setup_test_functions, tmp_path):
    """Test that export creates the directory if it doesn't exist."""
    from g2.cli import export_functions_to_directory

    export_dir = tmp_path / "nonexistent" / "feature-functions"

    # Should not raise error
    exported_count = export_functions_to_directory(db_conn, export_dir)

    assert exported_count == 3
    assert export_dir.exists()
    assert export_dir.is_dir()


def test_import_from_empty_directory(db_conn, tmp_path):
    """Test that importing from empty directory returns 0."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    imported_count = import_functions_from_directory(db_conn, empty_dir)

    assert imported_count == 0


def test_import_ignores_non_json_files(db_conn, tmp_path):
    """Test that import ignores non-JSON files in the directory."""
    from g2.cli import import_functions_from_directory

    schema.create_feature_functions_table(db_conn)

    func_dir = tmp_path / "feature-functions"
    func_dir.mkdir()

    # Create a valid JSON file
    valid_func = {
        "name": "valid_func",
        "version": "1.0",
        "language": "python",
        "function_body": "def compute(rows, specs): return []",
        "status": "active",
        "enabled": True,
    }
    (func_dir / "valid_func_v1.0.json").write_text(json.dumps(valid_func, indent=2))

    # Create non-JSON files that should be ignored
    (func_dir / "README.md").write_text("# Feature Functions")
    (func_dir / "config.yaml").write_text("foo: bar")
    (func_dir / ".gitignore").write_text("*.pyc")

    imported_count = import_functions_from_directory(db_conn, func_dir)

    # Should only import the one valid JSON file
    assert imported_count == 1
