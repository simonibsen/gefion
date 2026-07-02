import os
import psycopg
import pytest

from gefion.db import schema
from gefion.db.ingest import insert_computed_features, ensure_feature_definitions, ensure_store_targets


@pytest.fixture(scope="module", autouse=True)
def _restore_db_after_module():
    """Restore canonical test DB state after this module's destructive cleanup (issue #29)."""
    yield
    from conftest import restore_test_db
    restore_test_db()


DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def require_db():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture(autouse=True)
def clean_db():
    conn = require_db()
    conn.autocommit = True
    from conftest import reset_public_schema
    reset_public_schema(conn)
    conn.close()
    yield


def test_insert_computed_features_bad_data_id_skips():
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    defs = [
        {
            "name": "indicator_adx_14",
            "function_name": "indicator",
            "params": {"indicator": "adx"},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True,
        }
    ]
    fid_map = ensure_feature_definitions(conn, defs)
    ensure_store_targets(conn, defs)
    fid = fid_map["indicator_adx_14"]
    # data_id not convertible -> expect skip/zero inserted, not a DB error
    rows = [{"date": "2025-01-01", "adx_14": 10.0}]
    inserted = insert_computed_features(conn, data_id="bad", rows=rows, feature_map={"adx_14": fid})
    assert inserted == 0
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM computed_features;")
        cnt = cur.fetchone()[0]
    assert cnt == 0
    conn.close()
