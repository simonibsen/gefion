"""Cascading data cull — delete old data in dependency order (leaf → root)."""
import logging
from collections import OrderedDict
from datetime import date
from typing import Dict, List, Optional

from psycopg import Connection

from gefion.observability import create_span, set_attributes, add_event

logger = logging.getLogger(__name__)

# Deletion order: leaf tables first, root last.
# Each entry: (table_name, date_column, has_symbol_filter)
# Tables without a date column are handled by orphan detection.
CULL_ORDER: List[tuple] = [
    # Leaf: predictions and outcomes (date-filterable)
    ("predictions", "prediction_date", True),
    ("prediction_outcomes", "prediction_date", True),
    # Orphaned model performance (models with no remaining predictions)
    ("model_performance", None, False),
    # Orphaned ML models (must delete before ml_runs due to train_run_id FK)
    ("ml_models", None, False),
    # Orphaned ML runs (no predictions or outcomes reference them)
    ("ml_runs", None, False),
    # Orphaned ML datasets (no models reference them)
    ("ml_datasets", None, False),
    # Features (date-filterable)
    ("computed_features", "date", True),
    # OHLCV data (root, date-filterable)
    ("stock_ohlcv", "date", True),
]


def _symbol_ids(cur, symbols: List[str]) -> List[int]:
    """Resolve symbol names to stock IDs."""
    cur.execute(
        "SELECT id FROM stocks WHERE symbol = ANY(%s)", (symbols,)
    )
    return [row[0] for row in cur.fetchall()]


def _count_date_filtered(
    cur, table: str, date_col: str, before_date: date,
    data_ids: Optional[List[int]] = None,
) -> int:
    """Count rows in a date-partitioned table before the cutoff."""
    if data_ids is not None:
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {date_col} < %s AND data_id = ANY(%s)",
            (before_date, data_ids),
        )
    else:
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {date_col} < %s",
            (before_date,),
        )
    return cur.fetchone()[0]


def _count_orphaned(cur, table: str) -> int:
    """Count orphaned rows in ML registry tables."""
    if table == "model_performance":
        cur.execute(
            "SELECT COUNT(*) FROM model_performance mp "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.model_id = mp.model_id)"
        )
    elif table == "ml_runs":
        cur.execute(
            "SELECT COUNT(*) FROM ml_runs r "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.run_id = r.id) "
            "AND NOT EXISTS (SELECT 1 FROM prediction_outcomes po WHERE po.run_id = r.id) "
            "AND NOT EXISTS (SELECT 1 FROM ml_models m WHERE m.train_run_id = r.id)"
        )
    elif table == "ml_models":
        cur.execute(
            "SELECT COUNT(*) FROM ml_models m "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.model_id = m.id)"
        )
    elif table == "ml_datasets":
        cur.execute(
            "SELECT COUNT(*) FROM ml_datasets d "
            "WHERE NOT EXISTS (SELECT 1 FROM ml_models m WHERE m.dataset_id = d.id)"
        )
    else:
        return 0
    return cur.fetchone()[0]


def _table_exists(cur, table: str) -> bool:
    """Check if a table exists in the database."""
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def plan_cull(
    conn: Connection,
    before_date: date,
    symbols: Optional[List[str]] = None,
    on_progress: Optional[callable] = None,
) -> Dict[str, int]:
    """Dry-run: return {table_name: row_count_to_delete} in dependency order.

    Args:
        on_progress: Optional callback(table, count, step, total_steps) called
                     after each table is scanned.
    """
    with create_span("db.cull.plan", before_date=str(before_date)) as span:
        result: Dict[str, int] = OrderedDict()
        data_ids = None
        total_steps = len(CULL_ORDER)

        with conn.cursor() as cur:
            if symbols:
                data_ids = _symbol_ids(cur, symbols)
                if not data_ids:
                    set_attributes(span, total_rows=0)
                    return result

            for step, (table, date_col, has_symbol) in enumerate(CULL_ORDER, 1):
                if not _table_exists(cur, table):
                    if on_progress:
                        on_progress(table, 0, step, total_steps)
                    continue

                if date_col is not None:
                    sym_ids = data_ids if (has_symbol and data_ids is not None) else None
                    count = _count_date_filtered(cur, table, date_col, before_date, sym_ids)
                else:
                    count = _count_orphaned(cur, table)

                if count > 0:
                    result[table] = count

                if on_progress:
                    on_progress(table, count, step, total_steps)

        set_attributes(span, total_rows=sum(result.values()))
        return result


