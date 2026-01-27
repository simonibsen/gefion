"""
Helper functions for CLI commands.

These helpers consolidate repeated patterns across CLI commands to make
updates simpler and reduce code duplication.
"""
from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime
from contextlib import contextmanager
import os
import psycopg
from psycopg.types.json import Json


def parse_comma_separated(
    value: Optional[str],
    lowercase: bool = False,
    required: bool = False
) -> Optional[List[str]]:
    """
    Parse comma-separated string into list of trimmed values.

    Args:
        value: Comma-separated string or None
        lowercase: Apply .lower() to each value
        required: Raise error if result is empty

    Returns:
        List of parsed values or None if value is None/empty

    Raises:
        ValueError: If required=True and no values found

    Examples:
        >>> parse_comma_separated("foo,bar,baz")
        ['foo', 'bar', 'baz']

        >>> parse_comma_separated("  foo  ,  bar  ")
        ['foo', 'bar']

        >>> parse_comma_separated("FOO,Bar", lowercase=True)
        ['foo', 'bar']

        >>> parse_comma_separated(None)
        None

        >>> parse_comma_separated("", required=True)
        Traceback (most recent call last):
            ...
        ValueError: At least one value required
    """
    if not value:
        if required:
            raise ValueError("At least one value required")
        return None

    items = [s.strip() for s in value.split(",") if s.strip()]

    if required and not items:
        raise ValueError("At least one value required")

    if lowercase:
        items = [s.lower() for s in items]

    return items if items else None


