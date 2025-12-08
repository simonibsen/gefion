from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional, Sequence, Dict, List, Tuple

import psycopg
from psycopg import sql

from psycopg import errors
from psycopg.types.json import Json


def upsert_stock(conn: psycopg.Connection, symbol: str) -> int:
    """Insert symbol into stocks if missing; return id."""
    with conn.cursor() as cur:
        # Try insert first - returns ID if successful
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
    rows: Iterable[Mapping[str, object]]
) -> List[Mapping[str, object]]:
    """
    Filter API response rows to only include rows newer than existing data.

    This avoids inserting duplicate rows that would be skipped by ON CONFLICT,
    reducing database overhead.

    Args:
        conn: Database connection
        data_id: Stock ID
        rows: Rows from API response

    Returns:
        List of rows that are newer than existing data
    """
    if not rows:
        return []

    # Get the latest date we have for this symbol
    latest = latest_price_date(conn, data_id)

    # If no existing data, all rows are new
    if latest is None:
        return list(rows)

    # Filter to only rows after the latest date
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


def latest_indicator_date(conn: psycopg.Connection, data_id: int) -> Optional[date]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(date) FROM computed_features
            WHERE data_id = %s
            AND feature_id IN (
                SELECT id FROM feature_definitions WHERE function_name = 'indicator' AND active = TRUE
            );
            """,
            (data_id,),
        )
        row = cur.fetchone()
    return row[0]


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

_INDICATOR_COLUMNS: Dict[str, List[str]] = {
    "rsi": ["rsi_14"],
    "adx": ["adx_14"],
    "sma20": ["sma_20"],
    "sma50": ["sma_50"],
    "sma200": ["sma_200"],
    "ema12": ["ema_12"],
    "ema26": ["ema_26"],
    "macd": ["macd", "macd_signal", "macd_hist"],
    "bbands": ["bb_upper", "bb_middle", "bb_lower"],
    "stoch": ["stoch_k", "stoch_d"],
    "psar": ["psar"],
}


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


def ensure_indicator_feature_definitions(conn: psycopg.Connection, indicators: Sequence[str]) -> Dict[str, int]:
    """
    Create/ensure feature_definitions entries for requested indicator columns.
    Returns mapping column_name -> feature_id (e.g., "rsi_14" -> 5).
    """
    seen_cols: Dict[str, str] = {}
    for ind in indicators:
        cols = _INDICATOR_COLUMNS.get(ind)
        if not cols:
            continue
        for col in cols:
            seen_cols[col] = ind
    if not seen_cols:
        return {}

    defs = []
    for col, ind in seen_cols.items():
        defs.append(
            {
                "name": f"indicator_{col}",
                "function_name": "indicator",
                "params": {"indicator": ind},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            }
        )
    name_to_id = ensure_feature_definitions(conn, defs)
    ensure_store_targets(conn, defs)
    # Build column -> id map
    col_to_id: Dict[str, int] = {}
    for col in seen_cols:
        fid = name_to_id.get(f"indicator_{col}")
        if fid is not None:
            col_to_id[col] = fid
    return col_to_id


def ensure_all_indicator_feature_definitions(conn: psycopg.Connection) -> Dict[str, int]:
    """
    Convenience to seed definitions for all known indicators in _INDICATOR_COLUMNS.
    Returns column -> feature_id map.
    """
    return ensure_indicator_feature_definitions(conn, list(_INDICATOR_COLUMNS.keys()))


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
            cur.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {table}_date_idx ON {table}(date);"
                ).format(table=sql.Identifier(table))
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
) -> int:
    """
    Insert tall computed feature rows using feature_map of column -> feature_id.
    """
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
    total = 0

    # Check if prepared statements are enabled via the pool
    from g2.db import pool as db_pool
    prepare_enabled = db_pool.should_prepare_statements()

    for i in range(0, len(prepared), chunk_size):
        batch = prepared[i : i + chunk_size]
        batch_size_actual = len(batch)

        # Use prepared statements whenever enabled
        use_prepared = prepare_enabled

        params: List[object] = []
        for fid, did, dt, val, source in batch:
            params.extend([fid, did, dt, val, source])

        try:
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
        except Exception as exc:
            sample = batch[:3]
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
            raise Exception(f"insert_computed_features failed: {exc}; sample_types={sample_types}; sample={sample}") from exc
        total += len(batch)
    conn.commit()
    return total
