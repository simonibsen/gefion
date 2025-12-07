"""
Generic feature computation dispatcher.

Routes feature computation based on function_name in feature_definitions,
fetches source data based on source_table/source_column metadata,
calls appropriate compute functions, and stores results.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Callable, Any, Tuple
from datetime import date
import warnings
import inspect
import psycopg
from psycopg import sql

from g2.db.ingest import insert_computed_features


# Registry mapping function_name -> compute function
COMPUTE_FUNCTIONS: Dict[str, Callable] = {}
_FUNCTION_CACHE: Dict[str, Callable] = {}
_FUNCTION_CACHE_SOURCE: Dict[str, str] = {}


def register_compute_function(function_name: str, compute_func: Callable) -> None:
    """
    Register a compute function for a given function_name.

    Args:
        function_name: The function_name from feature_definitions (e.g., 'indicator', 'derivative')
        compute_func: The compute function to call
    """
    COMPUTE_FUNCTIONS[function_name] = compute_func


def compute_features(
    conn: psycopg.Connection,
    data_id: int,
    function_names: Optional[List[str]] = None,
    feature_names: Optional[List[str]] = None,
    incremental: bool = True,
    full_refresh: bool = False,
    update_existing: bool = False,
    feature_batch_size: int = 2000,
) -> Dict[str, Any]:
    """
    Generic feature computation dispatcher.

    Reads active feature_definitions, fetches source data based on metadata,
    calls appropriate compute functions, stores results.

    Args:
        conn: Database connection
        data_id: ID of the data entity (stock, market, etc.)
        function_names: Optional filter for specific function types
        feature_names: Optional filter for specific feature names
        incremental: If True, only compute new dates (default)
        full_refresh: If True, recompute all dates (overrides incremental)
        update_existing: If True, update existing rows on conflict

    Returns:
        Dict with results per function_name:
        {
            'indicator': {'inserted': 100, 'errors': []},
            'derivative': {'inserted': 50, 'errors': [...]},
            'summary': {'total_inserted': 150, 'total_errors': 0}
        }
    """
    # Ensure fresh resolution per run (cache still used within this call)
    _FUNCTION_CACHE.clear()
    _FUNCTION_CACHE_SOURCE.clear()

    results: Dict[str, Any] = {}
    total_inserted = 0
    total_errors: List[Dict[str, Any]] = []

    # Step 1: Read active feature definitions
    feature_defs = _fetch_feature_definitions(
        conn,
        function_names=function_names,
        feature_names=feature_names
    )

    if not feature_defs:
        return {
            'summary': {'total_inserted': 0, 'total_errors': 0}
        }

    # Step 2: Group by function_name
    grouped_by_function = _group_by_function_name(feature_defs)

    # Step 3: Process each function_name group
    for func_name, features in grouped_by_function.items():
        try:
            func_result = _process_function_group(
                conn,
                data_id,
                func_name,
                features,
                incremental=incremental and not full_refresh,
                update_existing=update_existing,
            )

            results[func_name] = func_result
            total_inserted += func_result.get('inserted', 0)
            total_errors.extend(func_result.get('errors', []))

        except Exception as exc:
            error = {
                'function_name': func_name,
                'error': str(exc),
                'feature_count': len(features),
            }
            results[func_name] = {'inserted': 0, 'errors': [error]}
            total_errors.append(error)

    # Summary
    results['summary'] = {
        'total_inserted': total_inserted,
        'total_errors': len(total_errors),
    }

    return results


def _fetch_feature_definitions(
    conn: psycopg.Connection,
    function_names: Optional[List[str]] = None,
    feature_names: Optional[List[str]] = None,
) -> List[Tuple]:
    """
    Fetch active feature definitions from database.

    Returns list of tuples:
    (id, name, function_name, params, source_table, source_column, store_table, store_column)
    """
    query = """
    SELECT
        id,
        name,
        function_name,
        params,
        source_table,
        source_column,
        store_table,
        store_column
    FROM feature_definitions
    WHERE active = TRUE
    """

    params = []

    if function_names:
        placeholders = ','.join(['%s'] * len(function_names))
        query += f" AND function_name IN ({placeholders})"
        params.extend(function_names)

    if feature_names:
        placeholders = ','.join(['%s'] * len(feature_names))
        query += f" AND name IN ({placeholders})"
        params.extend(feature_names)

    query += " ORDER BY function_name, name"

    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _group_by_function_name(feature_defs: List[Tuple]) -> Dict[str, List[Tuple]]:
    """
    Group feature definitions by function_name.

    Returns: {function_name: [feature_defs]}
    """
    grouped: Dict[str, List[Tuple]] = {}

    for feature_def in feature_defs:
        func_name = feature_def[2]  # function_name column
        grouped.setdefault(func_name, []).append(feature_def)

    return grouped


def _process_function_group(
    conn: psycopg.Connection,
    data_id: int,
    function_name: str,
    features: List[Tuple],
    incremental: bool,
    update_existing: bool,
) -> Dict[str, Any]:
    """
    Process all features for a given function_name.

    Returns: {'inserted': count, 'errors': [...]}
    """
    # Get compute function (DB overrides code registry)
    compute_func = _resolve_compute_function(conn, function_name)

    if not compute_func:
        return {
            'inserted': 0,
            'errors': [{
                'error': f'No compute function registered for {function_name}',
                'features': [f[1] for f in features]
            }]
        }

    # Determine latest date if incremental
    latest_date = None
    if incremental:
        latest_date = _get_latest_feature_date(conn, data_id, function_name)

    # Group by source_table for efficient data fetching
    grouped_by_source = _group_by_source(features)

    total_inserted = 0
    errors = []

    for source_key, source_features in grouped_by_source.items():
        try:
            # Fetch source data
            source_rows = _fetch_source_data(
                conn,
                data_id,
                source_key,
                source_features,
                start_date=latest_date,
            )

            if not source_rows:
                continue

            # Prepare compute specs from feature definitions
            compute_specs = [
                {
                    'name': f[1],  # feature name
                    'feature_id': f[0],  # feature id
                    **f[3],  # params (type, window, etc.)
                }
                for f in source_features
            ]

            # Call compute function
            try:
                computed_rows = compute_func(source_rows, compute_specs)

                if not computed_rows:
                    continue

                # Build feature_map for insert
                # Map output column names to feature IDs
                # Use params.column if specified, otherwise use feature name
                feature_map = {}
                for f in source_features:
                    feature_id = f[0]
                    feature_name = f[1]
                    params = f[3]  # params dict
                    # Use column name from params, or fall back to feature name
                    column_name = params.get('column', feature_name)
                    feature_map[column_name] = feature_id

                # Insert results
                inserted = insert_computed_features(
                    conn,
                    data_id=data_id,
                    rows=computed_rows,
                    feature_map=feature_map,
                    update_existing=update_existing,
                    batch_size=feature_batch_size,
                )

                total_inserted += inserted

            except Exception as exc:
                errors.append({
                    'error': str(exc),
                    'function_name': function_name,
                    'features': [f[1] for f in source_features],
                })

        except Exception as exc:
            errors.append({
                'error': str(exc),
                'source': source_key,
                'features': [f[1] for f in source_features],
            })

    return {
        'inserted': total_inserted,
        'errors': errors,
    }


def _resolve_compute_function(conn: psycopg.Connection, function_name: str) -> Optional[Callable]:
    """
    Resolve a compute function, preferring DB-registered functions over code registry.
    Cached to avoid repeated DB lookups and exec.
    """
    if function_name in _FUNCTION_CACHE:
        return _FUNCTION_CACHE[function_name]

    db_func = _load_db_function(conn, function_name)
    if db_func:
        fn, version = db_func
        if function_name in COMPUTE_FUNCTIONS:
            warnings.warn(f"Using DB function '{function_name}' (version {version}) overriding code registry")
        _FUNCTION_CACHE[function_name] = fn
        _FUNCTION_CACHE_SOURCE[function_name] = f"db:{version}" if version else "db"
        return fn

    code_func = COMPUTE_FUNCTIONS.get(function_name)
    if code_func:
        _FUNCTION_CACHE[function_name] = code_func
        _FUNCTION_CACHE_SOURCE[function_name] = "code"
        return code_func

    return None


def _load_db_function(conn: psycopg.Connection, function_name: str) -> Optional[Tuple[Callable, Optional[str]]]:
    """Load a compute function from feature_functions table if present."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT language, function_body, version
            FROM feature_functions
            WHERE enabled = TRUE AND status = 'active' AND name = %s
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1;
            """,
            (function_name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    language, body, version = row
    if language not in ("python_expr", "python"):
        warnings.warn(f"Ignoring feature_function '{function_name}' with unsupported language '{language}'")
        return None
    local_env: Dict[str, Any] = {}
    try:
        exec(body, {}, local_env)
    except Exception as exc:
        warnings.warn(f"Failed to exec feature_function '{function_name}': {exc}")
        return None
    fn = local_env.get("compute") or local_env.get(function_name)
    if not callable(fn):
        warnings.warn(f"feature_function '{function_name}' did not define a callable 'compute'")
        return None
    return _wrap_db_function(fn), version


def _wrap_db_function(fn: Callable) -> Callable:
    """
    Adapt a DB function to dispatcher signature (rows, specs).

    Supports two patterns:
    - existing dispatcher-style (rows, specs) -> list of row dicts
    - simple pandas-style functions: compute(df, **params) -> series/iterable
      In this case, we build rows per spec with column name from spec/feature.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if params:
        first, *rest = params
        if first.kind == inspect.Parameter.VAR_POSITIONAL:
            return fn
        if len(params) >= 2 and first.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            second = params[1]
            # Dispatcher-style signature rows/specs: pass through
            if first.name in ("rows", "source_rows") and second.name in ("specs", "features", "feature_specs"):
                return fn
            if second.name in ("specs", "features", "feature_specs"):
                return fn

    def adapter(rows: List[Dict[str, Any]], specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows or not specs:
            return []
        try:
            import pandas as pd
        except Exception as exc:
            warnings.warn(f"feature_function pandas import failed: {exc}")
            return []
        df = pd.DataFrame(rows)
        if df.empty:
            return []
        # Normalize column names
        df.columns = [str(c) for c in df.columns]
        if "date" not in df.columns:
            return []
        out: List[Dict[str, Any]] = []
        for spec in specs:
            # Strip dispatcher-specific keys
            params = {k: v for k, v in spec.items() if k not in ("name", "feature_id")}
            try:
                series = fn(df, **params)
            except Exception as exc:
                warnings.warn(f"feature_function '{fn.__name__}' execution failed: {exc}")
                continue
            if series is None:
                continue
            try:
                iterable = list(series)
            except Exception:
                continue
            col_name = spec.get("column") or spec.get("name")
            for d, v in zip(df["date"], iterable):
                out.append({"date": d, col_name: v, "source": "fx"})
        return out

    return adapter


def _get_latest_feature_date(
    conn: psycopg.Connection,
    data_id: int,
    function_name: str,
) -> Optional[date]:
    """Get latest date for features of this function_name."""
    query = """
    SELECT MAX(cf.date)
    FROM computed_features cf
    JOIN feature_definitions fd ON fd.id = cf.feature_id
    WHERE cf.data_id = %s
      AND fd.function_name = %s
      AND fd.active = TRUE
    """

    with conn.cursor() as cur:
        cur.execute(query, (data_id, function_name))
        row = cur.fetchone()
        return row[0] if row else None


def _group_by_source(features: List[Tuple]) -> Dict[Tuple[str, str], List[Tuple]]:
    """
    Group features by (source_table, source_column) for efficient fetching.

    Returns: {(source_table, source_column): [features]}
    """
    grouped: Dict[Tuple[str, str], List[Tuple]] = {}

    for feature in features:
        source_table = feature[4]
        source_column = feature[5]
        key = (source_table, source_column)
        grouped.setdefault(key, []).append(feature)

    return grouped


def _fetch_source_data(
    conn: psycopg.Connection,
    data_id: int,
    source_key: Tuple[str, str],
    features: List[Tuple],
    start_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch source data based on source_table and source_column.

    Handles different source types:
    - stock_ohlcv: Direct column access
    - computed_features: Requires feature_id lookup
    """
    source_table, source_column = source_key

    if source_table == 'computed_features':
        return _fetch_from_computed_features(
            conn, data_id, features, start_date
        )
    elif source_table == 'stock_ohlcv':
        return _fetch_from_stock_ohlcv(
            conn, data_id, source_column, start_date
        )
    else:
        # Generic table fetch
        return _fetch_from_generic_table(
            conn, data_id, source_table, source_column, start_date
        )


def _fetch_from_computed_features(
    conn: psycopg.Connection,
    data_id: int,
    features: List[Tuple],
    start_date: Optional[date],
) -> List[Dict[str, Any]]:
    """
    Fetch from computed_features table.

    For derivatives, params.source_feature specifies which feature to fetch.
    """
    # Extract source_feature from first feature's params
    # (all features in group should have same source)
    params = features[0][3]  # params dict
    source_feature_name = params.get('source_feature')

    if not source_feature_name:
        return []

    # Look up source feature_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM feature_definitions WHERE name = %s",
            (source_feature_name,)
        )
        row = cur.fetchone()

    if not row:
        return []

    source_feature_id = row[0]

    # Fetch computed feature values
    query = """
    SELECT date, value
    FROM computed_features
    WHERE data_id = %s AND feature_id = %s
    """
    query_params = [data_id, source_feature_id]

    if start_date:
        query += " AND date > %s"
        query_params.append(start_date)

    query += " ORDER BY date"

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    return [
        {'date': row[0], 'value': row[1]}
        for row in rows
    ]


def _fetch_from_stock_ohlcv(
    conn: psycopg.Connection,
    data_id: int,
    column: str,
    start_date: Optional[date],
) -> List[Dict[str, Any]]:
    """Fetch from stock_ohlcv table."""
    # For indicators, we need OHLC data
    # Column might be 'close', but we fetch all price columns
    query = """
    SELECT date, open, high, low, close, adjusted_close, volume
    FROM stock_ohlcv
    WHERE data_id = %s
    """
    query_params = [data_id]

    if start_date:
        query += " AND date > %s"
        query_params.append(start_date)

    query += " ORDER BY date"

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    return [
        {
            'date': row[0],
            'open': row[1],
            'high': row[2],
            'low': row[3],
            'close': row[4],
            'adjusted_close': row[5],
            'volume': row[6],
        }
        for row in rows
    ]


def _fetch_from_generic_table(
    conn: psycopg.Connection,
    data_id: int,
    table: str,
    column: str,
    start_date: Optional[date],
) -> List[Dict[str, Any]]:
    """Fetch from a generic table."""
    query = sql.SQL("""
    SELECT date, {column}
    FROM {table}
    WHERE data_id = %s
    """).format(
        table=sql.Identifier(table),
        column=sql.Identifier(column),
    )

    query_params = [data_id]

    if start_date:
        query = sql.SQL(str(query) + " AND date > %s")
        query_params.append(start_date)

    query = sql.SQL(str(query) + " ORDER BY date")

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    return [
        {'date': row[0], 'value': row[1]}
        for row in rows
    ]


# Register indicator compute function (will be imported)
def _register_default_functions():
    """Register default compute functions."""
    try:
        from g2.indicators.local import compute_indicators
        register_compute_function('indicator', compute_indicators)
    except ImportError:
        pass

    try:
        from g2.features.derivatives import compute_derivatives
        register_compute_function('derivative', compute_derivatives)
    except ImportError:
        pass


# Auto-register on import
_register_default_functions()
