"""Candidate ledger for generated market functions (spec 014, epic #114).

The waiting room: machine-generated market-scope bodies land here — never in
feature_functions — so pending/rejected generated code has no execution path
by construction. Approval promotes into feature_functions atomically;
rejection retains the row (audit: supersede/hide, never erase).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes

_COLS = ("id, name, version, kind, function_body, inputs, description, "
         "origin, principle_id, generator, dry_run, review_state, "
         "reviewed_by, reviewed_at, review_reason, promoted_function_id, "
         "created_at")


def _row_to_dict(row) -> Dict[str, Any]:
    keys = [c.strip() for c in _COLS.split(",")]
    return dict(zip(keys, row))


def create_candidate(conn, name: str, kind: str, function_body: str,
                     origin: str, inputs: Optional[Dict[str, Any]] = None,
                     description: Optional[str] = None,
                     principle_id: Optional[str] = None,
                     generator: Optional[str] = None) -> int:
    """Store a candidate in pending state. Same name = new version, never an
    overwrite (the pending queue shows both)."""
    with create_span("macro.candidates.create", candidate_name=name,
                     kind=kind, origin=origin) as span:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 "
                "FROM market_function_candidates WHERE name = %s", (name,))
            (version,) = cur.fetchone()
            cur.execute(
                """INSERT INTO market_function_candidates
                   (name, version, kind, function_body, inputs, description,
                    origin, principle_id, generator)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (name, version, kind, function_body, Json(inputs or {}),
                 description, origin, principle_id, generator))
            (cid,) = cur.fetchone()
        conn.commit()
        set_attributes(span, candidate_id=cid, version=version)
        return cid


def get_candidate(conn, candidate_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM market_function_candidates WHERE id = %s",
            (candidate_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_candidates(conn, state: Optional[str] = "pending") -> List[Dict[str, Any]]:
    """Candidates, newest first. Default: the pending queue."""
    with create_span("macro.candidates.list", state=state or "all") as span:
        where, params = "", []
        if state is not None:
            where, params = "WHERE review_state = %s", [state]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM market_function_candidates {where} "
                "ORDER BY created_at DESC, id DESC", params)
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        set_attributes(span, n_candidates=len(rows))
        return rows


def record_dry_run(conn, candidate_id: int, dry_run: Dict[str, Any]) -> None:
    """Attach (or refresh) the dry-run record shown at review."""
    with create_span("macro.candidates.record_dry_run",
                     candidate_id=candidate_id,
                     ok=bool(dry_run.get("ok"))):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE market_function_candidates SET dry_run = %s "
                "WHERE id = %s", (Json(dry_run), candidate_id))
        conn.commit()
