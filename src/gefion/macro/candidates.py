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


# --- dry-run: the ONLY sanctioned execution of a candidate -------------------------

_SYNTH_DATES = ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
                "2026-01-08")
_SYNTH_SECTORS = ("TECHNOLOGY", "FINANCIAL SERVICES", "HEALTHCARE",
                  "INDUSTRIALS", "ENERGY")


def synthetic_cross_section(seed: int = 42, n_symbols: int = 50,
                            feature_names: Optional[List[str]] = None):
    """Deterministic seeded synthetic cross-section matching the market
    contract's row shape. Never real data: the dry-run must not touch stored
    history (evaluation against real history IS execution)."""
    import random

    rng = random.Random(seed)
    days = []
    for d in _SYNTH_DATES:
        rows = []
        for i in range(n_symbols):
            close = round(rng.uniform(5, 500), 2)
            row = {
                "symbol": f"SYN{i:03d}",
                "close": close,
                "high": round(close * rng.uniform(1.0, 1.05), 2),
                "low": round(close * rng.uniform(0.95, 1.0), 2),
                "volume": rng.randint(10_000, 5_000_000),
                "sector": _SYNTH_SECTORS[i % len(_SYNTH_SECTORS)],
            }
            for feat in (feature_names or []):
                row[feat] = round(rng.uniform(0, 100), 4)
            rows.append(row)
        days.append((d, rows))
    return days


def dry_run_candidate(function_body: str, kind: str,
                      inputs: Optional[Dict[str, Any]] = None,
                      seed: int = 42) -> Dict[str, Any]:
    """Execute a candidate body in the sandbox over seeded synthetic inputs
    and return the review record: {ok, sample, error, seed, ran_at}. A
    sandbox violation, missing compute, raise, or wrong-shaped return fails
    the dry-run (which blocks approval)."""
    import datetime
    import math

    from gefion.features.dispatcher import _exec_in_sandbox

    with create_span("macro.candidates.dry_run", kind=kind, seed=seed) as span:
        record: Dict[str, Any] = {
            "ok": False, "sample": [], "error": None, "seed": seed,
            "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        try:
            env = _exec_in_sandbox(function_body, None, raise_errors=True)
        except Exception as exc:
            record["error"] = f"sandbox refused the body: {exc}"
            return record
        compute = (env or {}).get("compute")
        if not callable(compute):
            record["error"] = "body must define compute(...)"
            return record

        if kind == "composite":
            days = synthetic_series_rows(
                seed=seed, series_names=(inputs or {}).get("series") or [])
            call = lambda payload: compute(payload)  # noqa: E731
        else:
            days = synthetic_cross_section(
                seed=seed, feature_names=(inputs or {}).get("features") or [])
            call = lambda payload: compute(payload)  # noqa: E731

        sample = []
        for d, payload in days:
            try:
                result = call(payload)
            except Exception as exc:
                record["error"] = f"compute raised on {d}: {exc}"
                return record
            if result is None:
                sample.append({"date": d, "value": None})
                continue
            if isinstance(result, bool) or not isinstance(result, (int, float)):
                record["error"] = (
                    f"compute returned {type(result).__name__!r} on {d} — "
                    "must be float or None")
                return record
            value = float(result)
            sample.append({"date": d,
                           "value": value if math.isfinite(value) else None})

        record["ok"] = True
        record["sample"] = sample
        set_attributes(span, ok=True, n_sample=len(sample))
        return record


def synthetic_series_rows(seed: int = 42,
                          series_names: Optional[List[str]] = None):
    """Deterministic seeded per-date named-series rows for composite
    candidates (one dict of series values per synthetic date)."""
    import random

    rng = random.Random(seed)
    days = []
    for d in _SYNTH_DATES:
        days.append((d, {name: round(rng.uniform(0.1, 100), 4)
                         for name in (series_names or [])}))
    return days


# --- the gate: approve / reject / promote ------------------------------------------

def approve_candidate(conn, candidate_id: int,
                      approver: Optional[str] = None) -> int:
    """Human act: promote a pending candidate into feature_functions
    (scope='market', active) with its paired macro-home definition, atomically.
    Refuses failed/missing dry-runs, non-pending states, and name collisions.
    Returns the promoted feature_functions id."""
    import json as _json

    from gefion.macro import catalog

    with create_span("macro.candidates.approve",
                     candidate_id=candidate_id) as span:
        c = get_candidate(conn, candidate_id)
        if c is None:
            raise ValueError(f"no candidate with id {candidate_id}")
        if c["review_state"] != "pending":
            raise ValueError(
                f"candidate {candidate_id} is {c['review_state']} — only "
                "pending candidates can be approved")
        dry = c.get("dry_run")
        if not dry or not dry.get("ok"):
            raise ValueError(
                f"candidate {candidate_id} has no passing dry-run — a "
                "failed or missing dry-run blocks approval")
        if c["kind"] == "composite":
            from gefion.macro.composites import validate_composite_inputs
            validate_composite_inputs(conn, c["name"],
                                      (c["inputs"] or {}).get("series") or [])
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM feature_functions WHERE name = %s",
                        (c["name"],))
            if cur.fetchone():
                raise ValueError(
                    f"a function named {c['name']!r} already exists — "
                    "promotion refuses to overwrite it")

        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO feature_functions
                           (name, version, status, enabled, description,
                            language, function_body, inputs, scope,
                            created_by, tags)
                       VALUES (%s, %s, 'active', TRUE, %s, 'python', %s, %s,
                               'market', 'candidate-gate', %s)
                       RETURNING id""",
                    (c["name"], f"cand-{candidate_id}",
                     c.get("description") or
                     f"Generated from principle: {c.get('principle_id')}",
                     c["function_body"], _json.dumps(c["inputs"] or {}),
                     ["ai-generated", "market", str(c.get("principle_id"))]))
                (fid,) = cur.fetchone()
            series_id = catalog.ensure_series(
                conn, name=c["name"], provider="derived", kind="derived",
                cadence="daily", description=c.get("description"))
            with conn.cursor() as cur:
                # same upsert as ensure_feature_definitions, inlined so the
                # whole promotion stays one transaction (the helper commits)
                cur.execute(
                    """INSERT INTO feature_definitions
                           (name, function_name, params, source_table,
                            source_column, store_table, store_column,
                            store_type, active, entity_table)
                       VALUES (%s, %s, NULL, 'stock_ohlcv', 'close',
                               'computed_features', 'value',
                               'double precision', TRUE, 'macro_series')
                       ON CONFLICT (name) DO UPDATE SET
                           function_name = EXCLUDED.function_name,
                           active = EXCLUDED.active,
                           entity_table = EXCLUDED.entity_table""",
                    (f"macro_{c['name']}", c["name"]))
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE market_function_candidates
                       SET review_state = 'approved', reviewed_by = %s,
                           reviewed_at = NOW(), promoted_function_id = %s
                       WHERE id = %s""",
                    (approver, fid, candidate_id))
        set_attributes(span, promoted_function_id=fid,
                       series_id=series_id)
        return fid


