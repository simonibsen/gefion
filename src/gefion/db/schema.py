"""
DDL helpers for stock tables.

We keep DDL as simple SQL strings executed via psycopg2. Hypertable creation
assumes TimescaleDB is installed/enabled (see docker-compose).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import Connection
from psycopg import sql

from gefion.observability import create_span, set_attributes

_DEFAULT_TEST_URL = "postgresql://gefion:gefionpass@localhost:6432/gefion_test"


def _append_test_suffix(url: str) -> str:
    """Append ``_test`` to the database name in a PostgreSQL URL.

    Preserves query parameters and is idempotent (no double ``_test``).
    """
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if not db_name.endswith("_test"):
        db_name = db_name + "_test"
    new_path = "/" + db_name
    return urlunparse(parsed._replace(path=new_path))


def test_db_url() -> str:
    """Return the database URL for tests.

    Resolution order:
    1. ``TEST_DATABASE_URL`` env var (explicit override)
    2. ``DATABASE_URL`` env var with ``_test`` appended to the DB name
    3. Default: ``postgresql://gefion:gefionpass@localhost:6432/gefion_test``
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    base = os.environ.get("DATABASE_URL")
    if base:
        return _append_test_suffix(base)

    return _DEFAULT_TEST_URL


def _ensure_timescaledb(conn: Connection) -> None:
    """Ensure TimescaleDB extension is enabled, handling version conflicts gracefully."""
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            # Extension already loaded with different version - this is fine
            pass
    conn.commit()


def create_stocks_table(conn: Connection) -> None:
    """Create stocks dimension table."""
    with create_span("db.schema.create_stocks_table") as span:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL UNIQUE,
                    status TEXT,
                    name TEXT,
                    sector TEXT,
                    industry TEXT,
                    exchange TEXT,
                    asset_type TEXT,
                    updated_at TIMESTAMP
                );
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS asset_type TEXT;
                CREATE INDEX IF NOT EXISTS stocks_sector_idx ON stocks(sector);
                CREATE INDEX IF NOT EXISTS stocks_industry_idx ON stocks(industry);
                CREATE INDEX IF NOT EXISTS stocks_exchange_idx ON stocks(exchange) WHERE exchange IS NOT NULL;
                """
            )
        conn.commit()
        set_attributes(span, table="stocks")


def create_stock_ohlcv_table(conn: Connection) -> None:
    """Create stock_ohlcv hypertable with unique stock/date constraint."""
    with create_span("db.schema.create_stock_ohlcv_table") as span:
        _ensure_timescaledb(conn)

        # Check if table exists but is not a hypertable - if so, drop and recreate
        # This fixes issues where the table was created before TimescaleDB was enabled
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'stock_ohlcv'
                    );
                """)
                table_exists = cur.fetchone()[0]

                if table_exists:
                    # Check if it's already a hypertable
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM timescaledb_information.hypertables
                            WHERE hypertable_schema = 'public' AND hypertable_name = 'stock_ohlcv'
                        );
                    """)
                    is_hypertable = cur.fetchone()[0]

                    if not is_hypertable:
                        # Table exists but isn't a hypertable - drop and recreate
                        print("Dropping existing stock_ohlcv table to recreate as hypertable...")
                        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
                        conn.commit()
            except Exception as e:
                # If TimescaleDB queries fail, just try to continue
                pass

        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_ohlcv (
                    id BIGSERIAL,
                    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    open NUMERIC(18,6),
                    high NUMERIC(18,6),
                    low NUMERIC(18,6),
                    close NUMERIC(18,6),
                    adjusted_close NUMERIC(18,6),
                    dividend_amount NUMERIC(18,6),
                    split_coefficient NUMERIC(18,6),
                    volume BIGINT,
                    source TEXT,
                    PRIMARY KEY (id, date),
                    UNIQUE (data_id, date)
                );
                """
            )
            cur.execute(
                """
                SELECT create_hypertable('stock_ohlcv', 'date', if_not_exists => TRUE);
                """
            )
            # Performance helpers: chunk interval and BRIN on date for large scans
            try:
                cur.execute("SELECT set_chunk_time_interval('stock_ohlcv', INTERVAL '30 days');")
            except Exception:
                pass
            cur.execute("CREATE INDEX IF NOT EXISTS stock_ohlcv_brin ON stock_ohlcv USING BRIN(date);")
            # Composite B-tree index for efficient single-stock time-series queries
            # Optimized for "SELECT ... WHERE data_id = X AND date BETWEEN Y AND Z ORDER BY date DESC"
            cur.execute("""
                CREATE INDEX IF NOT EXISTS stock_ohlcv_data_id_date_idx
                    ON stock_ohlcv(data_id, date DESC);
            """)
        conn.commit()
        set_attributes(span, table="stock_ohlcv")


