"""
Tests for chart query functions.

These are DB integration tests that require ENABLE_DB_TESTS=1.
"""

import os
from datetime import date, timedelta

import psycopg
import pytest

from gefion.db import schema


DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


class TestFetchPredictionsUsesUnifiedTable:
    """Test that fetch_predictions_for_chart uses the unified predictions table."""

    def test_query_references_predictions_table(self):
        """SQL should reference 'predictions' table, not 'quantile_predictions'."""
        import inspect
        from gefion.charts.queries import fetch_predictions_for_chart

        source = inspect.getsource(fetch_predictions_for_chart)
        assert "FROM predictions " in source or "FROM predictions\n" in source
        assert "quantile_predictions" not in source

    def test_query_extracts_jsonb_fields(self):
        """SQL should extract q10/q50/q90 from prediction_values JSONB."""
        import inspect
        from gefion.charts.queries import fetch_predictions_for_chart

        source = inspect.getsource(fetch_predictions_for_chart)
        assert "prediction_values" in source

    def test_query_filters_by_prediction_type(self):
        """SQL should filter by prediction_type = 'quantile'."""
        import inspect
        from gefion.charts.queries import fetch_predictions_for_chart

        source = inspect.getsource(fetch_predictions_for_chart)
        assert "prediction_type" in source


def require_db():
    """Get DB connection or skip test."""
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture
def conn():
    """Provide database connection for tests."""
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    connection = require_db()
    connection.autocommit = True
    yield connection
    connection.close()


def _ensure_stocks_table_with_full_schema(conn):
    """Ensure stocks table exists with all columns needed.

    Other tests may create minimal stocks tables without sector/industry.
    This ensures the full schema is in place.
    """
    with conn.cursor() as cur:
        # Check if table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'stocks'
            );
        """)
        table_exists = cur.fetchone()[0]

        if table_exists:
            # Add missing columns if needed (other tests may create minimal schema)
            for col, coltype in [
                ("status", "TEXT"),
                ("name", "TEXT"),
                ("sector", "TEXT"),
                ("industry", "TEXT"),
                ("updated_at", "TIMESTAMP"),
            ]:
                cur.execute(f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'stocks' AND column_name = '{col}'
                        ) THEN
                            ALTER TABLE stocks ADD COLUMN {col} {coltype};
                        END IF;
                    END $$;
                """)
    conn.commit()


