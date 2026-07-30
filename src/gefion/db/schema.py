"""
DDL helpers for stock tables.

We keep DDL as simple SQL strings executed via psycopg2. Hypertable creation
assumes TimescaleDB is installed/enabled (see docker-compose).
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import Connection
from psycopg import sql

from gefion.observability import create_span, set_attributes

_DEFAULT_TEST_URL = "postgresql://gefion:gefionpass@localhost:6432/gefion_test"


# --- Soft-edge registry (issue #155) --------------------------------------
#
# Gefion's code-as-data design uses deliberate non-FK edges: by-name
# references, expression-embedded names, JSONB provenance stamps, and the
# registry-routed computed_features.data_id. Every such edge MUST be
# declared here with its compensation — a validator (detection callable)
# and/or the deletion door that scans or refuses across it.
#
# tests/test_soft_edge_registry.py enforces this registry: every entry's
# dotted paths must import to callables, and every `plan_*` function in a
# `gefion/*/deletion.py` module must be claimed by an entry. Adding a new
# soft edge (which gets a deletion door per the #76 house pattern) without
# registering it here is red CI.
#
# `validator=None` is a DECLARED gap: `no_validator_reason` must explain
# why the deletion door alone is sufficient (e.g. refusal-while-referenced
# makes dangling references unreachable through supported paths).

@dataclasses.dataclass(frozen=True)
class SoftEdge:
    """One deliberate non-FK reference and its registered compensation."""

    name: str            # unique short identifier
    from_ref: str        # referencing side: table.column / expression / JSONB path
    to_ref: str          # referenced side: table.column (or naming scheme)
    kind: str            # "column" | "expression" | "jsonb" | "naming"
    deletion_door: str   # dotted path to the plan_* door that scans/refuses it
    validator: Optional[str] = None  # dotted path to a detection callable
    no_validator_reason: str = ""    # required (non-empty) when validator is None
    notes: str = ""


SOFT_EDGES: tuple = (
    SoftEdge(
        name="definition_function",
        from_ref="feature_definitions.function_name",
        to_ref="feature_functions.name",
        kind="column",
        deletion_door="gefion.features.deletion.plan_function_delete",
        validator="gefion.cli._find_orphans",
        notes="Surfaced by `gefion feat-def-validate`; `feat-def-fix` "
              "deactivates orphaned definitions. The door refuses to delete "
              "a function while any definition routes to it.",
    ),
    SoftEdge(
        name="regime_expression_feature",
        from_ref="regime_definitions.expression (comparison leaf 'feature')",
        to_ref="feature_definitions.name",
        kind="expression",
        deletion_door="gefion.features.deletion.plan_definition_delete",
        validator=None,
        no_validator_reason=(
            "Define-time validation (validate_expression) is structural "
            "only; existence surfaces at compute time as a loud LookupError "
            "(regimes.labels), and the door REFUSES deleting a referenced "
            "feature (feat-def-fix deactivates, never deletes). No static "
            "dangling-reference scan exists — #155 finding: a YAML regime "
            "import can name a feature that does not exist and only fail "
            "at compute time."),
    ),
    SoftEdge(
        name="computed_features_entity",
        from_ref="computed_features.data_id",
        to_ref="(feature_definitions.entity_table).id",
        kind="column",
        deletion_door="gefion.entities.deletion.plan_delete",
        validator="gefion.entities.orphans.scan",
        notes="The retired hard FK (spec 007). The orphan scan feeds "
              "db-health's entity_integrity section.",
    ),
    SoftEdge(
        name="universe_provenance",
        from_ref="ml_datasets.universe / experiments.config / "
                 "regime_discovery_runs.search_space ->> 'universe_name'",
        to_ref="universe_definitions.name",
        kind="jsonb",
        deletion_door="gefion.universe.deletion.plan_universe_delete",
        validator=None,
        no_validator_reason=(
            "Refusal-while-referenced: the door refuses deletion while any "
            "provenance stamp names the universe, so stamps always resolve "
            "through supported paths — a validator would have nothing to "
            "find."),
    ),
    SoftEdge(
        name="dataset_feature_names",
        from_ref="ml_datasets.feature_names[]",
        to_ref="feature_definitions.name",
        kind="jsonb",
        deletion_door="gefion.features.deletion.plan_definition_delete",
        validator=None,
        no_validator_reason=(
            "Provenance is reported, never mutated (#76): a deleted feature "
            "leaves the dataset row as a historical record by design — "
            "dangling names here are accepted accounting, not corruption."),
    ),
    SoftEdge(
        name="experiment_regime_results",
        from_ref="experiments.results -> 'by_regime' ->> 'regime'",
        to_ref="regime_definitions.name",
        kind="jsonb",
        deletion_door="gefion.regimes.deletion.plan_regime_delete",
        validator=None,
        no_validator_reason=(
            "Reported, never mutated; `regime archive` is the recommended "
            "lifecycle exit so results stay resolvable by name. A forced "
            "hard delete may leave name-keyed results dangling by design."),
    ),
    SoftEdge(
        name="model_signal_features",
        from_ref="feature_definitions.name LIKE 'pred_%__<model>_<version>'",
        to_ref="ml_models(name, version)",
        kind="naming",
        deletion_door="gefion.ml.deletion.plan_model_delete",
        validator=None,
        no_validator_reason=(
            "Naming-convention edge (spec 012): the model door enumerates "
            "and deletes the whole materialized-signal family with the "
            "model, so the convention is exercised on every delete."),
    ),
    SoftEdge(
        name="feature_source_experiment",
        from_ref="feature_definitions.source_experiment_id",
        to_ref="experiments.id",
        kind="column",
        deletion_door="gefion.experiments.deletion.plan_experiment_delete",
        validator=None,
        no_validator_reason=(
            "Soft integer reference (no FK): the experiment door enumerates "
            "owned features (experimental vs promoted) in its blast radius; "
            "a dangling id after experiment deletion marks a feature as "
            "orphaned provenance, which the door surfaces before delete."),
    ),
    SoftEdge(
        name="discovery_admission_provenance",
        from_ref="regime_definitions.descriptive_metadata (machine origin)",
        to_ref="regime_discovery_runs / regime_candidates ledger",
        kind="jsonb",
        deletion_door="gefion.regimes.deletion.plan_run_delete",
        validator=None,
        no_validator_reason=(
            "The run door refuses deletion ALWAYS while admissions exist "
            "(the ledger is the multiple-testing audit trail behind a live "
            "artifact); the candidate ledger deliberately has no FK to the "
            "artifact so accounting survives artifact deletion."),
    ),
)


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


def _assert_test_db(url: str) -> str:
    """Refuse any URL whose database name does not look like a test DB (#167).

    The tests write and DELETE freely, so a misconfigured ``TEST_DATABASE_URL``
    pointing at a real database is a data-loss footgun — this is how the qcl_*
    universe fixtures once leaked into the dev ``gefion`` DB. Failing closed here
    protects every DB-backed test at a single chokepoint.

    The bar is "the database name contains ``test``" (case-insensitive), not a
    strict ``_test`` suffix: the explicit ``TEST_DATABASE_URL`` override is
    documented to accept operator-chosen names like ``my_test_db``. That still
    refuses the real footgun — a plain ``gefion`` has no ``test`` in it.
    """
    db_name = urlparse(url).path.lstrip("/")
    if "test" not in db_name.lower():
        raise ValueError(
            f"test_db_url() resolved to non-test database {db_name!r}; the tests "
            "database name must contain 'test' (see #167). Refusing to hand the "
            "suite a URL that could clobber real data."
        )
    return url


def test_db_url() -> str:
    """Return the database URL for tests.

    Resolution order:
    1. ``TEST_DATABASE_URL`` env var (explicit override)
    2. ``DATABASE_URL`` env var with ``_test`` appended to the DB name
    3. Default: ``postgresql://gefion:gefionpass@localhost:6432/gefion_test``

    Every resolved URL is guarded by :func:`_assert_test_db` — the result is
    guaranteed to target a ``_test`` database or raise (#167).
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return _assert_test_db(explicit)

    base = os.environ.get("DATABASE_URL")
    if base:
        return _assert_test_db(_append_test_suffix(base))

    return _assert_test_db(_DEFAULT_TEST_URL)


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
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS status TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sector TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS asset_type TEXT;
                ALTER TABLE stocks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
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
                created_at TIMESTAMP DEFAULT NOW(),
                entity_table TEXT NOT NULL DEFAULT 'stocks'
            );
            """
        )
        # Idempotent add for pre-007 databases (matches sql/schema.sql)
        cur.execute(
            "ALTER TABLE feature_definitions "
            "ADD COLUMN IF NOT EXISTS entity_table TEXT NOT NULL DEFAULT 'stocks';"
        )
        # Idempotent adds for the 004 experiment-provenance columns — the
        # creator must mirror sql/schema.sql (a schema-reset test rebuilds
        # from HERE, and a drifted creator broke later tests in suite order)
        cur.execute(
            "ALTER TABLE feature_definitions "
            "ADD COLUMN IF NOT EXISTS is_experimental BOOLEAN DEFAULT FALSE;"
        )
        cur.execute(
            "ALTER TABLE feature_definitions "
            "ADD COLUMN IF NOT EXISTS source_experiment_id INTEGER;"
        )
        cur.execute(
            "ALTER TABLE feature_definitions "
            "ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ;"
        )
    conn.commit()


def create_macro_series_tables(conn: Connection) -> None:
    """Macro home (spec 007): series catalog + raw values. Mirrors
    sql/schema.sql; idempotent — fixtures touching macro_series call this
    (CI collection order can run after a schema-reset test)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_series (
                id           SERIAL PRIMARY KEY,
                name         TEXT UNIQUE NOT NULL,
                provider     TEXT NOT NULL,
                kind         TEXT NOT NULL,
                cadence      TEXT NOT NULL CHECK (cadence IN ('daily','weekly','monthly')),
                description  TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS macro_series_values (
                series_id    INTEGER NOT NULL REFERENCES macro_series(id) ON DELETE CASCADE,
                date         DATE NOT NULL,
                value        NUMERIC(14,6) NOT NULL,
                open         NUMERIC(14,6),
                high         NUMERIC(14,6),
                low          NUMERIC(14,6),
                PRIMARY KEY (series_id, date)
            );
            """
        )
    conn.commit()


def create_market_function_candidates_table(conn: Connection) -> None:
    """Candidate ledger for generated market functions (spec 014,
    owner-approved 2026-07-18). Mirrors sql/schema.sql; idempotent —
    fixtures touching candidates call this (CI collection order can run
    after a schema-reset test). Candidates live here, never in
    feature_functions, so pending/rejected generated code has no execution
    path by construction."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_function_candidates (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                kind TEXT NOT NULL CHECK (kind IN ('cross_section', 'composite')),
                function_body TEXT NOT NULL,
                inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
                description TEXT,
                origin TEXT NOT NULL CHECK (origin IN ('claude', 'template', 'manual')),
                principle_id TEXT,
                generator TEXT,
                dry_run JSONB,
                review_state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (review_state IN ('pending', 'approved', 'rejected')),
                reviewed_by TEXT,
                reviewed_at TIMESTAMPTZ,
                review_reason TEXT,
                promoted_function_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (name, version)
            );
            """
        )
    conn.commit()


def create_system_observations_table(conn: Connection) -> None:
    """System-observations ledger (#144, owner-approved 2026-07-18).
    Mirrors sql/schema.sql; idempotent — fixtures touching observations
    call this. Observations, never actions: adoption is a human act."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS system_observations (
                id BIGSERIAL PRIMARY KEY,
                observer TEXT NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('improvement', 'anomaly', 'tuning', 'hypothesis')),
                observation TEXT NOT NULL,
                evidence JSONB,
                suggested_action TEXT,
                review_state TEXT NOT NULL DEFAULT 'open'
                    CHECK (review_state IN ('open', 'acknowledged', 'adopted', 'rejected')),
                reviewed_by TEXT,
                reviewed_at TIMESTAMPTZ,
                review_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    conn.commit()


def create_universe_definitions_table(conn: Connection) -> None:
    """Universe definitions (spec 015, owner-approved 2026-07-19). Named,
    rule-defined subsets of the stock population — the entity-space sibling
    of regime_definitions. Mirrors sql/schema.sql; idempotent — fixtures
    touching universes call this."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS universe_definitions (
                id          SERIAL PRIMARY KEY,
                name        TEXT UNIQUE NOT NULL,
                description TEXT,
                rules       JSONB NOT NULL,
                pins        JSONB NOT NULL DEFAULT '[]'::jsonb,
                fingerprint TEXT NOT NULL,
                is_default  BOOLEAN NOT NULL DEFAULT FALSE,
                enabled     BOOLEAN NOT NULL DEFAULT TRUE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS universe_definitions_default_idx
                ON universe_definitions(is_default) WHERE is_default;
            """
        )
    conn.commit()


