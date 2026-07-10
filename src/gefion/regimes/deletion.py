"""First-class deletion for regime artifacts (issues #75/#76).

The `data cull` / entity-delete mold: a plan function reports the FULL blast
radius changing nothing; an execute function deletes in dependency order.
Honest exceptions are structural, not advisory:

- A machine-origin (discovery-admitted) regime refuses deletion without
  ``force`` — and even forced deletion never touches the candidate ledger:
  removing the artifact must not remove the search accounting.
- A discovery run with admissions refuses deletion always (no force door) —
  its ledger is the multiple-testing audit trail behind a live artifact.
- Name-keyed soft references (``experiments.results -> by_regime``) are
  reported, never mutated — which is why ``regime archive`` remains the
  recommended lifecycle exit (results stay resolvable by name).
"""
from typing import Any, Dict

from gefion.observability import create_span, set_attributes


class RegimeDeleteError(ValueError):
    """Raised when a deletion is refused or the target does not exist."""


def _get_regime(conn, name: str) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, scope, origin, status, descriptive_metadata
               FROM regime_definitions WHERE name = %s""", (name,))
        row = cur.fetchone()
    if row is None:
        raise RegimeDeleteError(f"no regime named {name!r}")
    keys = ("id", "name", "scope", "origin", "status", "descriptive_metadata")
    return dict(zip(keys, row))


def plan_regime_delete(conn, name: str) -> Dict[str, Any]:
    """The dry-run: labels row count, discovery provenance if machine-origin,
    and stored results that reference the name. Changes nothing."""
    with create_span("regimes.deletion.plan", regime=name) as span:
        regime = _get_regime(conn, name)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM regime_labels WHERE regime_id = %s",
                        (regime["id"],))
            labels = cur.fetchone()[0]
            cur.execute(
                """SELECT id, name FROM experiments
                   WHERE results->'by_regime'->>'regime' = %s ORDER BY id""",
                (name,))
            refs = [{"id": i, "name": n} for i, n in cur.fetchall()]
        machine = regime["origin"] == "machine"
        set_attributes(span, regime_id=regime["id"], labels=labels,
                       experiment_references=len(refs), machine_origin=machine)
        return {
            "regime": regime,
            "labels": labels,
            "experiment_references": refs,
            "machine_origin": machine,
            "provenance": regime["descriptive_metadata"] if machine else None,
        }


def execute_regime_delete(conn, name: str, force: bool = False) -> Dict[str, Any]:
    """Delete for real, dependency order: labels first (the RESTRICT FK),
    then the definition. Machine-origin regimes require ``force``; the
    discovery ledger is never touched either way."""
    with create_span("regimes.deletion.execute", regime=name,
                     force=force) as span:
        plan = plan_regime_delete(conn, name)
        if plan["machine_origin"] and not force:
            raise RegimeDeleteError(
                f"regime {name!r} is discovery-admitted (machine origin) — "
                f"its definition is the landed artifact of an audited search. "
                f"Re-run with --force to delete it anyway (the candidate "
                f"ledger is never touched); `regime archive` is the "
                f"recommended exit")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM regime_labels WHERE regime_id = %s",
                        (plan["regime"]["id"],))
            labels_deleted = cur.rowcount
            cur.execute("DELETE FROM regime_definitions WHERE id = %s",
                        (plan["regime"]["id"],))
        set_attributes(span, labels_deleted=labels_deleted)
        return {**plan, "labels_deleted": labels_deleted}


def _get_run(conn, run_id: int) -> Dict[str, Any]:
    from gefion.regimes.discovery import ledger
    try:
        return ledger.get_run(conn, run_id)
    except Exception as exc:
        raise RegimeDeleteError(str(exc)) from exc


def plan_run_delete(conn, run_id: int) -> Dict[str, Any]:
    """The dry-run for a discovery run: what the DB cascade would remove
    (candidates, trust grades, diagnostics, SPA re-verdicts) and the blocker
    that refuses execution (admissions). Changes nothing."""
    with create_span("regimes.deletion.plan_run", run_id=run_id) as span:
        run = _get_run(conn, run_id)
        counts = {}
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM regime_candidates WHERE run_id = %s",
                        (run["id"],))
            counts["candidates"] = cur.fetchone()[0]
            cur.execute(
                """SELECT count(*) FROM regime_candidates
                   WHERE run_id = %s AND verdict = 'admitted'""", (run["id"],))
            counts["admitted"] = cur.fetchone()[0]
            cur.execute(
                """SELECT count(*) FROM regime_trust_grades g
                   JOIN regime_candidates c ON c.id = g.candidate_id
                   WHERE c.run_id = %s""", (run["id"],))
            counts["trust_grades"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM discovery_diagnostics WHERE run_id = %s",
                        (run["id"],))
            counts["diagnostics"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM spa_reverdicts WHERE run_id = %s",
                        (run["id"],))
            counts["spa_reverdicts"] = cur.fetchone()[0]
        set_attributes(span, **counts)
        return {"run": {"id": run["id"], "name": run["name"],
                        "status": run["status"]}, **counts}


def execute_run_delete(conn, run_id: int) -> Dict[str, Any]:
    """Delete a discovery run and let the DB cascade remove its ledger rows.

    Refuses ALWAYS (no force door) if the run has admissions: an admitted
    run's candidate ledger is the multiple-testing audit trail behind a live
    or once-live artifact — the accounting must survive."""
    with create_span("regimes.deletion.execute_run", run_id=run_id) as span:
        plan = plan_run_delete(conn, run_id)
        if plan["admitted"] > 0:
            raise RegimeDeleteError(
                f"run {plan['run']['id']} '{plan['run']['name']}' has "
                f"{plan['admitted']} admitted candidate(s) — its ledger is "
                f"the audit trail behind an admitted artifact and cannot be "
                f"deleted (there is deliberately no --force for this)")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM regime_discovery_runs WHERE id = %s",
                        (plan["run"]["id"],))
        set_attributes(span, candidates_deleted=plan["candidates"])
        return plan
