"""Fresh-holdout reserve for the expressive tier (006, T028 — US3).

Free-form expressions and sandboxed detector candidates are admissible only
against a declared, dated reserve block distinct from the outer holdout
(FR-118a/119). Fresh-holdout honesty is entirely about NON-REUSE (R4):
consumption is a database fact (`regime_discovery_runs.reserve_consumed`),
re-declaring a consumed block is refused unless explicitly justified, and the
justification is recorded in the new run's pre-registration.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes


class ReserveError(ValueError):
    """Raised on an invalid, undeclared, or already-consumed reserve block."""


def _date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(str(value))


def validate_reserve(boundaries: Dict[str, Any], start: str, end: str) -> Dict[str, str]:
    """Validate a reserve block against the run's segregation boundaries.

    The block must be a forward-dated range and must not overlap the outer
    holdout — an overlapping reserve would let expressive candidates peek at
    the judge.
    """
    s, e = _date(start), _date(end)
    if s >= e:
        raise ReserveError(f"reserve must be a forward block, got {start}..{end}")
    h_start, h_end = _date(boundaries["holdout_start"]), _date(boundaries["holdout_end"])
    if s <= h_end and e >= h_start:
        raise ReserveError(
            f"reserve {start}..{end} overlaps the outer holdout "
            f"{boundaries['holdout_start']}..{boundaries['holdout_end']}")
    return {"start": str(s), "end": str(e)}


def conflicting_runs(conn, start: str, end: str) -> List[Dict[str, Any]]:
    """Runs that already CONSUMED a reserve block overlapping [start, end]."""
    s, e = _date(start), _date(end)
    out: List[Dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, segregation->'reserve' FROM regime_discovery_runs
               WHERE reserve_consumed AND segregation ? 'reserve' ORDER BY id""")
        for run_id, name, reserve in cur.fetchall():
            if not reserve:
                continue
            r_start, r_end = _date(reserve["start"]), _date(reserve["end"])
            if s <= r_end and e >= r_start:
                out.append({"id": run_id, "name": name, "reserve": reserve})
    return out


def require_reserve(conn, boundaries: Dict[str, Any], start: str, end: str,
                    justification: Optional[str] = None) -> Dict[str, Any]:
    """The full declaration gate: valid block, and no reuse of a consumed
    block unless explicitly justified — the justification (and the prior run
    ids it overrides) is recorded in the returned reserve declaration."""
    with create_span("discovery.freshhold.require_reserve") as span:
        reserve: Dict[str, Any] = validate_reserve(boundaries, start, end)
        conflicts = conflicting_runs(conn, start, end)
        set_attributes(span, n_conflicts=len(conflicts))
        if conflicts:
            if not justification:
                names = [(c["id"], c["name"]) for c in conflicts]
                raise ReserveError(
                    f"reserve {start}..{end} overlaps block(s) already consumed by "
                    f"run(s) {names}; re-declaration requires an explicit justification")
            reserve["justification"] = justification
            reserve["overlaps_consumed_runs"] = [c["id"] for c in conflicts]
        return reserve


def consume(conn, run_id: int) -> None:
    """Mark the run's reserve consumed. Single-use: consuming twice raises."""
    with create_span("discovery.freshhold.consume", run_id=run_id):
        with conn.cursor() as cur:
            cur.execute("SELECT reserve_consumed FROM regime_discovery_runs WHERE id = %s",
                        (run_id,))
            row = cur.fetchone()
            if row is None:
                raise ReserveError(f"run {run_id} not found")
            if row[0]:
                raise ReserveError(f"run {run_id} already consumed its reserve block")
            cur.execute(
                "UPDATE regime_discovery_runs SET reserve_consumed = TRUE WHERE id = %s",
                (run_id,))