def upsert_feature_function(
    conn: psycopg.Connection,
    payload: Dict[str, Any],
    return_id: bool = False
) -> Optional[int]:
    """
    Insert or update a feature function in the database.

    This consolidates the duplicate logic between register_function() and
    _upsert_feature_function() CLI commands.

    Args:
        conn: Database connection
        payload: Feature function definition dict with fields:
            - name (required): Function name
            - version (required): Version string
            - language (required): Programming language (e.g., "python")
            - function_body (required): Function code
            - status (optional): Status (default: "active")
            - description (optional): Function description
            - inputs (optional): Input schema as dict
            - output_name (optional): Output column name (default: "value")
            - output_type (optional): SQL type (default: "double precision")
            - param_schema (optional): Parameter schema as dict
            - defaults (optional): Default parameters as dict
            - dependencies (optional): Dependencies as dict
            - checksum (optional): Code checksum
            - tags (optional): Tags array
            - min_app_version (optional): Minimum app version
            - enabled (optional): Whether enabled (default: True)
            - created_by (optional): Creator (default: "cli")
            - called_by (optional): Parent meta-function name for plugin architecture
        return_id: If True, return the function ID

    Returns:
        Function ID if return_id=True, otherwise None

    Example:
        >>> payload = {
        ...     "name": "my_indicator",
        ...     "version": "1.0",
        ...     "language": "python",
        ...     "function_body": "def compute(rows, specs): return []"
        ... }
        >>> func_id = upsert_feature_function(conn, payload, return_id=True)
    """
    params = {
        "name": payload.get("name"),
        "version": payload.get("version"),
        "status": payload.get("status", "active"),
        "description": payload.get("description"),
        "language": payload.get("language"),
        "function_body": payload.get("function_body"),
        "inputs": Json(payload.get("inputs")) if payload.get("inputs") is not None else None,
        "output_name": payload.get("output_name", "value"),
        "output_type": payload.get("output_type", "double precision"),
        "param_schema": Json(payload.get("param_schema")) if payload.get("param_schema") is not None else None,
        "defaults": Json(payload.get("defaults")) if payload.get("defaults") is not None else None,
        "dependencies": Json(payload.get("dependencies")) if payload.get("dependencies") is not None else None,
        "checksum": payload.get("checksum"),
        "tags": payload.get("tags"),
        "min_app_version": payload.get("min_app_version"),
        "enabled": payload.get("enabled", True),
        "created_by": payload.get("created_by", "cli"),
        "called_by": payload.get("called_by"),
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_functions
            (name, version, status, description, language, function_body, inputs, output_name, output_type,
             param_schema, defaults, dependencies, checksum, tags, min_app_version, enabled, created_by, called_by)
            VALUES (%(name)s, %(version)s, %(status)s, %(description)s, %(language)s, %(function_body)s,
                    %(inputs)s, %(output_name)s, %(output_type)s, %(param_schema)s, %(defaults)s,
                    %(dependencies)s, %(checksum)s, %(tags)s, %(min_app_version)s, %(enabled)s, %(created_by)s, %(called_by)s)
            ON CONFLICT (name, version) DO UPDATE SET
                status = EXCLUDED.status,
                description = EXCLUDED.description,
                language = EXCLUDED.language,
                function_body = EXCLUDED.function_body,
                inputs = EXCLUDED.inputs,
                output_name = EXCLUDED.output_name,
                output_type = EXCLUDED.output_type,
                param_schema = EXCLUDED.param_schema,
                defaults = EXCLUDED.defaults,
                dependencies = EXCLUDED.dependencies,
                checksum = EXCLUDED.checksum,
                tags = EXCLUDED.tags,
                min_app_version = EXCLUDED.min_app_version,
                enabled = EXCLUDED.enabled,
                created_by = EXCLUDED.created_by,
                called_by = EXCLUDED.called_by,
                updated_at = NOW()
            """ + ("RETURNING id;" if return_id else ";"),
            params,
        )
        if return_id:
            return cur.fetchone()[0]
        return None


def setup_progress_reporter(
    total: int,
    progress: bool,
    json_output: bool,
    mode: str = "api",
    **kwargs
) -> Tuple[Any, Optional[Any]]:
    """
    Set up progress reporter and live display.

    Consolidates the repeated pattern of creating a ProgressReporter and
    starting the live display context.

    Args:
        total: Total items to process
        progress: Enable progress display
        json_output: Suppress progress for JSON output
        mode: Progress mode ("api", "local", "dispatcher")
        **kwargs: Additional reporter attributes (workers, batch_size, max_workers, etc.)

    Returns:
        Tuple of (reporter, live_context)
        - reporter: ProgressReporter instance
        - live_context: Live display context or None

    Example:
        >>> reporter, live = setup_progress_reporter(
        ...     total=100,
        ...     progress=True,
        ...     json_output=False,
        ...     mode="api",
        ...     workers=4,
        ...     max_workers=8
        ... )
        >>> # Use reporter for progress tracking
        >>> if live:
        ...     live.__exit__(None, None, None)  # Clean up
    """
    from g2.utils.progress import ProgressReporter

    reporter = ProgressReporter(total=total, json_output=json_output, enabled=progress)
    reporter.mode = mode

    # Set additional attributes from kwargs
    for key, value in kwargs.items():
        if hasattr(reporter, key):
            setattr(reporter, key, value)

    # Start live display if appropriate
    live = None
    if progress and not json_output:
        live = reporter.start_live()
        if live:
            live.__enter__()

    return reporter, live


def validate_date_range(
    before: Optional[str],
    after: Optional[str],
    allow_both_missing: bool = True
) -> Tuple[Optional[date], Optional[date]]:
    """
    Validate and parse date range options.

    Consolidates the repeated pattern of parsing and validating before/after
    date parameters in CLI commands.

    Args:
        before: ISO date string (YYYY-MM-DD) or None
        after: ISO date string (YYYY-MM-DD) or None
        allow_both_missing: If False, require at least one date

    Returns:
        Tuple of (before_date, after_date) as date objects or None

    Raises:
        ValueError: If dates are invalid or both missing when not allowed

    Example:
        >>> before, after = validate_date_range("2024-01-15", "2024-01-01")
        >>> # before = date(2024, 1, 15), after = date(2024, 1, 1)

        >>> before, after = validate_date_range(None, None, allow_both_missing=False)
        Traceback (most recent call last):
            ...
        ValueError: At least one date required
    """
    # Treat empty strings as None
    before = before if before and before.strip() else None
    after = after if after and after.strip() else None

    # Check if both are missing when not allowed
    if not before and not after:
        if not allow_both_missing:
            raise ValueError("At least one date required")
        return None, None

    # Parse before date
    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before).date()
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid date format for 'before': {before}") from exc

    # Parse after date
    after_dt = None
    if after:
        try:
            after_dt = datetime.fromisoformat(after).date()
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid date format for 'after': {after}") from exc

    return before_dt, after_dt


@contextmanager
def db_connection(url: Optional[str], autocommit: bool = True):
    """
    Context manager for database connections.

    Consolidates the repeated pattern of connecting to the database with
    standard configuration.

    Args:
        url: Database URL or None to use default (from settings/env)
        autocommit: Whether to enable autocommit mode (default: True)

    Yields:
        psycopg.Connection: Database connection

    Example:
        >>> with db_connection(None) as conn:
        ...     with conn.cursor() as cur:
        ...         cur.execute("SELECT 1")
    """
    from g2.config import load_settings
    from g2.db import schema

    # Determine database URL (same logic as _db_url in cli.py)
    SETTINGS = load_settings()
    db_url = url or SETTINGS.database_url or os.getenv("DATABASE_URL") or schema.test_db_url()

    try:
        conn = psycopg.connect(db_url)
    except psycopg.OperationalError as e:
        # Provide helpful error message for common database connection issues
        error_msg = str(e).lower()

        if "connection refused" in error_msg or "could not connect" in error_msg:
            raise RuntimeError(
                "✗ Could not connect to database.\n"
                "\n"
                "Possible causes:\n"
                "  1. Database is not running\n"
                "     → Start it with: docker compose up -d\n"
                "  2. Wrong port or credentials\n"
                f"     → Check DATABASE_URL in .env file\n"
                f"     → Currently using: {db_url}\n"
                "\n"
                "See: docs/USER_GUIDE.md#database-setup"
            ) from e
        elif "authentication failed" in error_msg or "password" in error_msg:
            raise RuntimeError(
                "✗ Database authentication failed.\n"
                "\n"
                "Check your database credentials:\n"
                f"  → DATABASE_URL in .env file\n"
                f"  → Currently using: {db_url}\n"
                "\n"
                "See: docs/USER_GUIDE.md#database-setup"
            ) from e
        elif "does not exist" in error_msg:
            raise RuntimeError(
                "✗ Database does not exist.\n"
                "\n"
                "Create the database:\n"
                "  → Run: docker compose up -d\n"
                "  → Run: g2 db-init\n"
                "\n"
                "See: docs/USER_GUIDE.md#database-setup"
            ) from e
        else:
            # Unknown error - re-raise with context
            raise RuntimeError(
                f"✗ Database connection error: {e}\n"
                "\n"
                "See: docs/USER_GUIDE.md#database-setup"
            ) from e

    if autocommit:
        conn.autocommit = True

    try:
        yield conn
    finally:
        conn.close()


def init_schema_tables(conn: psycopg.Connection, tables: List[str]) -> None:
    """
    Initialize required schema tables.

    Consolidates the repeated pattern of calling create_*_table() functions
    for required tables.

    Args:
        conn: Database connection
        tables: List of table names to initialize
            Valid values: "stocks", "stock_ohlcv", "feature_functions",
            "feature_definitions", "computed_features"

    Raises:
        ValueError: If unknown table name is provided

    Example:
        >>> with db_connection(None) as conn:
        ...     init_schema_tables(conn, ["stocks", "feature_functions"])
    """
    from g2.db import schema

    # Map table names to their creation functions
    table_creators = {
        "stocks": schema.create_stocks_table,
        "stock_ohlcv": schema.create_stock_ohlcv_table,
        "feature_functions": schema.create_feature_functions_table,
        "feature_definitions": schema.create_feature_definitions_table,
        "computed_features": schema.create_computed_features_table,
        "ml_datasets": schema.create_ml_datasets_table,
        "ml_runs": schema.create_ml_runs_table,
        "ml_models": schema.create_ml_models_table,
        "quantile_predictions": schema.create_quantile_predictions_table,
        "prediction_outcomes": schema.create_prediction_outcomes_table,
        "model_performance": schema.create_model_performance_table,
        "trend_class_predictions": schema.create_trend_class_predictions_table,
        "strategy_registry": schema.create_strategy_registry_table,
        "strategy_configs": schema.create_strategy_configs_table,
    }

    for table in tables:
        if table not in table_creators:
            raise ValueError(f"Unknown table: {table}. Valid tables: {list(table_creators.keys())}")
        table_creators[table](conn)
