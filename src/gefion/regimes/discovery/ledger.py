"""Run, candidate, and diagnostics ledgers for regime discovery (006, T010).

The ledger is the honesty mechanism (FR-104/105/106): a run pre-registers its
search space (with the three pluggable seams declared) and segregation
boundaries before anything is evaluated; the status lifecycle freezes the
candidate set before evaluation (the T4 guard); every candidate — losers
included — is persisted; and counted_in_family invariants are enforced here
so silent survivorship is structurally impossible, not just discouraged.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Union

from psycopg.types.json import Json

from gefion.observability import create_span, set_attributes

REQUIRED_SEAMS = ("signal_source", "grading_scheme", "universe_filter")

_TRANSITIONS = {
    "pre_registered": {"enumerated", "invalid"},
    "enumerated": {"evaluated", "invalid"},
    "evaluated": {"complete", "invalid"},
    "complete": set(),
    "invalid": set(),
}

# Refusal verdicts never enter the FDR family; evaluated verdicts always do.
_REFUSAL_VERDICTS = ("refused_low_power", "refused_degenerate", "refused_unstable")

_RUN_COLUMNS = ("id", "name", "seed", "search_space", "segregation", "reserve_consumed",
                "family_size", "status", "dataset_version", "created_at", "completed_at")


class LedgerError(ValueError):
    """Raised on an invalid ledger operation (bad pre-registration, illegal transition)."""


# --- runs --------------------------------------------------------------------

def create_run(conn, name: str, seed: int, search_space: Dict[str, Any],
               segregation: Dict[str, Any], dataset_version: str) -> int:
    """Pre-register a discovery run. The search space MUST declare the three
    pluggable seams (signal_source, grading_scheme, universe_filter) — an
    undeclared seam is a hidden researcher degree of freedom, refused."""
    with create_span("discovery.ledger.create_run", run_name=name) as span:
        missing = [s for s in REQUIRED_SEAMS if not search_space.get(s)]
        if missing:
            raise LedgerError(f"search space missing declared seam(s): {missing}")
        if not segregation:
            raise LedgerError("segregation boundaries must be recorded at pre-registration")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO regime_discovery_runs
                       (name, seed, search_space, segregation, dataset_version)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (name, seed, Json(search_space), Json(segregation), dataset_version),
            )
            run_id = cur.fetchone()[0]
        set_attributes(span, run_id=run_id)
        return run_id


def _row_to_run(row) -> Dict[str, Any]:
    return dict(zip(_RUN_COLUMNS, row))


def get_run(conn, run: Union[int, str]) -> Dict[str, Any]:
    """Load a run by id or name (name: most recent). Raises LedgerError if absent."""
    where = "id = %s" if isinstance(run, int) else "name = %s"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM regime_discovery_runs "
            f"WHERE {where} ORDER BY id DESC LIMIT 1",
            (run,),
        )
        row = cur.fetchone()
    if row is None:
        raise LedgerError(f"discovery run {run!r} not found")
    return _row_to_run(row)


def list_runs(conn, status: Optional[str] = None) -> List[Dict[str, Any]]:
    where, params = "", []
    if status is not None:
        where = " WHERE status = %s"
        params.append(status)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM regime_discovery_runs{where} "
            "ORDER BY id DESC",
            params,
        )
        return [_row_to_run(r) for r in cur.fetchall()]


def set_status(conn, run_id: int, status: str) -> None:
    """Advance the run lifecycle; only declared transitions are legal.

    pre_registered → enumerated (candidate set FROZEN) → evaluated → complete;
    any active run may become invalid. Terminal states never change.
    """
    with create_span("discovery.ledger.set_status", run_id=run_id, status=status):
        current = get_run(conn, run_id)["status"]
        if status not in _TRANSITIONS.get(current, set()):
            raise LedgerError(f"illegal status transition {current!r} -> {status!r}")
        completed = (
            datetime.datetime.now(datetime.timezone.utc)
            if status in ("complete", "invalid") else None
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE regime_discovery_runs SET status = %s, "
                "completed_at = COALESCE(%s, completed_at) WHERE id = %s",
                (status, completed, run_id),
            )


def set_family_size(conn, run_id: int, family_size: int) -> None:
    """Record the realized FDR denominator (FR-120)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE regime_discovery_runs SET family_size = %s WHERE id = %s",
                    (family_size, run_id))


