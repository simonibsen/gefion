"""Macro-series catalog CRUD (007, T018 — US2).

Rows are configuration, not schema: adding a series is an INSERT here plus an
ingest — never DDL (SC-207). The catalog row's `provider` string is the ingest
dispatch key ('fred:VIXCLS', 'alphavantage:INDEX_DATA').
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes


def ensure_series(conn, name: str, provider: str, kind: str, cadence: str,
                  description: Optional[str] = None) -> int:
    """Upsert a catalog row by name; return its id."""
    with create_span("macro.catalog.ensure_series", series=name) as span:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO macro_series (name, provider, kind, cadence, description)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET
                       provider = EXCLUDED.provider,
                       kind = EXCLUDED.kind,
                       cadence = EXCLUDED.cadence,
                       description = COALESCE(EXCLUDED.description,
                                              macro_series.description)
                   RETURNING id""",
                (name, provider, kind, cadence, description),
            )
            series_id = cur.fetchone()[0]
        set_attributes(span, series_id=series_id)
        return series_id


def get_series(conn, name: str) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, provider, kind, cadence, description
               FROM macro_series WHERE name = %s""", (name,))
        row = cur.fetchone()
    if row is None:
        return None
    keys = ("id", "name", "provider", "kind", "cadence", "description")
    return dict(zip(keys, row))


def list_series(conn) -> List[Dict[str, Any]]:
    """Catalog + per-series value coverage + whether the feature exists."""
    with create_span("macro.catalog.list_series") as span:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ms.name, ms.provider, ms.kind, ms.cadence,
                          count(v.*) AS values,
                          min(v.date) AS first_date, max(v.date) AS last_date,
                          EXISTS (SELECT 1 FROM feature_definitions fd
                                  WHERE fd.name = 'macro_' || ms.name) AS materialized
                   FROM macro_series ms
                   LEFT JOIN macro_series_values v ON v.series_id = ms.id
                   GROUP BY ms.id ORDER BY ms.name""")
            rows = cur.fetchall()
        out = [{
            "name": name, "provider": provider, "kind": kind, "cadence": cadence,
            "values": values,
            "first_date": first.isoformat() if first else None,
            "last_date": last.isoformat() if last else None,
            "materialized": materialized,
        } for name, provider, kind, cadence, values, first, last, materialized in rows]
        set_attributes(span, n_series=len(out))
        return out
