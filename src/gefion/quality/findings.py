"""Findings-ledger API (008, T009 — US1).

Idempotent by construction: recording rides the UNIQUE (entity_table,
entity_id, metric, date, rule) — re-validation refreshes observed/expected/
detail/context, never duplicates. Resolution supersedes, never erases
(FR-307): detection facts are immutable, the resolved fields are the only
later amendment.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes


class FindingError(ValueError):
    """Raised on an invalid ledger operation (unknown finding, empty reason)."""


def record_findings(conn, entries: List[Dict[str, Any]], context: str) -> int:
    """Upsert detections. Each entry: entity_table, entity_id, metric, date,
    result (a rules.RuleResult). Returns the number of entries written."""
    if not entries:
        return 0
    with create_span("quality.findings.record", n_entries=len(entries)) as span:
        with conn.cursor() as cur:
            for e in entries:
                r = e["result"]
                cur.execute(
                    """INSERT INTO data_quality_findings
                           (entity_table, entity_id, metric, date, rule,
                            verdict, observed, expected, detail, context)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (entity_table, entity_id, metric, date, rule)
                       DO UPDATE SET
                           verdict = EXCLUDED.verdict,
                           observed = EXCLUDED.observed,
                           expected = EXCLUDED.expected,
                           detail = EXCLUDED.detail,
                           context = EXCLUDED.context""",
                    (e["entity_table"], e["entity_id"], e["metric"], e["date"],
                     r.rule, r.verdict, r.observed, r.expected,
                     Json(r.detail) if r.detail else None, context),
                )
        set_attributes(span, n_written=len(entries))
        return len(entries)


_COLS = ("id", "entity_table", "entity_id", "metric", "date", "rule",
         "verdict", "observed", "expected", "detail", "context",
         "created_at", "resolved_at", "resolution")


def list_findings(conn, metric: Optional[str] = None,
                  entity_table: Optional[str] = None,
                  entity_id: Optional[int] = None,
                  verdict: Optional[str] = None,
                  since: Optional[Any] = None,
                  include_resolved: bool = False,
                  limit: int = 200) -> List[Dict[str, Any]]:
    """Findings, newest first. Default: unresolved only."""
    where, params = [], []
    for clause, value in (("metric = %s", metric),
                          ("entity_table = %s", entity_table),
                          ("entity_id = %s", entity_id),
                          ("verdict = %s", verdict),
                          ("date >= %s", since)):
        if value is not None:
            where.append(clause)
            params.append(value)
    if not include_resolved:
        where.append("resolved_at IS NULL")
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_COLS)} FROM data_quality_findings "
            f"{sql_where} ORDER BY created_at DESC, id DESC LIMIT %s",
            params + [limit],
        )
        return [dict(zip(_COLS, row)) for row in cur.fetchall()]


def resolve_finding(conn, finding_id: int, reason: str) -> None:
    """Supersede a finding: sets resolved_at/resolution, never deletes.
    Refuses without a reason."""
    if not reason or not reason.strip():
        raise FindingError("resolution requires a --reason")
    with create_span("quality.findings.resolve", finding_id=finding_id):
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE data_quality_findings
                   SET resolved_at = NOW(), resolution = %s
                   WHERE id = %s RETURNING id""",
                (reason, finding_id),
            )
            if cur.fetchone() is None:
                raise FindingError(f"no finding with id {finding_id}")
