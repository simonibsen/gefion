"""
Test that exec() of database-stored code is properly sandboxed.

The bug: exec(body, {}, local_env) allows unrestricted access to built-ins,
enabling file I/O, imports, and other dangerous operations.

The fix: Use a restricted globals environment that blocks dangerous built-ins.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
from datetime import date
from unittest.mock import Mock

import psycopg
import pytest
from psycopg.types.json import Json

from g2.config import load_settings
from g2.db import schema
from g2.db.ingest import upsert_stock
from g2.features.dispatcher import _load_db_function


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL from environment or settings."""
    settings = load_settings()
    return os.environ.get("DATABASE_URL", settings.database_url)


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def setup_db(db_conn):
    """Set up test database schema."""
    schema.create_stocks_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    schema.create_feature_functions_table(db_conn)

    yield

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'test_security_%'")


def test_exec_blocks_file_access(db_conn, setup_db):
    """
    Test that exec() blocks file system access operations.

    Malicious code should not be able to read/write files.
    """
    # Create a malicious function that tries to read a file
    malicious_code = """
def compute(rows, specs):
    # Try to read /etc/passwd or similar
    try:
        with open('/etc/passwd', 'r') as f:
            data = f.read()
        return [{"date": row["date"], "value": len(data)} for row in rows]
    except:
        return [{"date": row["date"], "value": -1} for row in rows]
"""

    # Insert the malicious function into the database
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions (name, language, function_body, enabled, status, version)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("test_security_file_access", "python", malicious_code, True, "active", "1.0")
        )

    # Try to load and execute the function
    # With the bug, this would succeed and read the file
    # With the fix, it should fail or return None
    result = _load_db_function(db_conn, "test_security_file_access")

    if result:
        fn, version = result
        # Even if loaded, calling it should not allow file access
        # This tests runtime security
        mock_rows = [{"date": date(2025, 1, 1), "close": 100.0}]
        mock_specs = [{"feature_id": 1, "feature_name": "test", "params": {}}]

        # The function should either:
        # 1. Fail to load (result is None)
        # 2. Raise an exception when trying file access
        # 3. Return -1 indicating access was denied
        try:
            output = fn(mock_rows, mock_specs)
            # If it executed, verify it didn't actually read the file
            # The malicious code returns len(data) if successful, -1 if blocked
            assert output[0].get("value") == -1, \
                "File access should have been blocked (expected -1)"
        except (NameError, AttributeError):
            # Expected: 'open' is not defined or similar
            pass
    # If result is None, the function failed to load - that's acceptable too


def test_exec_blocks_imports(db_conn, setup_db):
    """
    Test that exec() blocks dangerous imports.

    Malicious code should not be able to import arbitrary modules.
    """
    # Create a malicious function that tries to import os and execute commands
    malicious_code = """
def compute(rows, specs):
    try:
        import os
        # Try to execute a command
        result = os.system('echo test')
        return [{"date": row["date"], "value": result} for row in rows]
    except:
        return [{"date": row["date"], "value": -1} for row in rows]
"""

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions (name, language, function_body, enabled, status, version)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("test_security_imports", "python", malicious_code, True, "active", "1.0")
        )

    result = _load_db_function(db_conn, "test_security_imports")

    if result:
        fn, version = result
        mock_rows = [{"date": date(2025, 1, 1), "close": 100.0}]
        mock_specs = [{"feature_id": 1, "feature_name": "test", "params": {}}]

        try:
            output = fn(mock_rows, mock_specs)
            # Should return -1 indicating import was blocked
            assert output[0].get("value") == -1, \
                "Import should have been blocked"
        except (ImportError, NameError):
            # Expected: import is blocked
            pass


def test_exec_blocks_eval(db_conn, setup_db):
    """
    Test that exec() blocks eval() and exec() within user code.

    Nested eval/exec can be used for sandbox escapes.
    """
    malicious_code = """
def compute(rows, specs):
    try:
        # Try to use eval to bypass restrictions
        result = eval("1 + 1")
        return [{"date": row["date"], "value": result} for row in rows]
    except:
        return [{"date": row["date"], "value": -1} for row in rows]
"""

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions (name, language, function_body, enabled, status, version)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("test_security_eval", "python", malicious_code, True, "active", "1.0")
        )

    result = _load_db_function(db_conn, "test_security_eval")

    if result:
        fn, version = result
        mock_rows = [{"date": date(2025, 1, 1), "close": 100.0}]
        mock_specs = [{"feature_id": 1, "feature_name": "test", "params": {}}]

        try:
            output = fn(mock_rows, mock_specs)
            # Should return -1 indicating eval was blocked
            assert output[0].get("value") == -1, \
                "eval() should have been blocked"
        except NameError:
            # Expected: 'eval' is not defined
            pass


def test_exec_allows_safe_operations(db_conn, setup_db):
    """
    Test that safe operations are still allowed in the sandboxed environment.

    Normal feature computation using math, pandas-like operations should work.
    """
    safe_code = """
def compute(rows, specs):
    # Safe operations: basic math, list comprehensions
    return [{"date": row["date"], "value": float(row["close"]) * 2} for row in rows]
"""

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions (name, language, function_body, enabled, status, version)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ("test_security_safe", "python", safe_code, True, "active", "1.0")
        )

    result = _load_db_function(db_conn, "test_security_safe")

    # Should successfully load
    assert result is not None, "Safe code should load successfully"

    fn, version = result
    mock_rows = [{"date": date(2025, 1, 1), "close": 100.0}]
    mock_specs = [{"feature_id": 1, "feature_name": "test", "params": {}}]

    # Should execute without errors
    output = fn(mock_rows, mock_specs)
    assert len(output) == 1
    assert output[0]["value"] == 200.0
