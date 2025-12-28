from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional, Sequence, Dict, List, Tuple

import psycopg
from psycopg import sql

from psycopg import errors
from psycopg.types.json import Json

from g2.observability import create_span, set_attributes, get_current_span


def upsert_stock(conn: psycopg.Connection, symbol: str, status: Optional[str] = None) -> int:
    """
    Insert or update symbol in stocks table; return id.

    Args:
        conn: Database connection
        symbol: Stock symbol
        status: Optional status (e.g., 'Active', 'Inactive'). If None, status is not updated.

    Returns:
        Stock ID
    """
    with conn.cursor() as cur:
        if status is not None:
            # Insert with status, or update status on conflict
            cur.execute(
                """
                INSERT INTO stocks (symbol, status)
                VALUES (%s, %s)
                ON CONFLICT (symbol) DO UPDATE SET status = EXCLUDED.status
                RETURNING id
                """,
                (symbol, status),
            )
            return cur.fetchone()[0]
        else:
            # Insert without status (leaves it NULL), or do nothing on conflict
            cur.execute(
                "INSERT INTO stocks (symbol) VALUES (%s) ON CONFLICT (symbol) DO NOTHING RETURNING id",
                (symbol,),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            # If insert was skipped (conflict), fetch existing ID
            cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
            return cur.fetchone()[0]


def get_stocks_missing_fundamentals(conn: psycopg.Connection, limit: Optional[int] = None) -> List[Tuple[int, str]]:
    """
    Get stocks that are missing fundamentals data (sector is NULL).

    Args:
        conn: Database connection
        limit: Optional limit on number of results

    Returns:
        List of (id, symbol) tuples for stocks missing fundamentals
    """
    query = "SELECT id, symbol FROM stocks WHERE sector IS NULL ORDER BY id"
    if limit:
        query += f" LIMIT {limit}"

    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


def latest_price_date(conn: psycopg.Connection, data_id: int) -> Optional[date]:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM stock_ohlcv WHERE data_id = %s;", (data_id,))
        row = cur.fetchone()
    return row[0]


def filter_symbols_needing_update(
    conn: psycopg.Connection,
    symbols: Sequence[str],
    target_date: Optional[date] = None
) -> List[str]:
    """
    Filter out symbols that already have data up to the target date.

    Returns a list of symbols that need updates (either missing data or stale data).
    This is more efficient than checking each symbol individually in parallel workers.

    Args:
        conn: Database connection
        symbols: List of symbols to check
        target_date: Date to check against (defaults to today)

    Returns:
        List of symbols that need updates
    """
    if not symbols:
        return []

    if target_date is None:
        target_date = date.today()

    # Build query to get latest price date for each symbol
    # Use LEFT JOIN to include symbols that don't exist or have no prices
    # Exclude symbols with status='Inactive' (delisted/dead tickers)
    with conn.cursor() as cur:
        # Create a temporary table with the input symbols for efficient joining
        placeholders = ",".join(["%s"] * len(symbols))
        cur.execute(
            f"""
            WITH input_symbols AS (
                SELECT unnest(ARRAY[{placeholders}]) AS symbol
            )
            SELECT
                input_symbols.symbol,
                MAX(sp.date) AS latest_date
            FROM input_symbols
            LEFT JOIN stocks s ON s.symbol = input_symbols.symbol
            LEFT JOIN stock_ohlcv sp ON sp.data_id = s.id
            WHERE s.id IS NULL OR s.status IS DISTINCT FROM 'Inactive'
            GROUP BY input_symbols.symbol
            ORDER BY input_symbols.symbol
            """,
            list(symbols)
        )
        results = cur.fetchall()

    # Filter to only symbols that need updates
    symbols_needing_update = []
    for symbol, latest_date in results:
        # Need update if: no data exists OR data is stale
        if latest_date is None or latest_date < target_date:
            symbols_needing_update.append(symbol)

    return symbols_needing_update


def filter_new_rows(
    conn: psycopg.Connection,
    data_id: int,
    rows: Iterable[Mapping[str, object]],
    target_date: Optional[date] = None
) -> List[Mapping[str, object]]:
    """
    Filter API response rows to only include rows newer than existing data.

    This avoids inserting duplicate rows that would be skipped by ON CONFLICT,
    reducing database overhead. Also prevents inserting future dates beyond
    the expected target date (e.g., partial intraday data).

    Args:
        conn: Database connection
        data_id: Stock ID
        rows: Rows from API response
        target_date: Maximum date to allow (inclusive). Rows with dates after
                    this will be filtered out. Defaults to None (no limit).

    Returns:
        List of rows that are newer than existing data and not beyond target_date
    """
    if not rows:
        return []

    # Get the latest date we have for this symbol
    latest = latest_price_date(conn, data_id)

    # If no existing data, all rows are new (but still respect target_date)
    if latest is None:
        if target_date is None:
            return list(rows)
        # Filter to only rows up to target_date
        latest = date.min  # Use minimum date so all rows pass the > latest check

    # Filter to only rows after the latest date AND not beyond target_date
    new_rows = []
    for row in rows:
        row_date = row.get("date")

        # Handle different date formats
        if isinstance(row_date, date):
            parsed_date = row_date
        elif isinstance(row_date, str):
            try:
                from datetime import datetime
                parsed_date = datetime.fromisoformat(row_date).date()
            except Exception:
                # If we can't parse the date, include the row (let insert handle it)
                new_rows.append(row)
                continue
        else:
            # Unknown date format, include the row
            new_rows.append(row)
            continue

        # Only include rows newer than latest existing date
        if parsed_date > latest:
            # Also check target_date limit (prevent future/partial data)
            if target_date is None or parsed_date <= target_date:
                new_rows.append(row)

    return new_rows


def feature_ids_for_names(conn: psycopg.Connection, names: Sequence[str]) -> Dict[str, int]:
    """Resolve feature names to ids; returns mapping of found names."""
    placeholders = ",".join(["%s"] * len(names))
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT name, id FROM feature_definitions WHERE name IN ({placeholders});",
            list(names),
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


def trim_feature_data(
    conn: psycopg.Connection,
    feature_names: Sequence[str],
    before: Optional[date] = None,
    after: Optional[date] = None,
) -> int:
    """
    Trim computed_features rows for the given features.
    - before: drop rows strictly before this date (left trim)
    - after: drop rows strictly after this date (right trim)
    Returns count of rows deleted (best effort; exact count requires rowcount per stmt).
    """
    if not before and not after:
        return 0
    ids_map = feature_ids_for_names(conn, feature_names)
    if not ids_map:
        return 0
    ids = list(ids_map.values())
    placeholders = ",".join(["%s"] * len(ids))
    total_deleted = 0
    with conn.cursor() as cur:
        if before:
            cur.execute(
                f"DELETE FROM computed_features WHERE feature_id IN ({placeholders}) AND date < %s;",
                ids + [before],
            )
            total_deleted += cur.rowcount
        if after:
            cur.execute(
                f"DELETE FROM computed_features WHERE feature_id IN ({placeholders}) AND date > %s;",
                ids + [after],
            )
            total_deleted += cur.rowcount
    conn.commit()
    return total_deleted


def trim_stock_ohlcv(
    conn: psycopg.Connection,
    before: Optional[date] = None,
    after: Optional[date] = None,
    symbols: Optional[Sequence[str]] = None,
) -> int:
    """
    Trim stock_ohlcv rows by date.
    - before: drop rows strictly before this date
    - after: drop rows strictly after this date
    - symbols: optional list of symbols to restrict trimming
    """
    if not before and not after:
        return 0

    data_ids: Optional[List[int]] = None
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM stocks WHERE symbol IN ({placeholders});",
                list(symbols),
            )
            rows = cur.fetchall()
        data_ids = [r[0] for r in rows]
        if not data_ids:
            return 0

    total_deleted = 0
    with conn.cursor() as cur:
        where_ids = ""
        params: List[object] = []
        if data_ids is not None:
            where_ids = f" AND data_id IN ({','.join(['%s'] * len(data_ids))})"
            params.extend(data_ids)

        if before:
            cur.execute(
                f"DELETE FROM stock_ohlcv WHERE date < %s{where_ids};",
                [before, *params] if params else (before,),
            )
            total_deleted += cur.rowcount
        if after:
            cur.execute(
                f"DELETE FROM stock_ohlcv WHERE date > %s{where_ids};",
                [after, *params] if params else (after,),
            )
            total_deleted += cur.rowcount
    conn.commit()
    return total_deleted