@pytest.fixture
def sample_ohlcv_data(conn):
    """Insert sample OHLCV data for testing using existing schema."""
    # Ensure required tables exist with full schema
    _ensure_stocks_table_with_full_schema(conn)
    schema.create_stocks_table(conn)

    symbol = "CHARTTEST"
    with conn.cursor() as cur:
        # Insert test stock (or get existing id)
        cur.execute(
            """
            INSERT INTO stocks (symbol, status, name)
            VALUES (%s, 'active', 'Chart Test Stock')
            ON CONFLICT (symbol) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (symbol,),
        )
        data_id = cur.fetchone()[0]

        # Clear existing test data
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id = %s", (data_id,))

        # Insert 30 days of test data
        base_date = date.today() - timedelta(days=40)
        for i in range(30):
            d = base_date + timedelta(days=i)
            # Skip weekends
            if d.weekday() >= 5:
                continue
            cur.execute(
                """
                INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (data_id, date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high,
                    low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume
                """,
                (
                    data_id,
                    d,
                    100.0 + i,
                    102.0 + i,
                    99.0 + i,
                    101.0 + i,
                    1000000 + i * 10000,
                ),
            )

    yield symbol

    # Cleanup
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id = %s", (data_id,))
        cur.execute("DELETE FROM stocks WHERE symbol = %s", (symbol,))


class TestFetchOhlcvForChart:
    """Tests for fetch_ohlcv_for_chart function."""

    def test_fetch_ohlcv_returns_expected_structure(self, conn, sample_ohlcv_data):
        """fetch_ohlcv_for_chart should return list of dicts with OHLCV fields."""
        from gefion.charts.queries import fetch_ohlcv_for_chart

        data = fetch_ohlcv_for_chart(conn, sample_ohlcv_data)

        assert isinstance(data, list)
        assert len(data) > 0

        # Check first row has expected keys
        row = data[0]
        assert "date" in row
        assert "open" in row
        assert "high" in row
        assert "low" in row
        assert "close" in row
        assert "volume" in row

    def test_fetch_ohlcv_with_date_range(self, conn, sample_ohlcv_data):
        """fetch_ohlcv_for_chart should filter by date range."""
        from gefion.charts.queries import fetch_ohlcv_for_chart

        start = date.today() - timedelta(days=30)
        end = date.today() - timedelta(days=20)

        data = fetch_ohlcv_for_chart(conn, sample_ohlcv_data, start_date=start, end_date=end)

        assert isinstance(data, list)
        for row in data:
            assert row["date"] >= start
            assert row["date"] <= end

    def test_fetch_ohlcv_ordered_by_date(self, conn, sample_ohlcv_data):
        """fetch_ohlcv_for_chart should return data ordered by date ascending."""
        from gefion.charts.queries import fetch_ohlcv_for_chart

        data = fetch_ohlcv_for_chart(conn, sample_ohlcv_data)

        dates = [row["date"] for row in data]
        assert dates == sorted(dates)

    def test_fetch_ohlcv_nonexistent_symbol_returns_empty(self, conn):
        """fetch_ohlcv_for_chart should return empty list for unknown symbol."""
        from gefion.charts.queries import fetch_ohlcv_for_chart

        data = fetch_ohlcv_for_chart(conn, "NONEXISTENT_SYMBOL_XYZ")

        assert data == []


class TestFetchPredictionsForChart:
    """Tests for fetch_predictions_for_chart function."""

    @pytest.fixture
    def sample_predictions(self, conn, sample_ohlcv_data):
        """Insert sample prediction data."""
        symbol = sample_ohlcv_data
        model_name = "chart_test_model"

        # Ensure required tables exist (in dependency order)
        schema.create_ml_datasets_table(conn)
        schema.create_ml_runs_table(conn)
        schema.create_ml_models_table(conn)
        schema.create_predictions_table(conn)

        with conn.cursor() as cur:
            # Get stock id
            cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
            data_id = cur.fetchone()[0]

            # Create a test model (or get existing id)
            cur.execute(
                """
                INSERT INTO ml_models (name, version, artifact_uri)
                VALUES (%s, 'v1', '/tmp/test')
                ON CONFLICT (name, version) DO UPDATE SET artifact_uri = EXCLUDED.artifact_uri
                RETURNING id
                """,
                (model_name,),
            )
            model_id = cur.fetchone()[0]

            # Insert test predictions using unified predictions table
            cur.execute(
                "DELETE FROM predictions WHERE model_id = %s AND data_id = %s",
                (model_id, data_id),
            )
            base_date = date.today() - timedelta(days=10)
            for i in range(10):
                d = base_date + timedelta(days=i)
                from psycopg.types.json import Json
                cur.execute(
                    """
                    INSERT INTO predictions
                        (model_id, data_id, prediction_date, horizon_days,
                         prediction_type, prediction_values)
                    VALUES (%s, %s, %s, %s, 'quantile', %s)
                    ON CONFLICT (model_id, data_id, prediction_date, horizon_days, prediction_type)
                    DO UPDATE SET prediction_values = EXCLUDED.prediction_values
                    """,
                    (model_id, data_id, d, 7,
                     Json({"q10": 95.0 + i, "q50": 100.0 + i, "q90": 105.0 + i})),
                )

        yield model_name

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM predictions WHERE model_id = %s AND data_id = %s",
                (model_id, data_id),
            )
            cur.execute("DELETE FROM ml_models WHERE name = %s", (model_name,))

    def test_fetch_predictions_returns_expected_structure(
        self, conn, sample_ohlcv_data, sample_predictions
    ):
        """fetch_predictions_for_chart should return list of dicts with q10/q50/q90."""
        from gefion.charts.queries import fetch_predictions_for_chart

        data = fetch_predictions_for_chart(conn, sample_ohlcv_data, sample_predictions)

        assert isinstance(data, list)
        assert len(data) > 0

        row = data[0]
        assert "date" in row
        assert "q10" in row
        assert "q50" in row
        assert "q90" in row

    def test_fetch_predictions_filters_by_model(self, conn, sample_ohlcv_data, sample_predictions):
        """fetch_predictions_for_chart should filter by model_name."""
        from gefion.charts.queries import fetch_predictions_for_chart

        data = fetch_predictions_for_chart(conn, sample_ohlcv_data, "nonexistent_model")

        assert data == []

    def test_fetch_predictions_filters_by_horizon(self, conn, sample_ohlcv_data, sample_predictions):
        """fetch_predictions_for_chart should filter by horizon."""
        from gefion.charts.queries import fetch_predictions_for_chart

        # Test data uses horizon=7
        data_h7 = fetch_predictions_for_chart(conn, sample_ohlcv_data, sample_predictions, horizon=7)
        data_h30 = fetch_predictions_for_chart(conn, sample_ohlcv_data, sample_predictions, horizon=30)

        assert len(data_h7) > 0
        assert len(data_h30) == 0  # No data for horizon=30


class TestFetchFeaturesForChart:
    """Tests for fetch_features_for_chart function."""

    @pytest.fixture
    def sample_features(self, conn, sample_ohlcv_data):
        """Insert sample feature data."""
        # Ensure required tables exist
        schema.create_feature_definitions_table(conn)
        schema.create_computed_features_table(conn)

        symbol = sample_ohlcv_data
        feature_names = ["chart_test_rsi", "chart_test_macd"]

        with conn.cursor() as cur:
            # Get stock id
            cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
            data_id = cur.fetchone()[0]

            # Create test feature definitions
            feature_ids = []
            for fname in feature_names:
                cur.execute(
                    """
                    INSERT INTO feature_definitions (name, function_name, params, store_table, store_column, store_type, active)
                    VALUES (%s, 'test_func', '{}', 'computed_features', 'value', 'double precision', true)
                    ON CONFLICT (name) DO UPDATE SET function_name = EXCLUDED.function_name
                    RETURNING id
                    """,
                    (fname,),
                )
                feature_ids.append(cur.fetchone()[0])

            # Clear existing test features
            cur.execute(
                "DELETE FROM computed_features WHERE data_id = %s AND feature_id = ANY(%s)",
                (data_id, feature_ids),
            )

            # Insert test features
            base_date = date.today() - timedelta(days=30)
            for i in range(20):
                d = base_date + timedelta(days=i)
                if d.weekday() >= 5:
                    continue
                for idx, fid in enumerate(feature_ids):
                    cur.execute(
                        """
                        INSERT INTO computed_features (data_id, date, feature_id, value)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (feature_id, data_id, date) DO UPDATE SET value = EXCLUDED.value
                        """,
                        (data_id, d, fid, 50.0 + i + idx * 10),
                    )

        yield feature_names

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM computed_features WHERE data_id = %s AND feature_id = ANY(%s)",
                (data_id, feature_ids),
            )
            for fname in feature_names:
                cur.execute("DELETE FROM feature_definitions WHERE name = %s", (fname,))

    def test_fetch_features_returns_dict_structure(
        self, conn, sample_ohlcv_data, sample_features
    ):
        """fetch_features_for_chart should return dict mapping feature names to data."""
        from gefion.charts.queries import fetch_features_for_chart

        data = fetch_features_for_chart(conn, sample_ohlcv_data, sample_features)

        assert isinstance(data, dict)
        assert "chart_test_rsi" in data
        assert "chart_test_macd" in data

        # Check structure of each feature's data
        rsi_data = data["chart_test_rsi"]
        assert isinstance(rsi_data, list)
        assert len(rsi_data) > 0
        assert "date" in rsi_data[0]
        assert "value" in rsi_data[0]

    def test_fetch_features_with_date_range(self, conn, sample_ohlcv_data, sample_features):
        """fetch_features_for_chart should filter by date range."""
        from gefion.charts.queries import fetch_features_for_chart

        start = date.today() - timedelta(days=25)
        end = date.today() - timedelta(days=15)

        data = fetch_features_for_chart(
            conn, sample_ohlcv_data, sample_features, start_date=start, end_date=end
        )

        for feature_name, feature_data in data.items():
            for row in feature_data:
                assert row["date"] >= start
                assert row["date"] <= end

    def test_fetch_features_nonexistent_returns_empty(self, conn, sample_ohlcv_data):
        """fetch_features_for_chart should return empty lists for unknown features."""
        from gefion.charts.queries import fetch_features_for_chart

        data = fetch_features_for_chart(conn, sample_ohlcv_data, ["nonexistent_feature"])

        assert data == {"nonexistent_feature": []}


class TestFetchBacktestEquityCurve:
    """Tests for fetch_backtest_equity_curve function."""

    def test_fetch_backtest_nonexistent_returns_empty(self, conn):
        """fetch_backtest_equity_curve should return empty list for unknown backtest."""
        from gefion.charts.queries import fetch_backtest_equity_curve

        # When table doesn't exist or backtest not found, should return empty list
        data = fetch_backtest_equity_curve(conn, "nonexistent_backtest_id")

        assert data == []


class TestFetchExperimentTrialsForChart:
    """Tests for fetch_experiment_trials_for_chart function."""

    def test_query_references_trials_table(self):
        """SQL should read from experiment_trials."""
        import inspect
        from gefion.charts.queries import fetch_experiment_trials_for_chart

        source = inspect.getsource(fetch_experiment_trials_for_chart)
        assert "experiment_trials" in source

    @pytest.fixture
    def experiment_with_trials(self, conn):
        """Create an experiment with three trials; yield its id, then clean up."""
        from psycopg.types.json import Json

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiments (name, experiment_type, config, objective_direction)
                VALUES ('chart-test-exp', 'hyperparameter', %s, 'maximize')
                RETURNING id
                """,
                (Json({}),),
            )
            exp_id = cur.fetchone()[0]
            for n, score in ((1, 0.8), (2, 1.5), (3, 1.1)):
                cur.execute(
                    """
                    INSERT INTO experiment_trials (experiment_id, trial_number, params, metrics, score)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (exp_id, n, Json({"lr": 0.01 * n, "depth": n}), Json({}), score),
                )
        yield exp_id
        with conn.cursor() as cur:
            cur.execute("DELETE FROM experiments WHERE id = %s", (exp_id,))

    def test_returns_chart_ready_trials(self, conn, experiment_with_trials):
        """Rows should have trial/score/parameters/promoted keys."""
        from gefion.charts.queries import fetch_experiment_trials_for_chart

        data = fetch_experiment_trials_for_chart(conn, experiment_with_trials)

        assert len(data) == 3
        for row in data:
            assert set(row.keys()) >= {"trial", "score", "parameters", "promoted"}
            assert isinstance(row["score"], float)

    def test_best_trial_is_marked_promoted(self, conn, experiment_with_trials):
        """Only the best trial (per objective direction) should be promoted=True."""
        from gefion.charts.queries import fetch_experiment_trials_for_chart

        data = fetch_experiment_trials_for_chart(conn, experiment_with_trials)

        promoted = [row for row in data if row["promoted"]]
        assert len(promoted) == 1
        assert promoted[0]["trial"] == 2  # score 1.5 is the max

    def test_unknown_experiment_returns_empty(self, conn):
        from gefion.charts.queries import fetch_experiment_trials_for_chart

        assert fetch_experiment_trials_for_chart(conn, -1) == []


class TestFetchCycleFdrForChart:
    """Tests for fetch_cycle_fdr_for_chart function."""

    def test_query_references_fdr_columns(self):
        """SQL should read holdout_p_value and fdr_survived."""
        import inspect
        from gefion.charts.queries import fetch_cycle_fdr_for_chart

        source = inspect.getsource(fetch_cycle_fdr_for_chart)
        assert "holdout_p_value" in source
        assert "fdr_survived" in source

    @pytest.fixture
    def cycle_with_experiments(self, conn):
        """Create a cycle with evaluated experiments; yield its id, then clean up."""
        from psycopg.types.json import Json

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiment_cycles (name, holdout_start_date, holdout_end_date, fdr_rate)
                VALUES ('chart-test-cycle', %s, %s, 0.10)
                RETURNING id
                """,
                (date.today() - timedelta(days=42), date.today()),
            )
            cycle_id = cur.fetchone()[0]
            exp_ids = []
            for name, p_value, survived in (
                ("exp-signal", 0.003, True),
                ("exp-noise", 0.42, False),
                ("exp-unevaluated", None, None),
            ):
                cur.execute(
                    """
                    INSERT INTO experiments
                        (name, experiment_type, config, cycle_id, holdout_p_value, fdr_survived)
                    VALUES (%s, 'feature_engineering', %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (name, Json({}), cycle_id, p_value, survived),
                )
                exp_ids.append(cur.fetchone()[0])
        yield cycle_id
        with conn.cursor() as cur:
            cur.execute("DELETE FROM experiments WHERE cycle_id = %s", (cycle_id,))
            cur.execute("DELETE FROM experiment_cycles WHERE id = %s", (cycle_id,))

    def test_returns_experiments_and_rate(self, conn, cycle_with_experiments):
        """Result should carry chart-ready experiment rows plus the cycle's FDR rate."""
        from gefion.charts.queries import fetch_cycle_fdr_for_chart

        result = fetch_cycle_fdr_for_chart(conn, cycle_with_experiments)

        assert result["fdr_rate"] == pytest.approx(0.10)
        rows = result["experiments"]
        assert len(rows) == 2  # unevaluated experiment excluded (no p-value)
        by_name = {row["name"]: row for row in rows}
        assert by_name["exp-signal"]["promoted"] is True
        assert by_name["exp-noise"]["promoted"] is False
        for row in rows:
            assert isinstance(row["p_value"], float)
            assert row["p_value"] > 0  # FDR chart uses a log scale

    def test_unknown_cycle_returns_empty(self, conn):
        from gefion.charts.queries import fetch_cycle_fdr_for_chart

        result = fetch_cycle_fdr_for_chart(conn, -1)
        assert result["experiments"] == []
