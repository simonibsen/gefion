"""System-observations ledger (#144).

The operating plane's notebook: machine-noticed improvements, anomalies,
tuning opportunities, and hypotheses. The ledger holds OBSERVATIONS, never
actions — nothing reads it programmatically to change system behavior;
adoption is always a human act (typically converting the row into an issue,
spec, or config change). Terminal review states are immutable: supersede,
never rewrite.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes

CATEGORIES = ("improvement", "anomaly", "tuning", "hypothesis")
_TERMINAL = ("adopted", "rejected")

_COLS = ("id, observer, category, observation, evidence, suggested_action, "
         "review_state, reviewed_by, reviewed_at, review_reason, created_at")


def _row_to_dict(row) -> Dict[str, Any]:
    keys = [c.strip() for c in _COLS.split(",")]
    return dict(zip(keys, row))


def record(conn, observer: str, category: str, observation: str,
           evidence: Optional[Dict[str, Any]] = None,
           suggested_action: Optional[str] = None) -> int:
    """Record one observation (state: open). Cheap by design — the moment
    of observation is the moment of recording."""
    with create_span("observations.record", observer=observer,
                     category=category) as span:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_observations
                       (observer, category, observation, evidence,
                        suggested_action)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (observer, category, observation,
                 Json(evidence) if evidence is not None else None,
                 suggested_action))
            (oid,) = cur.fetchone()
        conn.commit()
        set_attributes(span, observation_id=oid)
        return oid


def get(conn, observation_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLS} FROM system_observations WHERE id = %s",
                    (observation_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_observations(conn, state: Optional[str] = "open",
                      observer: Optional[str] = None,
                      limit: int = 200) -> List[Dict[str, Any]]:
    """Observations, newest first. Default: the open queue."""
    with create_span("observations.list", state=state or "all") as span:
        where, params = [], []
        if state is not None:
            where.append("review_state = %s")
            params.append(state)
        if observer is not None:
            where.append("observer = %s")
            params.append(observer)
        sql_where = ("WHERE " + " AND ".join(where)) if where else ""
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM system_observations {sql_where} "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                params + [limit])
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        set_attributes(span, n=len(rows))
        return rows


def open_count(conn) -> int:
    """For db-health: how many observations await a human."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM system_observations "
                    "WHERE review_state = 'open'")
        return cur.fetchone()[0]


def review(conn, observation_id: int, state: str,
           reviewer: Optional[str] = None,
           reason: Optional[str] = None) -> None:
    """Human act: acknowledge (intermediate), adopt, or reject (terminal).
    Rejection requires a reason; terminal states are immutable."""
    with create_span("observations.review", observation_id=observation_id,
                     state=state):
        if state not in ("acknowledged", "adopted", "rejected"):
            raise ValueError(f"unknown review state {state!r}")
        if state == "rejected" and not (reason or "").strip():
            raise ValueError("rejection requires a reason")
        o = get(conn, observation_id)
        if o is None:
            raise ValueError(f"no observation with id {observation_id}")
        if o["review_state"] in _TERMINAL:
            raise ValueError(
                f"observation {observation_id} is {o['review_state']} — "
                "terminal states are immutable (supersede, never rewrite)")
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE system_observations
                   SET review_state = %s, reviewed_by = %s,
                       reviewed_at = NOW(), review_reason = %s
                   WHERE id = %s""",
                (state, reviewer, reason, observation_id))
        conn.commit()