def trim_all_computed_features(
    conn: psycopg.Connection,
    before: Optional[date] = None,
    after: Optional[date] = None,
    symbols: Optional[Sequence[str]] = None,
) -> int:
    """
    Trim ALL computed_features rows by date (optionally limited to symbols).
    - before: drop rows strictly before this date
    - after: drop rows strictly after this date
    - symbols: optional list of symbols to restrict trimming to specific stocks
    Returns count of rows deleted.
    """
    if not before and not after:
        return 0

    data_ids: Optional[List[int]] = None
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM stocks WHERE symbol IN ({placeholders});",
                list(symbols),
            )
            rows = cur.fetchall()
        data_ids = [r[0] for r in rows]
        if not data_ids:
            return 0

    total_deleted = 0
    with conn.cursor() as cur:
        where_ids = ""
        params: List[object] = []
        if data_ids is not None:
            where_ids = f" AND data_id IN ({','.join(['%s'] * len(data_ids))})"
            params.extend(data_ids)

        if before:
            cur.execute(
                f"DELETE FROM computed_features WHERE date < %s{where_ids};",
                [before, *params] if params else (before,),
            )
            total_deleted += cur.rowcount
        if after:
            cur.execute(
                f"DELETE FROM computed_features WHERE date > %s{where_ids};",
                [after, *params] if params else (after,),
            )
            total_deleted += cur.rowcount
    conn.commit()
    return total_deleted


