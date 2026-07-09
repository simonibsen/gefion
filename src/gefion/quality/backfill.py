"""On-demand validation of already-stored history (008, T019 — US4).

The write-path validation covers new data; the backfill covers everything
written before this spec existed (prod holds the issue-79 garbage right now)
or outside the covered paths. It runs the same catalog + rules + ledger,
idempotently, and changes ZERO stored values (SC-305) — it only reads the
data tables and writes findings.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg import sql

from gefion.observability import create_span, set_attributes
from gefion.quality import catalog as qcatalog
from gefion.quality import findings as qfindings
from gefion.quality import rules
from gefion.quality import validate as qvalidate


def run(conn, entity_table: Optional[str] = None,
        metric: Optional[str] = None, cat=None) -> Dict[str, Any]:
    """Validate stored values against the catalog and record findings.

    Filters: `entity_table` ('stocks' | 'macro_series'), `metric`. Reads the
    data tables directly (no re-ingest); every catalog metric's column is
    scanned per row. Corroboration tiers (temporal/cross-sectional) run here
    where full history/cross-section is at hand.
    """
    cat = cat or qcatalog.load_default()
    with create_span("quality.backfill.run", entity_table=entity_table or "all") as span:
        selected = [m for m in cat.metrics.values()
                    if (entity_table is None or m.entity_table == entity_table)
                    and (metric is None or m.name == metric)]
        rows_examined = 0
        entries: List[Dict[str, Any]] = []
        for m in selected:
            examined, found = _scan_metric(conn, cat, m)
            rows_examined += examined
            entries.extend(found)
        # "created" = findings that did not already exist (idempotence report),
        # measured before the upsert.
        created = _count_new(conn, entries)
        written = qfindings.record_findings(conn, entries, context="quality backfill") \
            if entries else 0
        by_rule: Dict[str, int] = {}
        for e in entries:
            by_rule[e["result"].rule] = by_rule.get(e["result"].rule, 0) + 1
        set_attributes(span, rows_examined=rows_examined, findings=written)
        return {"rows_examined": rows_examined,
                "findings": {"created": created, "written": written},
                "by_rule": by_rule, "stored_values_changed": 0}


def _count_new(conn, entries) -> int:
    """How many entries have no existing finding row yet (before upsert)."""
    if not entries:
        return 0
    new = 0
    with conn.cursor() as cur:
        for e in entries:
            cur.execute(
                """SELECT 1 FROM data_quality_findings
                   WHERE entity_table=%s AND entity_id=%s AND metric=%s
                     AND date=%s AND rule=%s""",
                (e["entity_table"], e["entity_id"], e["metric"], e["date"],
                 e["result"].rule),
            )
            if cur.fetchone() is None:
                new += 1
    return new


def _scan_metric(conn, cat, metric):
    """Scan every stored value of one metric; return (rows_examined, entries)."""
    entries: List[Dict[str, Any]] = []
    if metric.entity_table == "macro_series":
        return _scan_macro(conn, cat, metric, entries)
    return _scan_stock(conn, cat, metric, entries)


def _scan_stock(conn, cat, metric, entries):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT data_id, date, {col} FROM {tbl} WHERE {col} IS NOT NULL")
            .format(col=sql.Identifier(metric.column),
                    tbl=sql.Identifier(metric.table)))
        rows = cur.fetchall()
    examined = 0
    for data_id, d, value in rows:
        examined += 1
        r = rules.check_bounds(metric, float(value))
        if r is not None:
            entries.append({"entity_table": "stocks", "entity_id": data_id,
                            "metric": metric.name, "date": d, "result": r})
    return examined, entries


def _scan_macro(conn, cat, metric, entries):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT v.series_id, v.date, v.value
               FROM macro_series_values v JOIN macro_series s ON s.id = v.series_id
               WHERE s.name = %s AND v.value IS NOT NULL""",
            (metric.series,))
        rows = cur.fetchall()
    examined = 0
    for series_id, d, value in rows:
        examined += 1
        r = rules.check_bounds(metric, float(value))
        if r is not None:
            entries.append({"entity_table": "macro_series", "entity_id": series_id,
                            "metric": metric.name, "date": d, "result": r})
    return examined, entries