def create_feature_definitions_table(conn: Connection) -> None:
    """Descriptor table for computed features.

    Supports both legacy singular columns (source_table, source_column)
    and new plural columns (source_tables, source_columns) for features
    requiring multiple input columns.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_definitions (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                function_name TEXT NOT NULL,
                params JSONB,
                source_table TEXT,
                source_column TEXT,
                source_tables JSONB,
                source_columns JSONB,
                store_table TEXT DEFAULT 'computed_features',
                store_column TEXT,
                store_type TEXT DEFAULT 'double precision',
                active BOOLEAN DEFAULT TRUE,
                version TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """
        )
    conn.commit()


def create_feature_functions_table(conn: Connection) -> None:
    """Function registry for reusable feature functions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_functions (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                description TEXT,
                language TEXT NOT NULL,
                function_body TEXT NOT NULL,
                inputs JSONB,
                output_name TEXT DEFAULT 'value',
                output_type TEXT DEFAULT 'double precision',
                param_schema JSONB,
                defaults JSONB,
                dependencies JSONB,
                checksum TEXT,
                tags TEXT[],
                min_app_version TEXT,
                enabled BOOLEAN DEFAULT TRUE,
                called_by TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(name, version)
            );
            """
        )
        # Create composite index for efficient lookups by (enabled, status, name)
        # This optimizes the common query pattern in dispatcher:
        # WHERE enabled = TRUE AND status = 'active' AND name = %s
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feature_functions_enabled_status_name
            ON feature_functions (enabled, status, name);
            """
        )
        # Create index for plugin discovery - find all plugins for a meta-function
        # Optimizes: WHERE called_by = 'meta_function' AND enabled = TRUE AND status = 'active'
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feature_functions_called_by_enabled_status
            ON feature_functions (called_by, enabled, status)
            WHERE called_by IS NOT NULL;
            """
        )
    conn.commit()


def create_strategy_registry_table(conn: Connection) -> None:
    """Strategy registry - maps strategy names to Python implementations.

    Each entry points to a Python class (module_path + class_name).
    Strategy implementations remain as Python code, not stored in DB.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_registry (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                module_path TEXT NOT NULL,
                class_name TEXT NOT NULL,
                default_params JSONB DEFAULT '{}',
                param_schema JSONB,
                description TEXT,
                tags TEXT[],
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        # Index for efficient lookups
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_registry_enabled
            ON strategy_registry (enabled, name);
            """
        )
    conn.commit()


def create_strategy_configs_table(conn: Connection) -> None:
    """Strategy configurations - parameterized instances of strategies.

    Each config references a strategy from the registry and provides
    specific parameters for that strategy.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_configs (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                strategy_name TEXT NOT NULL
                    REFERENCES strategy_registry(name) ON DELETE CASCADE,
                params JSONB NOT NULL DEFAULT '{}',
                description TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        # Index for listing active configs
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_configs_active
            ON strategy_configs (active, name);
            """
        )
        # Index for finding configs by strategy
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_configs_strategy
            ON strategy_configs (strategy_name);
            """
        )
    conn.commit()


def create_computed_features_table(conn: Connection) -> None:
    """Tall table for computed feature values."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS computed_features (
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
                value DOUBLE PRECISION,
                source TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (feature_id, data_id, date)
            );
            """
        )
        cur.execute(
            """
            SELECT create_hypertable('computed_features', 'date', if_not_exists => TRUE);
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS computed_features_idx ON computed_features(feature_id, data_id, date);")
        try:
            cur.execute("SELECT set_chunk_time_interval('computed_features', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute("CREATE INDEX IF NOT EXISTS computed_features_brin ON computed_features USING BRIN(date);")
        # Composite B-tree index optimized for feature-specific queries with DESC date ordering
        # Optimized for "SELECT ... WHERE feature_id = X AND data_id = Y AND date BETWEEN ... ORDER BY date DESC"
        cur.execute("""
            CREATE INDEX IF NOT EXISTS computed_features_feature_data_date_idx
                ON computed_features(feature_id, data_id, date DESC);
        """)
    conn.commit()


def create_ml_datasets_table(conn: Connection) -> None:
    """Dataset manifests for ML training/inference runs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_datasets (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                universe JSONB,
                feature_names TEXT[] NOT NULL,
                lookback_days INTEGER NOT NULL,
                horizons_days INTEGER[] NOT NULL,
                label_spec JSONB NOT NULL,
                split_spec JSONB NOT NULL,
                artifact_uri TEXT NOT NULL,
                checksum TEXT,
                UNIQUE (name, version)
            );
            """
        )
    conn.commit()