def drop_features(
    conn: psycopg.Connection,
    feature_names: Sequence[str],
) -> Dict[str, int]:
    """
    Remove feature definitions and drop their storage tables/columns when custom.
    Drops rows from computed_features for tall storage; drops custom store tables
    when store_table != computed_features.
    Returns dict with counts of deleted rows per feature (for tall storage).
    """
    if not feature_names:
        return {}
    ids_map = feature_ids_for_names(conn, feature_names)
    if not ids_map:
        return {}
    # Fetch store targets
    placeholders = ",".join(["%s"] * len(ids_map))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT name, store_table, store_column FROM feature_definitions
            WHERE id IN ({placeholders});
            """,
            list(ids_map.values()),
        )
        targets = cur.fetchall()
    deleted_counts: Dict[str, int] = {}
    with conn.cursor() as cur:
        # Delete tall storage rows
        cur.execute(
            f"DELETE FROM computed_features WHERE feature_id IN ({placeholders});",
            list(ids_map.values()),
        )
        deleted = cur.rowcount
        for name in ids_map:
            deleted_counts[name] = deleted
        # Drop storage tables (non computed_features)
        for name, table, _col in targets:
            if table and table != "computed_features":
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(sql.Identifier(table)))
        # Delete definitions
        cur.execute(
            f"DELETE FROM feature_definitions WHERE id IN ({placeholders});",
            list(ids_map.values()),
        )
    conn.commit()
    return deleted_counts


def delete_feature_data_only(
    conn: psycopg.Connection,
    feature_names: Sequence[str],
) -> Dict[str, int]:
    """
    Remove stored data for the given features without dropping schema/definitions.
    - Deletes rows from computed_features for the feature_ids.
    - For custom store tables (store_table != computed_features), deletes all rows.
    Returns dict with counts of deleted rows per feature (tall storage only).
    """
    if not feature_names:
        return {}
    ids_map = feature_ids_for_names(conn, feature_names)
    if not ids_map:
        return {}
    placeholders = ",".join(["%s"] * len(ids_map))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT name, store_table, store_column FROM feature_definitions
            WHERE id IN ({placeholders});
            """,
            list(ids_map.values()),
        )
        targets = cur.fetchall()

    deleted_counts: Dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM computed_features WHERE feature_id IN ({placeholders});",
            list(ids_map.values()),
        )
        deleted = cur.rowcount
        for name in ids_map:
            deleted_counts[name] = deleted
        for name, table, _col in targets:
            if table and table != "computed_features":
                cur.execute(sql.SQL("DELETE FROM {};").format(sql.Identifier(table)))
    conn.commit()
    return deleted_counts