# --- candidate ledger ----------------------------------------------------------

def record_candidates(conn, run_id: int, candidates: List[Dict[str, Any]]) -> List[int]:
    """Persist enumerated candidates. Allowed only BEFORE the freeze: once the
    run is enumerated, the candidate set is immutable (T4 guard)."""
    with create_span("discovery.ledger.record_candidates",
                     run_id=run_id, n_candidates=len(candidates)):
        if get_run(conn, run_id)["status"] != "pre_registered":
            raise LedgerError("candidate set is frozen — run is past pre_registered")
        ids: List[int] = []
        with conn.cursor() as cur:
            for cand in candidates:
                cur.execute(
                    """INSERT INTO regime_candidates
                           (run_id, candidate_hash, expression, tier, provenance)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (run_id, cand["candidate_hash"], Json(cand["expression"]),
                     cand["tier"],
                     Json(cand["provenance"]) if cand.get("provenance") is not None else None),
                )
                ids.append(cur.fetchone()[0])
        return ids


def record_result(conn, candidate_id: int, results: Dict[str, Any], verdict: str) -> None:
    """Record a candidate's evaluation. Only legal after the freeze; the
    counted_in_family flag is derived from the verdict here — callers cannot
    un-count an evaluated candidate (FR-104)."""
    with create_span("discovery.ledger.record_result",
                     candidate_id=candidate_id, verdict=verdict):
        with conn.cursor() as cur:
            cur.execute("SELECT run_id FROM regime_candidates WHERE id = %s", (candidate_id,))
            row = cur.fetchone()
        if row is None:
            raise LedgerError(f"candidate {candidate_id} not found")
        status = get_run(conn, row[0])["status"]
        if status not in ("enumerated", "evaluated"):
            raise LedgerError(
                f"results may only be recorded after the candidate freeze (run is {status!r})")
        counted = verdict not in _REFUSAL_VERDICTS
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE regime_candidates SET results = %s, verdict = %s, "
                "counted_in_family = %s WHERE id = %s",
                (Json(results), verdict, counted, candidate_id),
            )


def list_candidates(conn, run_id: int, verdict: Optional[str] = None) -> List[Dict[str, Any]]:
    cols = ("id", "run_id", "candidate_hash", "expression", "tier", "provenance",
            "results", "counted_in_family", "verdict")
    where, params = "run_id = %s", [run_id]
    if verdict is not None:
        where += " AND verdict = %s"
        params.append(verdict)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(cols)} FROM regime_candidates WHERE {where} ORDER BY id",
            params,
        )
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- diagnostics ledger ----------------------------------------------------------

def record_diagnostic(conn, run_id: int, kind: str, detail: Dict[str, Any],
                      sample_dependent: bool,
                      dataset_version: Optional[str] = None) -> int:
    """Record a limit the search hit, tagged sample-dependent (re-test on new
    data) vs structural (accumulate). Dataset provenance defaults from the run
    (FR-124/125)."""
    with create_span("discovery.ledger.record_diagnostic", run_id=run_id, kind=kind):
        if dataset_version is None:
            dataset_version = get_run(conn, run_id)["dataset_version"]
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO discovery_diagnostics
                       (run_id, kind, detail, sample_dependent, dataset_version)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (run_id, kind, Json(detail), sample_dependent, dataset_version),
            )
            return cur.fetchone()[0]


def list_diagnostics(conn, run_id: int,
                     sample_dependent: Optional[bool] = None) -> List[Dict[str, Any]]:
    cols = ("id", "run_id", "kind", "detail", "sample_dependent",
            "dataset_version", "created_at")
    where, params = "run_id = %s", [run_id]
    if sample_dependent is not None:
        where += " AND sample_dependent = %s"
        params.append(sample_dependent)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(cols)} FROM discovery_diagnostics WHERE {where} ORDER BY id",
            params,
        )
        return [dict(zip(cols, r)) for r in cur.fetchall()]