def _delete_date_filtered(
    cur, table: str, date_col: str, before_date: date,
    data_ids: Optional[List[int]] = None,
) -> int:
    """Delete rows in a date-partitioned table before the cutoff."""
    if data_ids is not None:
        cur.execute(
            f"DELETE FROM {table} WHERE {date_col} < %s AND data_id = ANY(%s)",
            (before_date, data_ids),
        )
    else:
        cur.execute(
            f"DELETE FROM {table} WHERE {date_col} < %s",
            (before_date,),
        )
    return cur.rowcount


def _delete_orphaned(cur, table: str) -> int:
    """Delete orphaned rows from ML registry tables."""
    if table == "model_performance":
        cur.execute(
            "DELETE FROM model_performance mp "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.model_id = mp.model_id)"
        )
    elif table == "ml_runs":
        cur.execute(
            "DELETE FROM ml_runs r "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.run_id = r.id) "
            "AND NOT EXISTS (SELECT 1 FROM prediction_outcomes po WHERE po.run_id = r.id) "
            "AND NOT EXISTS (SELECT 1 FROM ml_models m WHERE m.train_run_id = r.id)"
        )
    elif table == "ml_models":
        cur.execute(
            "DELETE FROM ml_models m "
            "WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.model_id = m.id)"
        )
    elif table == "ml_datasets":
        cur.execute(
            "DELETE FROM ml_datasets d "
            "WHERE NOT EXISTS (SELECT 1 FROM ml_models m WHERE m.dataset_id = d.id)"
        )
    else:
        return 0
    return cur.rowcount


def vacuum_after_cull(
    conn: Connection,
    affected_tables: Dict[str, int],
) -> None:
    """VACUUM ANALYZE each table that had rows deleted.

    A global ``VACUUM ANALYZE`` does not reliably update
    ``pg_stat_user_tables.n_live_tup`` for TimescaleDB hypertable chunks.
    Targeting each table individually ensures the stats are refreshed so
    approximate row-count queries return correct values.
    """
    if not affected_tables:
        return

    with create_span("db.cull.vacuum", table_count=len(affected_tables)) as span:
        conn.autocommit = True
        with conn.cursor() as cur:
            for table_name in affected_tables:
                cur.execute(f"VACUUM ANALYZE {table_name}")
                add_event(span, f"vacuumed_{table_name}")


def execute_cull(
    conn: Connection,
    before_date: date,
    symbols: Optional[List[str]] = None,
    on_progress: Optional[callable] = None,
) -> Dict[str, int]:
    """Execute the cull, deleting in dependency order. Returns {table: rows_deleted}.

    Args:
        on_progress: Optional callback(table, deleted, step, total_steps) called
                     after each table is processed.
    """
    with create_span("db.cull.execute", before_date=str(before_date)) as span:
        result: Dict[str, int] = OrderedDict()
        data_ids = None
        total_steps = len(CULL_ORDER)

        with conn.cursor() as cur:
            if symbols:
                data_ids = _symbol_ids(cur, symbols)
                if not data_ids:
                    set_attributes(span, total_deleted=0)
                    return result

            for step, (table, date_col, has_symbol) in enumerate(CULL_ORDER, 1):
                if not _table_exists(cur, table):
                    if on_progress:
                        on_progress(table, 0, step, total_steps)
                    continue

                if date_col is not None:
                    sym_ids = data_ids if (has_symbol and data_ids is not None) else None
                    deleted = _delete_date_filtered(cur, table, date_col, before_date, sym_ids)
                else:
                    deleted = _delete_orphaned(cur, table)

                if deleted > 0:
                    result[table] = deleted
                    add_event(span, f"deleted_{table}", count=deleted)
                    logger.info("Culled %d rows from %s (before %s)", deleted, table, before_date)

                if on_progress:
                    on_progress(table, deleted, step, total_steps)

        conn.commit()
        set_attributes(span, total_deleted=sum(result.values()))
        return result
