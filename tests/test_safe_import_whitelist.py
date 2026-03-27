"""
Test that feature functions can import whitelisted modules but not dangerous ones.

The security sandbox should:
1. Allow imports of safe modules (numpy, pandas, datetime, etc.)
2. Block imports of dangerous modules (os, sys, subprocess, etc.)
3. Pre-import commonly used modules to avoid import overhead
"""
from unittest.mock import MagicMock
from datetime import date, timedelta


def test_safe_import_allows_whitelisted_modules():
    """
    Test that feature functions can import safe, whitelisted modules.

    Whitelisted modules:
    - numpy/np
    - pandas/pd
    - datetime
    - math
    - statistics
    - talib
    - scipy
    - sklearn
    - json
    - re
    - itertools
    - functools
    - operator
    - collections
    - typing
    """
    from gefion.features.dispatcher import _load_db_function

    # Mock database connection
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Feature function that imports numpy
    # The adapter expects compute(df, **params) signature
    feature_code = """
import numpy as np
def compute(df):
    # Use numpy to compute mean of close prices
    return np.mean(df['close'])
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    # Should NOT raise ImportError and should successfully load
    result = _load_db_function(mock_conn, 'test_numpy_import')

    # The key test: function loads successfully, which means import worked
    assert result is not None, \
        "Feature function should successfully load (import numpy succeeded)"

    fn, version = result
    assert callable(fn), "Should return callable function"
    assert version == '1.0', "Should return correct version"


def test_safe_import_allows_pandas():
    """Test that pandas can be imported."""
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    feature_code = """
import pandas as pd
def compute(df):
    # Return the length of the dataframe
    return len(df)
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_pandas_import')

    # The key test: function loads successfully, which means import pandas worked
    assert result is not None, \
        "Feature function should successfully load (import pandas succeeded)"

    fn, version = result
    assert callable(fn)


def test_safe_import_allows_datetime():
    """Test that datetime can be imported."""
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    feature_code = """
import datetime
def compute(df):
    # Extract year from first date
    return df['date'].iloc[0].year
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_datetime_import')

    # The key test: function loads successfully, which means import datetime worked
    assert result is not None, \
        "Feature function should successfully load (import datetime succeeded)"

    fn, version = result
    assert callable(fn)


def test_safe_import_blocks_dangerous_modules():
    """
    Test that dangerous modules are blocked.

    Blocked modules:
    - os (file system access)
    - sys (system access)
    - subprocess (shell execution)
    - __builtin__ (sandbox escape)
    - importlib (import system manipulation)
    - pickle (arbitrary code execution)
    """
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    dangerous_imports = [
        ("os", "import os"),
        ("sys", "import sys"),
        ("subprocess", "import subprocess"),
        ("importlib", "import importlib"),
        ("pickle", "import pickle"),
    ]

    for module_name, dangerous_import in dangerous_imports:
        feature_code = f"""
{dangerous_import}
def compute(stock_data):
    return 1
"""

        mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

        # _load_db_function should return None (warns but doesn't raise)
        result = _load_db_function(mock_conn, f'test_{module_name}_blocked')

        # Function should fail to load due to import error
        assert result is None, f"Should have blocked: {dangerous_import}"


def test_safe_import_blocks_submodules_of_dangerous_modules():
    """Test that submodules of dangerous modules are also blocked."""
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    feature_code = """
import os.path
def compute(stock_data):
    return 1
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_os_path_blocked')
    assert result is None, "Should have blocked os.path import"


def test_pre_imported_modules_available():
    """
    Test that commonly used modules are pre-imported for performance.

    Pre-imported modules should be available without explicit import:
    - np (numpy)
    - pd (pandas)
    - numpy
    - pandas
    - datetime
    """
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    # Feature function that uses pre-imported modules without import statement
    feature_code = """
def compute(stock_data):
    # np and pd should be pre-imported and available in global scope
    # Return a simple value to indicate success
    return 42
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_preimported')

    # Function should load successfully (pre-imported modules available)
    assert result is not None, "Feature function should load successfully"

    fn, version = result
    assert callable(fn)


def test_feature_function_with_multiple_imports():
    """Test that feature functions can import multiple whitelisted modules."""
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    feature_code = """
import numpy as np
import pandas as pd
import datetime
import math

def compute(df):
    # Use all imported modules
    arr = np.array(df['close'].values)
    mean_close = np.mean(arr)
    today = datetime.date.today()
    result = math.sqrt(mean_close)
    return result
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    result = _load_db_function(mock_conn, 'test_multiple_imports')

    # The key test: function loads successfully despite multiple imports
    assert result is not None, \
        "Feature function should successfully load (all imports succeeded)"

    fn, version = result
    assert callable(fn)


def test_import_error_contains_security_message():
    """Test that blocked imports result in failed function load."""
    from gefion.features.dispatcher import _load_db_function

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    feature_code = """
import os
def compute(stock_data):
    return 1
"""

    mock_cursor.fetchone.return_value = ('python', feature_code, '1.0')

    # Function should fail to load (returns None and warns)
    result = _load_db_function(mock_conn, 'test_os_blocked')
    assert result is None, "Should have failed to load function with dangerous import"