def create_universe_exclusions_table(conn: Connection) -> None:
    """Universe membership in complement form (spec 015, owner-approved
    2026-07-19): a symbol is a member as-of D iff no row covers it. Mirrors
    sql/schema.sql; idempotent. Requires stocks + universe_definitions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS universe_exclusions (
                id            SERIAL PRIMARY KEY,
                universe_id   INTEGER NOT NULL REFERENCES universe_definitions(id) ON DELETE CASCADE,
                data_id       INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                rule_name     TEXT NOT NULL,
                excluded_from DATE NOT NULL,
                excluded_to   DATE,
                refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (universe_id, data_id, rule_name, excluded_from)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS universe_exclusions_lookup_idx
                ON universe_exclusions(universe_id, data_id);
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
                scope TEXT NOT NULL DEFAULT 'stock' CHECK (scope IN ('stock','market','materialized')),
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
            "ALTER TABLE feature_functions ADD COLUMN IF NOT EXISTS scope TEXT "
            "NOT NULL DEFAULT 'stock' CHECK (scope IN ('stock','market','materialized'));"
        )
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
    """Tall table for computed feature values.

    No hard FK on data_id (spec 007): entity identity is declared — the pair
    (feature_definitions.entity_table, data_id) is the logical FK.
    """
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS computed_features (
                data_id INTEGER NOT NULL,
                date DATE NOT NULL,
                feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
                value DOUBLE PRECISION,
                source TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (feature_id, data_id, date)
            );
            """
        )
        # Idempotent for pre-007 tables: drop the retired FK by introspected name
        cur.execute(
            """
            DO $$
            DECLARE
                fk_name TEXT;
            BEGIN
                SELECT conname INTO fk_name
                FROM pg_constraint
                WHERE contype = 'f'
                  AND conrelid = 'computed_features'::regclass
                  AND confrelid = 'stocks'::regclass;
                IF fk_name IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE computed_features DROP CONSTRAINT %I', fk_name);
                END IF;
            END $$;
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


def create_cross_sectional_features_table(conn: Connection) -> None:
    """Market-relative rankings (rank/percentile) per comparison group.

    Universe provenance (issue #153): every ranking row records the
    population it was ranked within — universe_name + universe_fingerprint,
    the spec-015 ``ml_datasets.universe`` pattern. NULL in these columns
    means pre-provenance legacy: the population is unknown (pre-015
    unfiltered market or post-015 default universe — not distinguishable
    after the fact). Readers must never treat NULL as modeling_default;
    absence of data is not evidence.
    """
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cross_sectional_features (
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                feature_name TEXT NOT NULL,
                comparison_group TEXT NOT NULL DEFAULT 'market',
                value DOUBLE PRECISION,
                rank INTEGER,
                percentile DOUBLE PRECISION,
                universe_name TEXT,
                universe_fingerprint TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date, feature_name, comparison_group)
            );
            """
        )
        # Idempotent for pre-provenance tables (created before issue #153)
        cur.execute(
            """
            ALTER TABLE cross_sectional_features
                ADD COLUMN IF NOT EXISTS universe_name TEXT,
                ADD COLUMN IF NOT EXISTS universe_fingerprint TEXT;
            """
        )
        cur.execute(
            "SELECT create_hypertable('cross_sectional_features', 'date', "
            "if_not_exists => TRUE);"
        )
        try:
            cur.execute(
                "SELECT set_chunk_time_interval('cross_sectional_features', "
                "INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute(
            "CREATE INDEX IF NOT EXISTS cross_sectional_features_brin "
            "ON cross_sectional_features USING BRIN(date);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS "
            "cross_sectional_features_data_id_date_idx "
            "ON cross_sectional_features(data_id, date DESC);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS cross_sectional_features_date_idx "
            "ON cross_sectional_features(date DESC);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS "
            "cross_sectional_features_comparison_group_idx "
            "ON cross_sectional_features(comparison_group, date);")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS "
            "cross_sectional_features_feature_date_group_rank_idx "
            "ON cross_sectional_features(feature_name, date, "
            "comparison_group, rank);")
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


