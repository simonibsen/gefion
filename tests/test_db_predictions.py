"""Tests for the unified predictions table helper module."""
import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from gefion.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    """Non-destructive fixture: cleans only test-inserted prediction rows, never drops tables."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    connection = create_connection()
    connection.autocommit = True

    # Ensure prerequisite tables exist (idempotent CREATE IF NOT EXISTS)
    with connection.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            pass
    schema.create_stocks_table(connection)
    schema.create_ml_datasets_table(connection)
    schema.create_ml_runs_table(connection)
    schema.create_ml_models_table(connection)

    yield connection

    # Cleanup: remove only test data we inserted (identifiable by test model name)
    with connection.cursor() as cur:
        # predictions table may not exist if create_predictions_table hasn't been called
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'predictions'"
        )
        if cur.fetchone():
            cur.execute(
                "DELETE FROM predictions WHERE model_id IN "
                "(SELECT id FROM ml_models WHERE name = 'test_pred_model')"
            )
        cur.execute("DELETE FROM ml_models WHERE name = 'test_pred_model'")
        cur.execute("DELETE FROM stocks WHERE symbol IN ('AAPL_PRED_TEST', 'MSFT_PRED_TEST')")

    connection.close()


@pytest.fixture
def db_with_prereqs(conn):
    """Set up predictions table + test stock and model with distinctive names."""
    schema.create_predictions_table(conn)

    # Use distinctive names that won't collide with real data
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol) VALUES ('AAPL_PRED_TEST') "
            "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol "
            "RETURNING id"
        )
        stock_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO ml_models (name, version, artifact_uri, active) "
            "VALUES ('test_pred_model', 'v1', '/tmp/test_pred_model', true) "
            "ON CONFLICT (name, version) DO UPDATE SET active = true "
            "RETURNING id"
        )
        model_id = cur.fetchone()[0]

    return conn, stock_id, model_id


# ---------------------------------------------------------------------------
# Table creation tests
# ---------------------------------------------------------------------------

def test_predictions_table_exists(conn):
    """create_predictions_table creates the predictions table."""
    schema.create_predictions_table(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'predictions'"
        )
        assert cur.fetchone() is not None


def test_predictions_table_is_hypertable(conn):
    """predictions table is a TimescaleDB hypertable."""
    schema.create_predictions_table(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'predictions'"
        )
        assert cur.fetchone() is not None


def test_predictions_table_has_indexes(conn):
    """predictions table has the expected indexes."""
    schema.create_predictions_table(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'predictions'"
        )
        index_names = {row[0] for row in cur.fetchall()}
        assert "predictions_symbol_date_idx" in index_names
        assert "predictions_type_idx" in index_names


# ---------------------------------------------------------------------------
# Insert / upsert tests
# ---------------------------------------------------------------------------

def test_insert_quantile_prediction(db_with_prereqs):
    """insert_quantile_prediction stores values as JSONB and reads back correctly."""
    from gefion.db.predictions import insert_quantile_prediction, query_predictions

    conn, stock_id, model_id = db_with_prereqs
    with conn.cursor() as cur:
        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=date(2026, 3, 27), horizon_days=5,
            q10=-0.02, q50=0.01, q90=0.04, model_version="v1",
        )

    with conn.cursor() as cur:
        rows = query_predictions(cur, prediction_type="quantile", model_id=model_id)

    assert len(rows) == 1
    row = rows[0]
    assert row["prediction_type"] == "quantile"
    assert float(row["q10"]) == pytest.approx(-0.02, abs=1e-4)
    assert float(row["q50"]) == pytest.approx(0.01, abs=1e-4)
    assert float(row["q90"]) == pytest.approx(0.04, abs=1e-4)
    assert row["model_version"] == "v1"


def test_insert_trend_prediction(db_with_prereqs):
    """insert_trend_prediction stores class probabilities as JSONB."""
    from gefion.db.predictions import insert_trend_prediction, query_predictions

    conn, stock_id, model_id = db_with_prereqs
    class_probs = {
        "p_strong_up": 0.1, "p_weak_up": 0.25, "p_neutral": 0.3,
        "p_weak_down": 0.2, "p_strong_down": 0.15,
    }
    with conn.cursor() as cur:
        insert_trend_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=date(2026, 3, 27), horizon_days=5,
            predicted_class="neutral", class_probs=class_probs,
            entropy=1.58, margin=0.05,
        )

    with conn.cursor() as cur:
        rows = query_predictions(cur, prediction_type="trend_class", model_id=model_id)

    assert len(rows) == 1
    row = rows[0]
    assert row["prediction_type"] == "trend_class"
    assert row["predicted_class"] == "neutral"
    assert float(row["p_neutral"]) == pytest.approx(0.3, abs=1e-4)
    assert float(row["entropy"]) == pytest.approx(1.58, abs=1e-2)
    assert float(row["margin"]) == pytest.approx(0.05, abs=1e-4)


def test_upsert_prediction_on_conflict(db_with_prereqs):
    """Second insert with same PK updates the prediction values."""
    from gefion.db.predictions import insert_quantile_prediction, query_predictions

    conn, stock_id, model_id = db_with_prereqs
    pred_date = date(2026, 3, 27)

    with conn.cursor() as cur:
        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=pred_date, horizon_days=5,
            q10=-0.02, q50=0.01, q90=0.04,
        )

    # Upsert with new values
    with conn.cursor() as cur:
        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=pred_date, horizon_days=5,
            q10=-0.03, q50=0.02, q90=0.05,
        )

    with conn.cursor() as cur:
        rows = query_predictions(cur, prediction_type="quantile", model_id=model_id)

    assert len(rows) == 1
    assert float(rows[0]["q50"]) == pytest.approx(0.02, abs=1e-4)


def test_both_types_coexist(db_with_prereqs):
    """Quantile and trend_class predictions coexist for same model/stock/date/horizon."""
    from gefion.db.predictions import (
        insert_quantile_prediction, insert_trend_prediction, query_predictions,
    )

    conn, stock_id, model_id = db_with_prereqs
    pred_date = date(2026, 3, 27)

    with conn.cursor() as cur:
        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=pred_date, horizon_days=5,
            q10=-0.02, q50=0.01, q90=0.04,
        )
        insert_trend_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=pred_date, horizon_days=5,
            predicted_class="weak_up",
            class_probs={"p_strong_up": 0.1, "p_weak_up": 0.4, "p_neutral": 0.2,
                         "p_weak_down": 0.2, "p_strong_down": 0.1},
            entropy=1.5, margin=0.2,
        )

    with conn.cursor() as cur:
        all_rows = query_predictions(cur, model_id=model_id)
        q_rows = query_predictions(cur, prediction_type="quantile", model_id=model_id)
        t_rows = query_predictions(cur, prediction_type="trend_class", model_id=model_id)

    assert len(all_rows) == 2
    assert len(q_rows) == 1
    assert len(t_rows) == 1


def test_invalid_prediction_type_rejected(db_with_prereqs):
    """Inserting with an invalid prediction_type raises an error."""
    from gefion.db.predictions import insert_prediction

    conn, stock_id, model_id = db_with_prereqs
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_prediction(
                cur, model_id=model_id, data_id=stock_id,
                prediction_date=date(2026, 3, 27), horizon_days=5,
                prediction_type="invalid_type",
                values_dict={"foo": "bar"},
            )
            # Force flush to trigger constraint
            cur.connection.commit()


def test_query_predictions_filter_by_date(db_with_prereqs):
    """query_predictions filters by date range."""
    from gefion.db.predictions import insert_quantile_prediction, query_predictions

    conn, stock_id, model_id = db_with_prereqs

    with conn.cursor() as cur:
        for day in [25, 26, 27, 28]:
            insert_quantile_prediction(
                cur, model_id=model_id, data_id=stock_id,
                prediction_date=date(2026, 3, day), horizon_days=5,
                q10=-0.01, q50=0.01, q90=0.03,
            )

    with conn.cursor() as cur:
        rows = query_predictions(
            cur, prediction_type="quantile",
            date_from=date(2026, 3, 26), date_to=date(2026, 3, 27),
        )

    assert len(rows) == 2
    dates = {row["prediction_date"] for row in rows}
    assert dates == {date(2026, 3, 26), date(2026, 3, 27)}


def test_query_predictions_filter_by_data_id(db_with_prereqs):
    """query_predictions filters by data_id (stock)."""
    from gefion.db.predictions import insert_quantile_prediction, query_predictions

    conn, stock_id, model_id = db_with_prereqs

    # Insert a second stock
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stocks (symbol) VALUES ('MSFT_PRED_TEST') RETURNING id"
        )
        stock_id_2 = cur.fetchone()[0]

        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id,
            prediction_date=date(2026, 3, 27), horizon_days=5,
            q10=-0.01, q50=0.01, q90=0.03,
        )
        insert_quantile_prediction(
            cur, model_id=model_id, data_id=stock_id_2,
            prediction_date=date(2026, 3, 27), horizon_days=5,
            q10=-0.02, q50=0.02, q90=0.04,
        )

    with conn.cursor() as cur:
        rows = query_predictions(cur, prediction_type="quantile", data_id=stock_id)

    assert len(rows) == 1
    assert rows[0]["data_id"] == stock_id
