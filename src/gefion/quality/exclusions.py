"""Consumer-exclusion helper (008, T013 — US2).

The single place that answers "is this stored value convicted trash?" —
consumers apply it at the point they read, so exclusion lives in one place
rather than as distributed vigilance (the exact failure mode 008 fixes).

A value is excluded when an UNRESOLVED trash finding exists for its
(entity, metric, date), mapped from the physical (table, column) through the
catalog. Resolved findings and suspect-tier findings never exclude.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Set, Tuple

from gefion.observability import create_span, set_attributes
from gefion.quality import catalog as _catalog


def _metrics_for_column(cat, table: str, column: str):
    return [name for name, m in cat.metrics.items()
            if m.table == table and m.column == column]


def convicted_dates(conn, table: str, entity_id: int, column: str,
                    cat=None) -> Set[date]:
    """Dates on which (entity_id, column) carries an unresolved trash verdict."""
    cat = cat or _catalog.load_default()
    metrics = _metrics_for_column(cat, table, column)
    if not metrics:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date FROM data_quality_findings
               WHERE entity_id = %s AND verdict = 'trash'
                 AND resolved_at IS NULL AND metric = ANY(%s)""",
            (entity_id, metrics),
        )
        return {row[0] for row in cur.fetchall()}


def convicted_map(conn, table: str, cat=None) -> Dict[Tuple[int, str, date], None]:
    """The full (entity_id, column, date) exclusion set for a table — for bulk
    consumers that filter a whole read rather than one entity's series."""
    cat = cat or _catalog.load_default()
    metric_to_col = {name: m.column for name, m in cat.metrics.items()
                     if m.table == table}
    if not metric_to_col:
        return {}
    with create_span("quality.exclusions.map", table=table) as span:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT metric, entity_id, date FROM data_quality_findings
                   WHERE verdict = 'trash' AND resolved_at IS NULL
                     AND metric = ANY(%s)""",
                (list(metric_to_col),),
            )
            out = {(eid, metric_to_col[metric], d): None
                   for metric, eid, d in cur.fetchall()}
        set_attributes(span, n_convicted=len(out))
        return out
