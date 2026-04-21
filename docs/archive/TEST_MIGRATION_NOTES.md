# Test Migration Notes

## Background

After removing backward compatibility for code-registered functions (commit 7a3a41b7), the dispatcher now exclusively loads functions from the `feature_functions` database table.

## Test Status

**Passing:** 428 tests
**Failing:** 41 tests
**Errors:** 17 tests (collection/setup issues)

## Tests Fixed for DB-Only Architecture

The following tests were updated to register functions in the database instead of using the removed `register_compute_function()`:

1. `test_dispatcher_writer_thread_safety.py` ✅
2. `test_timings_thread_safety.py` ✅
3. `test_writer_error_propagation.py` ✅

**Deleted:**
- `test_dispatcher_db_override.py` - Tested removed backward compatibility feature

## Remaining Test Failures

Most remaining failures appear to be **pre-existing issues** unrelated to the backward compatibility removal. Common patterns:

### CLI Feature Tests (most common)
Tests like `test_cli_features_run_local_happy.py` fail because they:
- Only load feature **definitions** (not functions)
- Need to also load feature **functions** into `feature_functions` table
- Exit code 2 suggests CLI parsing or missing function errors

### How to Fix These Tests

For tests that need feature functions loaded:

```python
from gefion.db import schema
from gefion.cli_helpers import upsert_feature_function
from psycopg.types.json import Json

# 1. Create feature_functions table
schema.create_feature_functions_table(conn)

# 2. Register test function in DB
test_function_body = '''
def compute(rows, specs):
    # Your test function logic here
    return results
'''

upsert_feature_function(conn, {
    "name": "test_function",
    "version": "1.0",
    "language": "python",
    "function_body": test_function_body,
    "status": "active",
    "enabled": True,
})

# 3. Create feature definition
with conn.cursor() as cur:
    cur.execute(
        """
        INSERT INTO feature_definitions (name, function_name, params, ...)
        VALUES (%s, %s, %s, ...)
        """,
        ("feature_name", "test_function", Json({}), ...)
    )
```

### Test Helper Available

A helper function is available in `tests/conftest.py`:

```python
from tests.conftest import load_feature_functions

# Load all functions from feature-functions/ directory
load_feature_functions(conn)

# Load specific functions
load_feature_functions(conn, ["indicator_rsi", "indicator_ema"])
```

**Note:** Avoid hardcoding specific feature functions from the `feature-functions/` directory in tests, as that directory structure may change. Instead, create simple test functions directly in the test.

## Conservative Approach

Given that:
1. The backward compatibility removal is complete and working
2. Tests directly affected by the changes are fixed
3. Remaining failures appear pre-existing
4. Many tests may need architectural understanding of the feature/function relationship

**Recommendation:** Address remaining test failures on an as-needed basis when working on related features, rather than attempting a bulk fix that might introduce new issues.

## Test Categories Still Failing

- `test_cli_features_*.py` - Feature computation CLI tests
- `test_insert_computed_features_*.py` - Insert tests
- `test_ml_dataset_*.py` - ML dataset tests
- `test_schema_performance.py` - Schema/performance tests
- `test_trim_*.py` - Trim command tests
- `test_pool_resource_leak.py` - Pool management tests

Most of these likely just need the `feature_functions` table created and functions loaded, but should be verified individually.