def create_ml_runs_table(conn: Connection) -> None:
    """Run tracking for ML train/predict/eval."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_runs (
                id SERIAL PRIMARY KEY,
                run_type TEXT NOT NULL, -- 'train' | 'predict' | 'eval'
                status TEXT NOT NULL DEFAULT 'running',
                created_at TIMESTAMP DEFAULT NOW(),
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                dataset_id INTEGER REFERENCES ml_datasets(id),
                run_config JSONB NOT NULL,
                code_version TEXT,
                notes TEXT
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ml_runs_type_status_idx ON ml_runs(run_type, status);")
    conn.commit()


def create_ml_models_table(conn: Connection) -> None:
    """Model registry: training artifact metadata and lineage."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_models (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                train_run_id INTEGER REFERENCES ml_runs(id),
                dataset_id INTEGER REFERENCES ml_datasets(id),
                algorithm TEXT,
                hyperparams JSONB,
                metrics JSONB,
                artifact_uri TEXT NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                UNIQUE (name, version)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ml_models_active_idx ON ml_models(active, name);")
    conn.commit()


def create_quantile_predictions_table(conn: Connection) -> None:
    """Store predicted return quantiles for multiple horizons."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quantile_predictions (
                model_id INTEGER NOT NULL REFERENCES ml_models(id),
                data_id INTEGER NOT NULL REFERENCES stocks(id),
                prediction_date DATE NOT NULL,
                horizon_days INTEGER NOT NULL,
                q10 NUMERIC(10,4),
                q50 NUMERIC(10,4),
                q90 NUMERIC(10,4),
                model_version TEXT,
                features_snapshot JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                run_id INTEGER REFERENCES ml_runs(id),
                PRIMARY KEY (model_id, data_id, prediction_date, horizon_days),
                CONSTRAINT check_quantile_order CHECK (q10 <= q50 AND q50 <= q90),
                CONSTRAINT check_horizon_positive CHECK (horizon_days > 0)
            );
            """
        )
        cur.execute("SELECT create_hypertable('quantile_predictions', 'prediction_date', if_not_exists => TRUE);")
        try:
            cur.execute("SELECT set_chunk_time_interval('quantile_predictions', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS quantile_predictions_symbol_date_idx
                ON quantile_predictions(data_id, prediction_date, horizon_days);
            """
        )
    conn.commit()


def create_prediction_outcomes_table(conn: Connection) -> None:
    """Store realized outcomes for predictions (evaluation)."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_outcomes (
                data_id INTEGER NOT NULL REFERENCES stocks(id),
                prediction_date DATE NOT NULL,
                outcome_date DATE NOT NULL,
                horizon_days INTEGER NOT NULL,
                actual_return NUMERIC(10,4),
                model_id INTEGER REFERENCES ml_models(id),
                created_at TIMESTAMP DEFAULT NOW(),
                run_id INTEGER REFERENCES ml_runs(id),
                PRIMARY KEY (data_id, prediction_date, horizon_days)
            );
            """
        )
        cur.execute("SELECT create_hypertable('prediction_outcomes', 'prediction_date', if_not_exists => TRUE);")
        try:
            cur.execute("SELECT set_chunk_time_interval('prediction_outcomes', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS prediction_outcomes_symbol_date_idx
                ON prediction_outcomes(data_id, prediction_date, horizon_days);
            """
        )
    conn.commit()


def create_model_performance_table(conn: Connection) -> None:
    """Track model calibration and validation metrics."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS model_performance (
                model_id INTEGER PRIMARY KEY REFERENCES ml_models(id),
                model_name TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                q10_calibration NUMERIC(5,2),
                q50_calibration NUMERIC(5,2),
                q90_calibration NUMERIC(5,2),
                quantile_loss NUMERIC(10,6),
                avg_iqr NUMERIC(10,4),
                eval_start_date DATE,
                eval_end_date DATE,
                num_predictions INTEGER,
                updated_at TIMESTAMP DEFAULT NOW(),
                eval_run_id INTEGER REFERENCES ml_runs(id)
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS model_performance_name_horizon_idx ON model_performance(model_name, horizon_days);"
        )
    conn.commit()


def create_trend_class_predictions_table(conn: Connection) -> None:
    """Store 5-class trend classification probabilities per horizon."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        # Schema matches sql/schema.sql exactly
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_class_predictions (
                model_id INTEGER NOT NULL REFERENCES ml_models(id),
                data_id INTEGER NOT NULL REFERENCES stocks(id),
                prediction_date DATE NOT NULL,
                horizon_days INTEGER NOT NULL,
                predicted_class TEXT NOT NULL,
                weak_threshold NUMERIC(8,6),
                strong_threshold NUMERIC(8,6),
                p_strong_up NUMERIC(5,4),
                p_weak_up NUMERIC(5,4),
                p_neutral NUMERIC(5,4),
                p_weak_down NUMERIC(5,4),
                p_strong_down NUMERIC(5,4),
                entropy NUMERIC(8,6),
                margin NUMERIC(5,4),
                created_at TIMESTAMP DEFAULT NOW(),
                run_id INTEGER REFERENCES ml_runs(id),
                PRIMARY KEY (model_id, data_id, prediction_date, horizon_days)
            );
            """
        )
        cur.execute("SELECT create_hypertable('trend_class_predictions', 'prediction_date', if_not_exists => TRUE);")
        try:
            cur.execute("SELECT set_chunk_time_interval('trend_class_predictions', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS trend_class_predictions_symbol_date_idx
                ON trend_class_predictions(data_id, prediction_date, horizon_days);
            """
        )
    conn.commit()