def latest_feature_date(
    conn: psycopg.Connection,
    data_id: int,
    function_name: Optional[str] = None
) -> Optional[date]:
    """
    Get the latest date for computed features for a given data_id.

    Args:
        conn: Database connection
        data_id: Stock data_id
        function_name: Optional function_name to filter by (e.g., 'indicator', 'derivative')
                      If None, returns latest date across all active features

    Returns:
        Latest date or None if no data exists
    """
    with conn.cursor() as cur:
        if function_name:
            cur.execute(
                """
                SELECT MAX(date) FROM computed_features
                WHERE data_id = %s
                AND feature_id IN (
                    SELECT id FROM feature_definitions WHERE function_name = %s AND active = TRUE
                );
                """,
                (data_id, function_name),
            )
        else:
            cur.execute(
                """
                SELECT MAX(date) FROM computed_features
                WHERE data_id = %s
                AND feature_id IN (
                    SELECT id FROM feature_definitions WHERE active = TRUE
                );
                """,
                (data_id,),
            )
        row = cur.fetchone()
    return row[0]


def filter_symbols_needing_features(
    conn: psycopg.Connection,
    symbols: Sequence[str],
    target_date: Optional[date] = None,
    function_name: Optional[str] = None
) -> List[str]:
    """
    Filter out symbols that already have up-to-date feature data.

    Args:
        conn: Database connection
        symbols: List of stock symbols to check
        target_date: Target date for up-to-date check (defaults to today)
        function_name: Optional function_name to filter by (e.g., 'indicator', 'derivative')
                      If None, checks all active features

    Returns:
        List of symbols that need feature computation (either missing data or stale data)
    """
    from datetime import date as date_type
    if target_date is None:
        target_date = date_type.today()

    if not symbols:
        return []

    with conn.cursor() as cur:
        if function_name:
            # Query to find symbols with stale or missing feature data for specific function
            cur.execute(
                """
                WITH symbol_ids AS (
                    SELECT s.id, s.symbol
                    FROM stocks s
                    WHERE s.symbol = ANY(%s)
                      AND s.status IS DISTINCT FROM 'Inactive'
                ),
                latest_features AS (
                    SELECT
                        cf.data_id,
                        MAX(cf.date) as latest_date
                    FROM computed_features cf
                    WHERE cf.data_id IN (SELECT id FROM symbol_ids)
                      AND cf.feature_id IN (
                          SELECT id FROM feature_definitions
                          WHERE function_name = %s AND active = TRUE
                      )
                    GROUP BY cf.data_id
                )
                SELECT s.symbol
                FROM symbol_ids s
                LEFT JOIN latest_features lf ON s.id = lf.data_id
                WHERE lf.latest_date IS NULL
                   OR lf.latest_date < %s;
                """,
                (symbols, function_name, target_date)
            )
        else:
            # Query to find symbols with stale or missing feature data for any active features
            cur.execute(
                """
                WITH symbol_ids AS (
                    SELECT s.id, s.symbol
                    FROM stocks s
                    WHERE s.symbol = ANY(%s)
                      AND s.status IS DISTINCT FROM 'Inactive'
                ),
                latest_features AS (
                    SELECT
                        cf.data_id,
                        MAX(cf.date) as latest_date
                    FROM computed_features cf
                    WHERE cf.data_id IN (SELECT id FROM symbol_ids)
                      AND cf.feature_id IN (
                          SELECT id FROM feature_definitions WHERE active = TRUE
                      )
                    GROUP BY cf.data_id
                )
                SELECT s.symbol
                FROM symbol_ids s
                LEFT JOIN latest_features lf ON s.id = lf.data_id
                WHERE lf.latest_date IS NULL
                   OR lf.latest_date < %s;
                """,
                (symbols, target_date)
            )
        needs_update = [row[0] for row in cur.fetchall()]

    return needs_update


