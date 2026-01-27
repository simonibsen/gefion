"""
Shared test fixtures and helpers.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, List

import pytest

from g2.cli_helpers import upsert_feature_function


# Global to store backup path across session hooks
_test_backup_path: Optional[str] = None


def pytest_sessionstart(session):
    """
    Create a full database backup before test session starts.

    Only runs if ENABLE_DB_TESTS=1 (i.e., database tests are enabled).
    This ensures we can restore the exact database state after tests complete.
    """
    if os.getenv("ENABLE_DB_TESTS") != "1":
        return

    global _test_backup_path

    print("\nBacking up database before tests...")

    # Create temp directory for backup
    _test_backup_path = tempfile.mkdtemp(prefix="g2_test_backup_")

    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    # Full backup using g2 backup command
    result = subprocess.run(
        [sys.executable, "-m", "g2.cli", "backup", "-o", _test_backup_path, "--data-types", "all"],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode == 0:
        print(f"  Backup created at: {_test_backup_path}")
    else:
        print(f"  Warning: Backup failed: {result.stderr}")
        _test_backup_path = None


def pytest_sessionfinish(session, exitstatus):
    """
    Restore database from backup after test session completes.

    This ensures the database returns to the exact state it was in before tests ran.
    Only runs if ENABLE_DB_TESTS=1 (i.e., database tests were enabled).
    """
    global _test_backup_path

    if os.getenv("ENABLE_DB_TESTS") != "1":
        return

    # Only restore if tests actually ran (not just collected)
    if session.testscollected == 0:
        return

    env = os.environ.copy()
    env["OTEL_ENABLED"] = "false"

    # If we have a backup, restore from it
    if _test_backup_path and Path(_test_backup_path).exists():
        print("\n\nRestoring database from backup...")

        result = subprocess.run(
            [sys.executable, "-m", "g2.cli", "restore", "-i", _test_backup_path, "--mode", "replace"],
            capture_output=True,
            text=True,
            env=env,
        )

        if result.returncode == 0:
            print(f"  Database restored successfully")
        else:
            print(f"  Warning: Restore failed: {result.stderr}")
            # Fall back to re-importing feature definitions/functions
            _restore_features_fallback(env)

        # Always run db-init after restore to recreate any tables dropped by tests
        # and re-seed built-in data (strategies, etc.)
        _ensure_tables_exist(env)

        # Clean up backup directory
        try:
            shutil.rmtree(_test_backup_path)
        except Exception:
            pass
    else:
        # Fallback: just restore feature definitions/functions
        print("\n\nRestoring feature definitions and functions after tests...")
        _restore_features_fallback(env)
        _ensure_tables_exist(env)


def _restore_features_fallback(env):
    """Fallback restoration of just feature definitions and functions."""
    # Re-import feature definitions
    result = subprocess.run(
        [sys.executable, "-m", "g2.cli", "feat-def-import"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        print(f"  {result.stdout.strip()}")

    # Re-import feature functions
    result = subprocess.run(
        [sys.executable, "-m", "g2.cli", "feat-fx-import"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        print(f"  {result.stdout.strip()}")


def _ensure_tables_exist(env):
    """Run db-init to recreate any tables dropped by tests and seed built-in data."""
    result = subprocess.run(
        [sys.executable, "-m", "g2.cli", "db-init"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        print(f"  Tables and seed data restored")
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