def create_predictions_table(conn: Connection) -> None:
    """Unified predictions table storing both quantile and trend_class predictions as JSONB."""
    with create_span("db.schema.create_predictions_table") as span:
        _ensure_timescaledb(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    model_id INTEGER NOT NULL REFERENCES ml_models(id),
                    data_id INTEGER NOT NULL REFERENCES stocks(id),
                    prediction_date DATE NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    prediction_type TEXT NOT NULL,
                    prediction_values JSONB NOT NULL,
                    metadata JSONB DEFAULT '{}',
                    run_id INTEGER REFERENCES ml_runs(id),
                    created_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (model_id, data_id, prediction_date, horizon_days, prediction_type),
                    CONSTRAINT check_horizon_positive CHECK (horizon_days > 0),
                    CONSTRAINT check_prediction_type CHECK (prediction_type IN ('quantile', 'trend_class'))
                );
                """
            )
            cur.execute("SELECT create_hypertable('predictions', 'prediction_date', if_not_exists => TRUE);")
            try:
                cur.execute("SELECT set_chunk_time_interval('predictions', INTERVAL '30 days');")
            except Exception:
                pass
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS predictions_symbol_date_idx
                    ON predictions(data_id, prediction_date, horizon_days);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS predictions_type_idx
                    ON predictions(prediction_type, prediction_date DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS predictions_run_id_idx
                    ON predictions(run_id);
                """
            )
        conn.commit()
        set_attributes(span, table="predictions")


def create_stocks_fundamentals_table(conn: Connection) -> None:
    """Time-series table for company fundamentals (market cap, PE, etc.)."""
    _ensure_timescaledb(conn)
    with create_span("db.schema.create_stocks_fundamentals_table") as span:
      with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stocks_fundamentals (
                data_id INTEGER NOT NULL REFERENCES stocks(id),
                date DATE NOT NULL,
                market_cap BIGINT,
                pe_ratio NUMERIC(10,2),
                forward_pe NUMERIC(10,2),
                peg_ratio NUMERIC(10,4),
                book_value NUMERIC(12,4),
                dividend_yield NUMERIC(8,6),
                eps NUMERIC(10,4),
                revenue_per_share NUMERIC(10,4),
                profit_margin NUMERIC(8,6),
                operating_margin NUMERIC(8,6),
                return_on_equity NUMERIC(8,6),
                beta NUMERIC(8,4),
                ev_to_ebitda NUMERIC(10,2),
                shares_outstanding BIGINT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date)
            );
            """
        )
        cur.execute("SELECT create_hypertable('stocks_fundamentals', 'date', if_not_exists => TRUE);")
        try:
            cur.execute("SELECT set_chunk_time_interval('stocks_fundamentals', INTERVAL '90 days');")
        except Exception:
            pass
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS stocks_fundamentals_data_date_idx
                ON stocks_fundamentals(data_id, date DESC);
            """
        )
      conn.commit()
      set_attributes(span, table="stocks_fundamentals")


def migrate_stock_tables_to_data_id(conn: Connection) -> None:
    """
    Rename legacy stock_id columns to data_id if they exist.

    Safe to run repeatedly; no-op when already migrated.
    """
    tables = ["stock_ohlcv", "company_fundamentals_history"]
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = 'stock_id';
                """,
                (table,),
            )
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER TABLE {} RENAME COLUMN stock_id TO data_id;").format(sql.Identifier(table))
                )
    conn.commit()


def drop_legacy_stock_indicators(conn: Connection) -> None:
    """Drop legacy wide stock_indicators table if present."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stock_indicators CASCADE;")
    conn.commit()