def latest_indicator_date(conn: psycopg.Connection, data_id: int) -> Optional[date]:
    """DEPRECATED: Use latest_feature_date(conn, data_id, function_name='indicator') instead."""
    return latest_feature_date(conn, data_id, function_name='indicator')


def filter_symbols_needing_indicators(
    conn: psycopg.Connection,
    symbols: Sequence[str],
    target_date: Optional[date] = None
) -> List[str]:
    """DEPRECATED: Use filter_symbols_needing_features(conn, symbols, target_date, function_name='indicator') instead."""
    return filter_symbols_needing_features(conn, symbols, target_date, function_name='indicator')



def decide_outputsize(conn: psycopg.Connection, data_id: int, timeframe: str = "auto") -> str:
    """
    Decide AlphaVantage outputsize based on existing data.

    - If timeframe is explicit ('compact'/'full'), return it.
    - If no data exists, return 'full'.
    - If most recent data is older than 100 days, return 'full', else 'compact'.
    """
    if timeframe in {"compact", "full"}:
        return timeframe
    latest = latest_price_date(conn, data_id)
    if latest is None:
        return "full"
    delta = (date.today() - latest).days
    if delta > 100:
        return "full"
    return "compact"


def insert_stock_ohlcv(
    conn: psycopg.Connection,
    data_id: int,
    rows: Iterable[Mapping[str, object]],
    update_existing: bool = False,
) -> int:
    """
    Insert parsed price rows using efficient batch operations.

    This implementation uses multi-row VALUES clauses to minimize round trips
    and database overhead, resulting in 10-100x performance improvement over
    row-by-row inserts.
    """
    # Convert to list to allow multiple passes
    rows_list = list(rows)
    if not rows_list:
        return 0

    def safe_num(val):
        """Sanitize numeric values to prevent overflow."""
        if val is None:
            return None
        try:
            f = float(val)
            if abs(f) >= 1e12:
                return None
            return val
        except Exception:
            return None

    # Prepare data with validation
    prepared = []
    for row in rows_list:
        open_v = safe_num(row.get("open"))
        high_v = safe_num(row.get("high"))
        low_v = safe_num(row.get("low"))
        close_v = safe_num(row.get("close"))
        adj_v = safe_num(row.get("adjusted_close"))
        dividend_v = safe_num(row.get("dividend_amount"))
        split_v = safe_num(row.get("split_coefficient"))

        # Skip rows with all NULL price values
        if all(v is None for v in [open_v, high_v, low_v, close_v, adj_v]):
            continue

        prepared.append((
            data_id,
            row["date"],
            open_v,
            high_v,
            low_v,
            close_v,
            adj_v,
            dividend_v,
            split_v,
            safe_num(row.get("volume")),
            row.get("source", "alphavantage"),
        ))

    if not prepared:
        return 0

    # Batch insert in chunks of 200 to avoid parameter limits
    chunk_size = 200
    total_inserted = 0

    conflict_clause = (
        "ON CONFLICT (data_id, date) DO UPDATE SET "
        "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
        "close = EXCLUDED.close, adjusted_close = EXCLUDED.adjusted_close, "
        "dividend_amount = EXCLUDED.dividend_amount, split_coefficient = EXCLUDED.split_coefficient, "
        "volume = EXCLUDED.volume, source = EXCLUDED.source"
        if update_existing
        else "ON CONFLICT (data_id, date) DO NOTHING"
    )

    with conn.cursor() as cur:
        for i in range(0, len(prepared), chunk_size):
            batch = prepared[i : i + chunk_size]

            # Build multi-row VALUES clause
            values_placeholders = []
            params = []
            for row_data in batch:
                values_placeholders.append("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
                params.extend(row_data)

            sql_stmt = (
                "INSERT INTO stock_ohlcv "
                "(data_id, date, open, high, low, close, adjusted_close, dividend_amount, split_coefficient, volume, source) "
                "VALUES " + ",".join(values_placeholders) + " " + conflict_clause
            )

            cur.execute(sql_stmt, params)
            # Use rowcount to get ACTUAL inserts (excludes ON CONFLICT skipped rows)
            total_inserted += cur.rowcount

    conn.commit()
    return total_inserted




# --- Feature store helpers (computed_features / feature_definitions) ---

def ensure_feature_definitions(
    conn: psycopg.Connection, defs: Sequence[Mapping[str, object]]
) -> Dict[str, int]:
    """
    Upsert feature_definitions rows; return map name -> id.
    Each def expects keys: name, function_name, params, source_table, source_column, store_table, store_column, store_type.
    """
    ids: Dict[str, int] = {}
    with conn.cursor() as cur:
        for d in defs:
            payload = dict(d)
            # Default tall store column to "value"
            if payload.get("store_table") == "computed_features" and not payload.get("store_column"):
                payload["store_column"] = "value"
            if payload.get("params") is not None:
                payload["params"] = Json(payload["params"])
            cur.execute(
                """
                INSERT INTO feature_definitions
                (name, function_name, params, source_table, source_column, store_table, store_column, store_type, active)
                VALUES (%(name)s, %(function_name)s, %(params)s, %(source_table)s, %(source_column)s,
                        %(store_table)s, %(store_column)s, %(store_type)s, %(active)s)
                ON CONFLICT (name) DO UPDATE SET
                    function_name = EXCLUDED.function_name,
                    params = EXCLUDED.params,
                    source_table = EXCLUDED.source_table,
                    source_column = EXCLUDED.source_column,
                    store_table = EXCLUDED.store_table,
                    store_column = EXCLUDED.store_column,
                    store_type = EXCLUDED.store_type,
                    active = EXCLUDED.active
                RETURNING id;
                """,
                payload,
            )
            ids[payload["name"]] = cur.fetchone()[0]
    conn.commit()
    return ids


def load_feature_definitions_from_json(path: str) -> List[Dict[str, object]]:
    """
    Load feature definitions from JSON file(s).

    Args:
        path: Path to a JSON file or directory containing JSON files

    Returns:
        List of feature definition dicts

    Raises:
        ValueError: If required fields are missing
        json.JSONDecodeError: If JSON is malformed
    """
    import json
    from pathlib import Path as PathlibPath

    path_obj = PathlibPath(path)
    definitions: List[Dict[str, object]] = []

    if path_obj.is_file():
        # Load single file
        with open(path_obj) as f:
            data = json.load(f)
            _validate_feature_definition(data)
            definitions.append(data)
    elif path_obj.is_dir():
        # Load all JSON files in directory
        for json_file in sorted(path_obj.glob("*.json")):
            with open(json_file) as f:
                data = json.load(f)
                _validate_feature_definition(data)
                definitions.append(data)
    else:
        raise ValueError(f"Path does not exist: {path}")

    return definitions


def _validate_feature_definition(definition: Dict[str, object]) -> None:
    """Validate that a feature definition has all required fields."""
    required_fields = [
        "name",
        "function_name",
        "source_table",
        "source_column",
        "store_table",
        "store_column",
        "store_type",
        "active"
    ]

    for field in required_fields:
        if field not in definition:
            raise ValueError(f"Missing required field '{field}' in feature definition: {definition.get('name', 'unknown')}")


def ensure_store_targets(conn: psycopg.Connection, defs: Sequence[Mapping[str, object]]) -> None:
    """
    Ensure the target store_table/store_column exists for the given feature defs.
    - computed_features: ensure hypertable exists
    - other tables: create a simple table if missing with (data_id, date, <col>, source)
    """
    for d in defs:
        table = d.get("store_table") or "computed_features"
        column = d.get("store_column")
        store_type = d.get("store_type", "double precision")
        if not column:
            continue
        if table == "computed_features":
            from g2.db import schema  # local import to avoid cycles
            schema.create_computed_features_table(conn)
            continue
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                        date DATE NOT NULL,
                        {col} {coltype},
                        source TEXT,
                        PRIMARY KEY (data_id, date)
                    );
                    """
                ).format(
                    table=sql.Identifier(table),
                    col=sql.Identifier(column),
                    coltype=sql.SQL(store_type),
                )
            )
            # Create index with properly constructed name
            index_name = f"{table}_date_idx"
            cur.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index} ON {table}(date);"
                ).format(
                    index=sql.Identifier(index_name),
                    table=sql.Identifier(table)
                )
            )
    conn.commit()


def insert_computed_features(
    conn: psycopg.Connection,
    data_id: int,
    rows: Iterable[Mapping[str, object]],
    feature_map: Mapping[str, int],
    update_existing: bool = False,
    skip_before: Optional[date] = None,
    batch_size: int = 200,
    sync_commit: bool = False,
) -> int:
    """
    Insert tall computed feature rows using feature_map of column -> feature_id.
    """
    with create_span(
        "insert_computed_features",
        data_id=data_id,
        feature_count=len(feature_map),
        batch_size=batch_size,
        update_existing=update_existing,
        sync_commit=sync_commit
    ):
        result = _insert_computed_features_impl(
            conn, data_id, rows, feature_map, update_existing, skip_before, batch_size, sync_commit
        )
        current_span = get_current_span()
        set_attributes(current_span, rows_inserted=result)
        return result


def _insert_computed_features_impl(
    conn: psycopg.Connection,
    data_id: int,
    rows: Iterable[Mapping[str, object]],
    feature_map: Mapping[str, int],
    update_existing: bool,
    skip_before: Optional[date],
    batch_size: int,
    sync_commit: bool,
) -> int:
    """Internal implementation of insert_computed_features."""
    def to_date(val):
        if val is None:
            return None
        if isinstance(val, date):
            return val
        if hasattr(val, "date"):
            try:
                return val.date()
            except Exception:
                pass
        try:
            return date.fromisoformat(str(val))
        except Exception:
            return None

    def safe_num(val):
        if val is None:
            return None
        try:
            f = float(val)
            if abs(f) >= 1e12:
                return None
            return f
        except Exception:
            return None

    prepared: List[Tuple[int, int, date, float, str]] = []
    try:
        data_id_int = int(data_id)
    except Exception:
        return 0
    for r in rows:
        d = to_date(r.get("date"))
        if d is None:
            continue
        if skip_before and d <= skip_before:
            continue
        for col, fid in feature_map.items():
            val = safe_num(r.get(col))
            if val is None:
                continue
            try:
                prepared.append((int(fid), data_id_int, d, val, r.get("source", "alphavantage")))
            except Exception:
                continue

    if not prepared:
        return 0

    chunk_size = max(1, batch_size)

    # Check if prepared statements are enabled via the pool
    from g2.db import pool as db_pool
    prepare_enabled = db_pool.should_prepare_statements()

    # Helper function to perform the actual insert
    def do_insert() -> int:
        """Perform the batch insert operation."""
        total = 0
        sync_commit_changed = False
        try:
            # Disable synchronous_commit for the entire operation if requested
            if not sync_commit:
                with conn.cursor() as cur:
                    cur.execute("SET synchronous_commit TO OFF;")
                sync_commit_changed = True

            for i in range(0, len(prepared), chunk_size):
                batch = prepared[i : i + chunk_size]
                batch_size_actual = len(batch)

                # Use prepared statements whenever enabled
                use_prepared = prepare_enabled

                params: List[object] = []
                for fid, did, dt, val, source in batch:
                    params.extend([fid, did, dt, val, source])

                # Build the SQL statement
                conflict = (
                    "ON CONFLICT (feature_id, data_id, date) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source"
                    if update_existing
                    else "ON CONFLICT (feature_id, data_id, date) DO NOTHING"
                )
                values_sql = ",".join(["(%s::int, %s::int, %s::date, %s::double precision, %s::text)"] * batch_size_actual)
                stmt = (
                    "INSERT INTO computed_features (feature_id, data_id, date, value, source) VALUES "
                    + values_sql
                    + " "
                    + conflict
                )

                with conn.cursor() as cur:
                    # Use psycopg3's automatic prepared statement caching for common batch sizes
                    # This reduces parsing overhead by 10-30%
                    if use_prepared:
                        cur.execute(stmt, params, prepare=True)
                    else:
                        cur.execute(stmt, params)

                total += len(batch)
            conn.commit()
            return total
        finally:
            # Reset synchronous_commit back to default if we changed it
            if sync_commit_changed:
                try:
                    with conn.cursor() as cur:
                        cur.execute("RESET synchronous_commit;")
                except Exception:
                    # Ignore errors during reset (connection might be closed)
                    pass

    # OPTIMISTIC INSERT: Try insert first, create chunks only if needed
    try:
        # Fast path: try insert without chunk creation (works 99.9% of the time)
        return do_insert()

    except Exception as exc:
        # Check if this is a chunk-not-found error
        error_msg = str(exc).lower()
        is_chunk_error = (
            "no chunks found" in error_msg or
            "could not find chunk" in error_msg or
            "chunk not found" in error_msg or
            "insert or update on table" in error_msg and "violates" not in error_msg
        )

        if is_chunk_error:
            # Slow path (rare): create missing chunks and retry
            import warnings
            from g2.utils.timescale import ensure_chunks_for_date_range
            from datetime import timedelta

            # Find date range
            dates = [dt for _, _, dt, _, _ in prepared]
            min_date = min(dates)
            max_date = max(dates)
            buffer = timedelta(days=1)

            warnings.warn(
                f"Chunk not found for date range {min_date} to {max_date}. "
                f"Auto-creating chunks (this is a one-time operation)."
            )

            try:
                # Use separate connection to avoid deadlock with autocommit writers
                # Autocommit mode can cause circular lock dependencies when multiple
                # workers try to create chunks simultaneously
                with psycopg.connect(conn.info.dsn) as chunk_conn:
                    chunk_conn.autocommit = False  # Transaction mode prevents lock conflicts
                    ensure_chunks_for_date_range(
                        chunk_conn,
                        "computed_features",
                        min_date - buffer,
                        max_date + buffer,
                        chunk_interval_days=30
                    )
            except Exception as chunk_err:
                raise Exception(
                    f"Failed to create chunks for {min_date} to {max_date}: {chunk_err}"
                ) from exc

            # Retry insert after creating chunks
            try:
                return do_insert()
            except Exception as retry_exc:
                raise Exception(
                    f"Insert failed even after creating chunks: {retry_exc}"
                ) from exc
        else:
            # Different error - format with debug info and re-raise
            sample = prepared[:3]
            sample_types = [
                {
                    "feature_id": type(x[0]).__name__,
                    "data_id": type(x[1]).__name__,
                    "date": type(x[2]).__name__,
                    "value": type(x[3]).__name__,
                    "source": type(x[4]).__name__,
                }
                for x in sample
            ]
            raise Exception(
                f"insert_computed_features failed: {exc}; sample_types={sample_types}; sample={sample}"
            ) from exc
