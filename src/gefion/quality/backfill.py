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
        # Reconcile (issue #85): unresolved findings in this run's scope that
        # no longer reproduce are superseded, never deleted — a catalog retune
        # is self-cleaning.
        resolved = _reconcile(conn, selected, entries)
        by_rule: Dict[str, int] = {}
        for e in entries:
            by_rule[e["result"].rule] = by_rule.get(e["result"].rule, 0) + 1
        set_attributes(span, rows_examined=rows_examined, findings=written,
                       resolved=resolved)
        return {"rows_examined": rows_examined,
                "findings": {"created": created, "written": written,
                             "resolved": resolved},
                "by_rule": by_rule, "stored_values_changed": 0}


# Rules this backfill actually evaluates — the only ones it may reconcile.
# cross_field is write-path only (needs the provider payload) and must never
# be resolved by a scan that didn't examine it.
_BACKFILL_RULES = ("definitional_bound", "temporal_spike",
                   "cross_sectional_outlier", "series_dynamic_range")


def _reconcile(conn, selected, entries) -> int:
    """Supersede unresolved findings, within this run's scope, that did not
    reproduce in this scan (issue #85). Scope = the metrics examined × the
    rules the backfill evaluates. Returns the number resolved."""
    if not selected:
        return 0
    reproduced = {(e["entity_table"], e["entity_id"], e["metric"], e["date"],
                   e["result"].rule) for e in entries}
    metric_names = [m.name for m in selected]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, entity_table, entity_id, metric, date, rule
               FROM data_quality_findings
               WHERE resolved_at IS NULL
                 AND metric = ANY(%s) AND rule = ANY(%s)""",
            (metric_names, list(_BACKFILL_RULES)),
        )
        stale = [fid for fid, et, eid, met, d, rule in cur.fetchall()
                 if (et, eid, met, d, rule) not in reproduced]
        for fid in stale:
            cur.execute(
                """UPDATE data_quality_findings
                   SET resolved_at = NOW(),
                       resolution = 'no longer reproduces under the current '
                                    'catalog (quality backfill reconcile)'
                   WHERE id = %s""",
                (fid,),
            )
    return len(stale)


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
    if metric.series_range is not None:
        return _scan_series_range(conn, metric, entries)
    if metric.entity_table == "macro_series":
        return _scan_macro(conn, cat, metric, entries)
    return _scan_stock(conn, cat, metric, entries)


def _scan_series_range(conn, metric, entries):
    """Series dynamic range (issue #136): ONE aggregate per entity, computed
    SQL-side — the per-row scan path would drag the whole hypertable into
    memory and mint a finding per date. One suspect finding per offending
    entity, dated at its max value (the most-restated point)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """SELECT data_id, MAX({col}),
                          MIN({col}) FILTER (WHERE {col} > 0)
                   FROM {tbl} WHERE {col} IS NOT NULL GROUP BY data_id"""
            ).format(col=sql.Identifier(metric.column),
                     tbl=sql.Identifier(metric.table)))
        aggregates = cur.fetchall()
    examined = len(aggregates)
    for eid, max_v, min_pos in aggregates:
        r = rules.check_series_range(
            float(max_v) if max_v is not None else None,
            float(min_pos) if min_pos is not None else None,
            metric.series_range)
        if r is None:
            continue
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT date FROM {tbl} WHERE data_id = %s "
                        "AND {col} = %s ORDER BY date LIMIT 1")
                .format(tbl=sql.Identifier(metric.table),
                        col=sql.Identifier(metric.column)),
                (eid, max_v))
            (max_date,) = cur.fetchone()
        entries.append(_entry(metric.entity_table, eid, metric, max_date, r))
    return examined, entries


def _scan_stock(conn, cat, metric, entries):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT data_id, date, {col} FROM {tbl} WHERE {col} IS NOT NULL")
            .format(col=sql.Identifier(metric.column),
                    tbl=sql.Identifier(metric.table)))
        rows = [(data_id, d, float(value)) for data_id, d, value in cur.fetchall()]
    examined = len(rows)
    _apply_tiers(cat, metric, rows, "stocks", entries)
    return examined, entries


def _apply_tiers(cat, metric, rows, entity_table, entries):
    """Bounds (trash) plus the corroboration tiers (suspect): temporal spike
    per entity series and cross-sectional outlier per date. A date already
    convicted by bounds is not re-flagged by a corroboration tier."""
    convicted = set()   # (entity_id, date) already trash — don't double-flag

    for eid, d, value in rows:
        r = rules.check_bounds(metric, value)
        if r is not None:
            entries.append(_entry(entity_table, eid, metric, d, r))
            convicted.add((eid, d))

    # temporal spike: per entity, ordered by date, interior points
    spike_factor = cat.defaults["spike_factor"]
    by_entity: Dict[int, list] = {}
    for eid, d, value in rows:
        by_entity.setdefault(eid, []).append((d, value))
    for eid, series in by_entity.items():
        series.sort(key=lambda t: t[0])
        for i in range(1, len(series) - 1):
            d, value = series[i]
            if (eid, d) in convicted:
                continue
            r = rules.check_temporal_spike(series[i - 1][1], value,
                                           series[i + 1][1], spike_factor)
            if r is not None:
                entries.append(_entry(entity_table, eid, metric, d, r))

    # cross-sectional outlier: per date across the universe
    z_threshold = cat.defaults["robust_z_threshold"]
    by_date: Dict[Any, list] = {}
    for eid, d, value in rows:
        by_date.setdefault(d, []).append((eid, value))
    for d, members in by_date.items():
        universe = [v for _, v in members]
        for eid, value in members:
            if (eid, d) in convicted:
                continue
            r = rules.check_cross_sectional(value, universe, z_threshold)
            if r is not None:
                entries.append(_entry(entity_table, eid, metric, d, r))


def _entry(entity_table, entity_id, metric, d, result):
    return {"entity_table": entity_table, "entity_id": entity_id,
            "metric": metric.name, "date": d, "result": result}


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
