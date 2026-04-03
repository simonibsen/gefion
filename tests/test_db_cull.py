"""Tests for cascading data cull functionality."""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema
from gefion.db.predictions import insert_quantile_prediction


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    """Non-destructive fixture using test-namespaced data."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    connection = create_connection()
    connection.autocommit = True

    with connection.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            pass

    # Ensure tables exist
    schema.create_stocks_table(connection)
    schema.create_stock_ohlcv_table(connection)
    schema.create_feature_definitions_table(connection)
    schema.create_computed_features_table(connection)
    schema.create_ml_datasets_table(connection)
    schema.create_ml_runs_table(connection)
    schema.create_ml_models_table(connection)
    schema.create_predictions_table(connection)
    schema.create_prediction_outcomes_table(connection)
    schema.create_model_performance_table(connection)

    yield connection

    # Cleanup test data
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM predictions WHERE model_id IN "
            "(SELECT id FROM ml_models WHERE name LIKE 'cull_test_%')"
        )
        cur.execute(
            "DELETE FROM prediction_outcomes WHERE data_id IN "
            "(SELECT id FROM stocks WHERE symbol LIKE 'CULL_TEST_%')"
        )
        cur.execute(
            "DELETE FROM model_performance WHERE model_id IN "
            "(SELECT id FROM ml_models WHERE name LIKE 'cull_test_%')"
        )
        cur.execute("DELETE FROM ml_models WHERE name LIKE 'cull_test_%'")
        cur.execute("DELETE FROM ml_runs WHERE run_type = 'cull_test'")
        cur.execute(
            "DELETE FROM computed_features WHERE data_id IN "
            "(SELECT id FROM stocks WHERE symbol LIKE 'CULL_TEST_%')"
        )
        cur.execute(
            "DELETE FROM stock_ohlcv WHERE data_id IN "
            "(SELECT id FROM stocks WHERE symbol LIKE 'CULL_TEST_%')"
        )
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'CULL_TEST_%'")

    connection.close()


def _seed_cull_data(conn, before_count: int = 3, after_count: int = 2):
    """Seed test data spanning dates before and after a cull boundary.

    Creates stock CULL_TEST_A with:
    - `before_count` OHLCV rows + computed features + predictions before 2026-01-01
    - `after_count` rows after 2026-01-01
    Returns (stock_id, model_id, cull_date).
    """
    cull_date = date(2026, 1, 1)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol) VALUES ('CULL_TEST_A') "
            "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol RETURNING id"
        )
        stock_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO ml_models (name, version, artifact_uri, active) "
            "VALUES ('cull_test_model', 'v1', '/tmp/cull_test', true) "
            "ON CONFLICT (name, version) DO UPDATE SET active = true RETURNING id"
        )
        model_id = cur.fetchone()[0]

        # Insert OHLCV data
        for i in range(before_count):
            d = date(2025, 12, 20 + i)
            cur.execute(
                "INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume) "
                "VALUES (%s, %s, 100, 105, 95, 102, 1000000) ON CONFLICT DO NOTHING",
                (stock_id, d),
            )
            # Insert a prediction for each date
            insert_quantile_prediction(
                cur, model_id=model_id, data_id=stock_id,
                prediction_date=d, horizon_days=5,
                q10=-0.02, q50=0.01, q90=0.04,
            )

        for i in range(after_count):
            d = date(2026, 1, 5 + i)
            cur.execute(
                "INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume) "
                "VALUES (%s, %s, 100, 105, 95, 102, 1000000) ON CONFLICT DO NOTHING",
                (stock_id, d),
            )
            insert_quantile_prediction(
                cur, model_id=model_id, data_id=stock_id,
                prediction_date=d, horizon_days=5,
                q10=-0.01, q50=0.02, q90=0.05,
            )

    return stock_id, model_id, cull_date


# ---------------------------------------------------------------------------
# plan_cull tests
# ---------------------------------------------------------------------------

def test_plan_cull_returns_counts(conn):
    """plan_cull returns a dict of table -> row count to delete."""
    from gefion.db.cull import plan_cull

    stock_id, model_id, cull_date = _seed_cull_data(conn, before_count=3, after_count=2)
    plan = plan_cull(conn, before_date=cull_date)

    assert isinstance(plan, dict)
    # Should report 3 predictions to delete (those before cull_date)
    assert plan.get("predictions", 0) == 3
    # Should report 3 OHLCV rows to delete
    assert plan.get("stock_ohlcv", 0) == 3


def test_plan_cull_empty_when_no_old_data(conn):
    """plan_cull returns zero counts when all data is after the cull date."""
    from gefion.db.cull import plan_cull

    _seed_cull_data(conn, before_count=0, after_count=2)
    plan = plan_cull(conn, before_date=date(2025, 1, 1))

    total = sum(plan.values())
    assert total == 0


def test_plan_cull_with_symbols_filter(conn):
    """plan_cull respects symbols filter."""
    from gefion.db.cull import plan_cull

    _seed_cull_data(conn, before_count=3, after_count=2)
    # Filter by non-existent symbol
    plan = plan_cull(conn, before_date=date(2026, 1, 1), symbols=["NONEXISTENT"])
    assert plan.get("stock_ohlcv", 0) == 0

    # Filter by actual test symbol
    plan = plan_cull(conn, before_date=date(2026, 1, 1), symbols=["CULL_TEST_A"])
    assert plan.get("stock_ohlcv", 0) == 3


# ---------------------------------------------------------------------------
# execute_cull tests
# ---------------------------------------------------------------------------

def test_execute_cull_deletes_in_order(conn):
    """execute_cull deletes rows and returns counts."""
    from gefion.db.cull import execute_cull

    stock_id, model_id, cull_date = _seed_cull_data(conn, before_count=3, after_count=2)
    result = execute_cull(conn, before_date=cull_date)

    assert result["predictions"] == 3
    assert result["stock_ohlcv"] == 3

    # Verify remaining data is intact
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM predictions WHERE model_id = %s", (model_id,))
        assert cur.fetchone()[0] == 2  # after_count predictions remain

        cur.execute("SELECT COUNT(*) FROM stock_ohlcv WHERE data_id = %s", (stock_id,))
        assert cur.fetchone()[0] == 2  # after_count OHLCV rows remain


def test_execute_cull_with_symbols(conn):
    """execute_cull respects symbols filter."""
    from gefion.db.cull import execute_cull

    stock_id, model_id, cull_date = _seed_cull_data(conn, before_count=3, after_count=2)

    # Add a second stock with data — should not be affected
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol) VALUES ('CULL_TEST_B') "
            "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol RETURNING id"
        )
        stock_id_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume) "
            "VALUES (%s, %s, 100, 105, 95, 102, 1000000) ON CONFLICT DO NOTHING",
            (stock_id_b, date(2025, 12, 25)),
        )

    result = execute_cull(conn, before_date=cull_date, symbols=["CULL_TEST_A"])

    # CULL_TEST_B's data should remain
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stock_ohlcv WHERE data_id = %s", (stock_id_b,))
        assert cur.fetchone()[0] == 1


def test_execute_cull_is_idempotent(conn):
    """Running execute_cull twice produces zero deletes the second time."""
    from gefion.db.cull import execute_cull

    _seed_cull_data(conn, before_count=3, after_count=2)
    cull_date = date(2026, 1, 1)

    first = execute_cull(conn, before_date=cull_date)
    assert first["predictions"] == 3

    second = execute_cull(conn, before_date=cull_date)
    assert second.get("predictions", 0) == 0
    assert second.get("stock_ohlcv", 0) == 0


def test_cull_order_models_before_runs():
    """ml_models must be deleted before ml_runs (train_run_id FK)."""
    from gefion.db.cull import CULL_ORDER
    table_order = [t[0] for t in CULL_ORDER]
    models_idx = table_order.index("ml_models")
    runs_idx = table_order.index("ml_runs")
    assert models_idx < runs_idx, (
        f"ml_models (idx {models_idx}) must come before ml_runs (idx {runs_idx}) "
        "because ml_models.train_run_id references ml_runs(id)"
    )


def test_execute_cull_accepts_on_progress_callback():
    """execute_cull should accept an on_progress callback for per-table status updates."""
    import inspect
    sig = inspect.signature(__import__('gefion.db.cull', fromlist=['execute_cull']).execute_cull)
    assert 'on_progress' in sig.parameters, (
        "execute_cull must accept an on_progress callback parameter"
    )


def test_vacuum_after_cull_targets_affected_tables():
    """vacuum_after_cull must VACUUM ANALYZE each affected table individually,
    not a global VACUUM ANALYZE (which doesn't reliably update hypertable chunk stats)."""
    from gefion.db.cull import vacuum_after_cull
    import inspect

    src = inspect.getsource(vacuum_after_cull)
    # Must iterate over affected tables and vacuum each one
    assert "VACUUM ANALYZE" in src, "vacuum_after_cull must run VACUUM ANALYZE"
    # Must accept a dict of affected tables (the result from execute_cull)
    sig = inspect.signature(vacuum_after_cull)
    assert "affected_tables" in sig.parameters, (
        "vacuum_after_cull must accept affected_tables parameter (dict from execute_cull)"
    )


def test_vacuum_after_cull_skips_when_no_tables():
    """vacuum_after_cull should be a no-op when no tables were affected."""
    from gefion.db.cull import vacuum_after_cull
    from unittest.mock import MagicMock

    conn = MagicMock()
    # Empty dict = nothing deleted
    vacuum_after_cull(conn, affected_tables={})
    # Should not have executed any SQL
    conn.cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_cli_cull_uses_per_table_vacuum():
    """CLI data cull must call vacuum_after_cull with per-table targeting."""
    import inspect
    from gefion import cli
    src = inspect.getsource(cli.data_cull)
    assert "vacuum_after_cull" in src, (
        "CLI data_cull must use vacuum_after_cull for per-table vacuum "
        "(not a global VACUUM ANALYZE)"
    )


def test_orphan_detection_respects_train_run_id():
    """ml_runs must not be considered orphaned if ml_models.train_run_id references them."""
    from gefion.db.cull import _count_orphaned, _delete_orphaned

    # Verify the SQL in both functions checks ml_models.train_run_id
    import inspect
    count_src = inspect.getsource(_count_orphaned)
    delete_src = inspect.getsource(_delete_orphaned)

    assert "train_run_id" in count_src, (
        "_count_orphaned for ml_runs must check ml_models.train_run_id references"
    )
    assert "train_run_id" in delete_src, (
        "_delete_orphaned for ml_runs must check ml_models.train_run_id references"
    )