def reject_candidate(conn, candidate_id: int, reason: str,
                     approver: Optional[str] = None) -> None:
    """Human act: terminal rejection with a required reason. The row is
    retained for audit — supersede/hide, never erase."""
    with create_span("macro.candidates.reject",
                     candidate_id=candidate_id):
        if not (reason or "").strip():
            raise ValueError("rejection requires a reason")
        c = get_candidate(conn, candidate_id)
        if c is None:
            raise ValueError(f"no candidate with id {candidate_id}")
        if c["review_state"] != "pending":
            raise ValueError(
                f"candidate {candidate_id} is {c['review_state']} — only "
                "pending candidates can be rejected")
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE market_function_candidates
                   SET review_state = 'rejected', reviewed_by = %s,
                       reviewed_at = NOW(), review_reason = %s
                   WHERE id = %s""",
                (approver, reason, candidate_id))
        conn.commit()


def gate_refusal(conn, name: str) -> Optional[str]:
    """If `name` exists only as an unpromoted candidate, the refusal message
    naming the gate — else None. Used by the derive door (SC-1401)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT review_state FROM market_function_candidates "
            "WHERE name = %s ORDER BY version DESC LIMIT 1", (name,))
        row = cur.fetchone()
    if row is None or row[0] == "approved":
        return None
    return (f"{name!r} is a {row[0]} candidate — review with "
            "`gefion macro candidate show`; it cannot compute values "
            "until approved.")
