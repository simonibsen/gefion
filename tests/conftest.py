"""
Shared test fixtures and helpers.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

import psycopg
import pytest

from g2.cli_helpers import upsert_feature_function
from g2.db.schema import test_db_url


def pytest_sessionstart(session):
    """
    Ensure the test database exists before tests run.

    Only runs if ENABLE_DB_TESTS=1 (i.e., database tests are enabled).
    Creates the g2_test database if it doesn't exist and runs db-init to
    set up schema, extensions, and seed data.
    """
    if os.getenv("ENABLE_DB_TESTS") != "1":
        return

    url = test_db_url()
    parsed = urlparse(url)
    test_db_name = parsed.path.lstrip("/")

    # Connect to the maintenance database to check/create the test DB
    maint_url = url.replace(f"/{test_db_name}", "/postgres")
    # Strip query params for maintenance connection
    if "?" in maint_url:
        maint_url = maint_url.split("?")[0]

    try:
        with psycopg.connect(maint_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (test_db_name,),
                )
                exists = cur.fetchone() is not None

            if not exists:
                print(f"\nCreating test database '{test_db_name}'...")
                with conn.cursor() as cur:
                    cur.execute(
                        psycopg.sql.SQL("CREATE DATABASE {}").format(
                            psycopg.sql.Identifier(test_db_name)
                        )
                    )
                print(f"  Test database '{test_db_name}' created")
    except psycopg.OperationalError as e:
        print(f"\n  Warning: Could not connect to maintenance DB: {e}")
        return

    # Run db-init against the test database to set up schema + seeds
    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"
    env["DATABASE_URL"] = url

    print(f"\nInitializing test database '{test_db_name}'...")
    result = subprocess.run(
        [sys.executable, "-m", "g2.cli", "db-init"],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode == 0:
        print(f"  Test database ready: {test_db_name}")
    else:
        print(f"  Warning: db-init failed: {result.stderr}")


def create_test_function(conn, name: str = "test_func", returns_value: str = "close") -> None:
    """
    Create a simple test function in the database.

    Args:
        conn: Database connection
        name: Function name (default: "test_func")
        returns_value: What value to return as feature (default: "close")

    Example:
        create_test_function(conn, "my_test_func", "volume")
    """
    function_body = f'''
import pandas as pd

def compute(rows, specs):
    """Simple test function."""
    if not rows:
        return []
    df = pd.DataFrame(rows)
    result = []
    for _, row in df.iterrows():
        result.append({{
            'date': row['date'],
            'value': float(row.get('{returns_value}', 0))
        }})
    return result
'''
    upsert_feature_function(conn, {
        "name": name,
        "version": "1.0",
        "language": "python",
        "function_body": function_body,
        "status": "active",
        "enabled": True,
    })


def load_feature_function_from_json(conn, json_path: str) -> None:
    """
    Load a feature function from a JSON file into the feature_functions table.

    Args:
        conn: Database connection
        json_path: Path to JSON file relative to project root

    Example:
        load_feature_function_from_json(conn, "feature-functions/indicator_rsi.json")
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Feature function file not found: {json_path}")

    payload = json.loads(path.read_text())
    upsert_feature_function(conn, payload)


def load_feature_functions(conn, function_names: Optional[List[str]] = None) -> int:
    """
    Load feature functions from the feature-functions directory.

    Args:
        conn: Database connection
        function_names: Optional list of function names to load (loads all if None)

    Returns:
        Number of functions loaded

    Example:
        # Load all functions
        load_feature_functions(conn)

        # Load specific functions
        load_feature_functions(conn, ["indicator_rsi", "indicator_adx"])
    """
    from g2.cli import import_functions_from_directory
    from pathlib import Path

    feature_functions_dir = Path("feature-functions")
    if not feature_functions_dir.exists():
        raise FileNotFoundError(f"Feature functions directory not found: {feature_functions_dir}")

    return import_functions_from_directory(conn, feature_functions_dir, function_names)
