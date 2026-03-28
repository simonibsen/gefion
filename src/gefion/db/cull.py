"""Cascading data cull — delete old data in dependency order (leaf → root)."""
import logging
from collections import OrderedDict
from datetime import date
from typing import Dict, List, Optional

from psycopg import Connection

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
    # Orphaned ML runs (no predictions or outcomes reference them)
    ("ml_runs", None, False),
    # Orphaned ML models (no predictions reference them)
    ("ml_models", None, False),
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
            "AND NOT EXISTS (SELECT 1 FROM prediction_outcomes po WHERE po.run_id = r.id)"
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
) -> Dict[str, int]:
    """Dry-run: return {table_name: row_count_to_delete} in dependency order."""
    result: Dict[str, int] = OrderedDict()
    data_ids = None

    with conn.cursor() as cur:
        if symbols:
            data_ids = _symbol_ids(cur, symbols)
            if not data_ids:
                return result

        for table, date_col, has_symbol in CULL_ORDER:
            if not _table_exists(cur, table):
                continue

            if date_col is not None:
                sym_ids = data_ids if (has_symbol and data_ids is not None) else None
                count = _count_date_filtered(cur, table, date_col, before_date, sym_ids)
            else:
                # Orphan detection — only after date-filtered deletes would happen
                # In plan mode we report current orphan count (conservative estimate)
                count = _count_orphaned(cur, table)

            if count > 0:
                result[table] = count

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
            "AND NOT EXISTS (SELECT 1 FROM prediction_outcomes po WHERE po.run_id = r.id)"
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


def execute_cull(
    conn: Connection,
    before_date: date,
    symbols: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Execute the cull, deleting in dependency order. Returns {table: rows_deleted}."""
    result: Dict[str, int] = OrderedDict()
    data_ids = None

    with conn.cursor() as cur:
        if symbols:
            data_ids = _symbol_ids(cur, symbols)
            if not data_ids:
                return result

        for table, date_col, has_symbol in CULL_ORDER:
            if not _table_exists(cur, table):
                continue

            if date_col is not None:
                sym_ids = data_ids if (has_symbol and data_ids is not None) else None
                deleted = _delete_date_filtered(cur, table, date_col, before_date, sym_ids)
            else:
                deleted = _delete_orphaned(cur, table)

            if deleted > 0:
                result[table] = deleted
                logger.info("Culled %d rows from %s (before %s)", deleted, table, before_date)

    conn.commit()
    return result
