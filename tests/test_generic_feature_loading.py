"""
TDD tests for generic feature definition loading from JSON files.

Tests the generic approach where ALL feature types (indicators, derivatives, etc.)
are loaded from JSON files without special-case handling.
"""
import json
import os
from pathlib import Path
import pytest
import psycopg
from g2.db import schema
from g2.db.ingest import load_feature_definitions_from_json, ensure_feature_definitions


def test_load_feature_definitions_from_json_single_file():
    """Test loading a single feature definition from JSON file."""
    # Create temporary JSON file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "name": "test_feature_rsi_14",
            "function_name": "indicator",
            "params": {"indicator": "rsi", "period": 14},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }, f)
        temp_path = f.name

    try:
        # Load definitions from file
        defs = load_feature_definitions_from_json(temp_path)

        assert len(defs) == 1
        assert defs[0]["name"] == "test_feature_rsi_14"
        assert defs[0]["function_name"] == "indicator"
        assert defs[0]["params"]["indicator"] == "rsi"
    finally:
        os.unlink(temp_path)


def test_load_feature_definitions_from_json_directory():
    """Test loading multiple feature definitions from a directory."""
    import tempfile
    import shutil

    # Create temporary directory with multiple JSON files
    temp_dir = tempfile.mkdtemp()
    try:
        # Create multiple feature definition files
        feature1 = {
            "name": "indicator_rsi_14",
            "function_name": "indicator",
            "params": {"indicator": "rsi"},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }

        feature2 = {
            "name": "derivative_rsi_14_slope_5",
            "function_name": "derivative",
            "params": {"source_feature": "indicator_rsi_14", "window": 5},
            "source_table": "computed_features",
            "source_column": "value",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }

        with open(Path(temp_dir) / "rsi.json", "w") as f:
            json.dump(feature1, f)

        with open(Path(temp_dir) / "rsi_derivative.json", "w") as f:
            json.dump(feature2, f)

        # Load definitions from directory
        defs = load_feature_definitions_from_json(temp_dir)

        assert len(defs) == 2
        names = {d["name"] for d in defs}
        assert "indicator_rsi_14" in names
        assert "derivative_rsi_14_slope_5" in names
    finally:
        shutil.rmtree(temp_dir)


def test_load_feature_definitions_validates_required_fields():
    """Test that loading validates required fields are present."""
    import tempfile

    # Missing required field 'name'
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "function_name": "indicator",
            "params": {"indicator": "rsi"}
        }, f)
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="Missing required field"):
            load_feature_definitions_from_json(temp_path)
    finally:
        os.unlink(temp_path)


def test_load_feature_definitions_rejects_malformed_json():
    """Test that malformed JSON is rejected with clear error."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{invalid json")
        temp_path = f.name

    try:
        with pytest.raises(json.JSONDecodeError):
            load_feature_definitions_from_json(temp_path)
    finally:
        os.unlink(temp_path)


def test_load_feature_definitions_ignores_non_json_files():
    """Test that non-JSON files in directory are ignored."""
    import tempfile
    import shutil

    temp_dir = tempfile.mkdtemp()
    try:
        # Create a JSON file and a non-JSON file
        with open(Path(temp_dir) / "feature.json", "w") as f:
            json.dump({
                "name": "test_feature",
                "function_name": "indicator",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True
            }, f)

        # Create non-JSON file
        with open(Path(temp_dir) / "README.md", "w") as f:
            f.write("# Features\n")

        defs = load_feature_definitions_from_json(temp_dir)

        # Should only load the JSON file
        assert len(defs) == 1
        assert defs[0]["name"] == "test_feature"
    finally:
        shutil.rmtree(temp_dir)


@pytest.fixture
def db_conn():
    """Create test database connection."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            # Clean schema
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_ensure_feature_definitions_from_json_inserts_to_db(db_conn):
    """Test that JSON definitions can be inserted into database."""
    import tempfile

    schema.create_feature_definitions_table(db_conn)

    # Create JSON definition
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({
            "name": "indicator_rsi_14",
            "function_name": "indicator",
            "params": {"indicator": "rsi"},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }, f)
        temp_path = f.name

    try:
        # Load and insert
        defs = load_feature_definitions_from_json(temp_path)
        result = ensure_feature_definitions(db_conn, defs)

        # Verify insertion
        assert "indicator_rsi_14" in result
        assert isinstance(result["indicator_rsi_14"], int)

        # Verify in database
        with db_conn.cursor() as cur:
            cur.execute("SELECT name, function_name FROM feature_definitions WHERE name = %s",
                       ("indicator_rsi_14",))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "indicator_rsi_14"
            assert row[1] == "indicator"
    finally:
        os.unlink(temp_path)