def create_quarterly_financials_table(conn: Connection) -> None:
    """Time-series table for quarterly financial reports (income, balance sheet, cash flow, earnings)."""
    _ensure_timescaledb(conn)
    with create_span("db.schema.create_quarterly_financials_table") as span:
      with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS quarterly_financials (
                data_id INTEGER NOT NULL REFERENCES stocks(id),
                date DATE NOT NULL,
                statement_type TEXT NOT NULL,
                reported_at DATE,
                -- Income statement
                revenue BIGINT,
                net_income BIGINT,
                gross_profit BIGINT,
                ebitda BIGINT,
                operating_income BIGINT,
                eps NUMERIC(10,4),
                -- Balance sheet
                total_assets BIGINT,
                total_liabilities BIGINT,
                shareholder_equity BIGINT,
                current_assets BIGINT,
                current_liabilities BIGINT,
                long_term_debt BIGINT,
                shares_outstanding BIGINT,
                -- Cash flow
                operating_cashflow BIGINT,
                capital_expenditures BIGINT,
                free_cash_flow BIGINT,
                -- Earnings
                reported_eps NUMERIC(10,4),
                estimated_eps NUMERIC(10,4),
                surprise NUMERIC(10,4),
                surprise_percentage NUMERIC(10,4),
                -- Overflow
                raw JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date, statement_type)
            );
            """
        )
        cur.execute("SELECT create_hypertable('quarterly_financials', 'date', if_not_exists => TRUE);")
        try:
            cur.execute("SELECT set_chunk_time_interval('quarterly_financials', INTERVAL '90 days');")
        except Exception:
            pass
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS quarterly_financials_data_type_date_idx
                ON quarterly_financials(data_id, statement_type, date DESC);
            """
        )
      conn.commit()
      set_attributes(span, table="quarterly_financials")


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
                pe_ratio NUMERIC(14,6),
                forward_pe NUMERIC(14,6),
                peg_ratio NUMERIC(14,6),
                book_value NUMERIC(14,6),
                dividend_yield NUMERIC(14,6),
                eps NUMERIC(14,6),
                revenue_per_share NUMERIC(14,6),
                profit_margin NUMERIC(14,6),
                operating_margin NUMERIC(14,6),
                return_on_equity NUMERIC(14,6),
                beta NUMERIC(14,6),
                ev_to_ebitda NUMERIC(14,6),
                shares_outstanding BIGINT,
                sector TEXT,
                industry TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date)
            );
            """
        )
        # Idempotent for pre-existing tables: providers report garbage
        # extremes (issue #79) — every ratio column must be NUMERIC(14,6)
        for col in ("pe_ratio", "forward_pe", "peg_ratio", "book_value",
                    "dividend_yield", "eps", "revenue_per_share",
                    "profit_margin", "operating_margin", "return_on_equity",
                    "beta", "ev_to_ebitda"):
            cur.execute(
                sql.SQL("ALTER TABLE stocks_fundamentals ALTER COLUMN {} "
                        "TYPE NUMERIC(14,6)").format(sql.Identifier(col)))
        # Classification history (018): sector/industry AS OF the fetch date
        cur.execute("ALTER TABLE stocks_fundamentals "
                    "ADD COLUMN IF NOT EXISTS sector TEXT;")
        cur.execute("ALTER TABLE stocks_fundamentals "
                    "ADD COLUMN IF NOT EXISTS industry TEXT;")
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
