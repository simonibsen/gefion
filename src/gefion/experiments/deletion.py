"""Experiment deletion door (#76 audit).

Dry-run default reporting the blast radius; --confirm executes. Trials
cascade via the existing FK; experimental features OWNED by the experiment
(is_experimental, source_experiment_id) cascade with their values and
functions. Refusals are the policy speaking: a PROMOTED experiment refuses
ALWAYS (production influence is an audit fact — deliberately no force
gate); a promoted feature refuses; regime_discovery experiments belong to
their own guarded door; child experiments block until deleted first. The
cycle row is a soft reference — reported, never mutated.
"""
from __future__ import annotations

from typing import Any, Dict, List

from gefion.observability import create_span, set_attributes


def _experiment_row(conn, experiment_id: int) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, experiment_type, status, cycle_id, promoted_at "
            "FROM experiments WHERE id = %s", (experiment_id,))
        r = cur.fetchone()
    if r is None:
        raise ValueError(f"no experiment with id {experiment_id}")
    return {"id": r[0], "name": r[1], "experiment_type": r[2], "status": r[3],
            "cycle_id": r[4], "promoted_at": str(r[5]) if r[5] else None}


def _owned_features(conn, experiment_id: int) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, is_experimental, promoted_at "
            "FROM feature_definitions WHERE source_experiment_id = %s "
            "ORDER BY name", (experiment_id,))
        return [{"name": r[0], "is_experimental": r[1],
                 "promoted_at": str(r[2]) if r[2] else None}
                for r in cur.fetchall()]


def plan_experiment_delete(conn, experiment_id: int) -> Dict[str, Any]:
    """Dry-run: the full blast radius, changing nothing."""
    with create_span("experiments.deletion.plan",
                     experiment_id=experiment_id) as span:
        exp = _experiment_row(conn, experiment_id)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM experiment_trials "
                        "WHERE experiment_id = %s", (experiment_id,))
            trials = cur.fetchone()[0]
            cur.execute("SELECT id, name FROM experiments "
                        "WHERE parent_experiment_id = %s", (experiment_id,))
            children = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
        features = _owned_features(conn, experiment_id)
        plan = {
            "experiment": exp,
            "trials": trials,
            "children": children,
            "experimental_features": [f["name"] for f in features
                                      if f["is_experimental"]],
            "promoted_features": [f["name"] for f in features
                                  if f["promoted_at"]],
            "promoted": exp["promoted_at"] is not None,
            "cycle_reference": exp["cycle_id"],   # soft: reported, never mutated
        }
        set_attributes(span, trials=trials, n_children=len(children))
        return plan


def execute_experiment_delete(conn, experiment_id: int) -> Dict[str, Any]:
    """Delete the experiment, its trials, and its OWNED experimental
    features. No force gate exists for the refusals below — each protects
    an audit fact or a separate door's territory."""
    with create_span("experiments.deletion.execute",
                     experiment_id=experiment_id) as span:
        plan = plan_experiment_delete(conn, experiment_id)
        exp = plan["experiment"]
        if exp["experiment_type"] == "regime_discovery":
            raise ValueError(
                f"experiment {experiment_id} is a regime_discovery "
                "experiment — its artifacts belong to the discovery ledger; "
                "use `gefion regime discover delete` (its own guarded door)")
        if plan["promoted"]:
            raise ValueError(
                f"experiment {experiment_id} was promoted "
                f"({exp['promoted_at']}) — production influence is an audit "
                "fact and promoted experiments refuse deletion, deliberately "
                "with no force flag")
        if plan["promoted_features"]:
            raise ValueError(
                f"experiment {experiment_id} produced promoted feature(s) "
                f"{plan['promoted_features']} — promoted features refuse "
                "deletion with their experiment")
        if plan["children"]:
            raise ValueError(
                f"experiment {experiment_id} has child experiment(s) "
                f"{[c['id'] for c in plan['children']]} — delete children "
                "first (dependency order)")

        experimental = plan["experimental_features"]
        deleted: Dict[str, Any] = {"trials": plan["trials"],
                                   "experimental_features": len(experimental)}
        with conn.transaction():
            with conn.cursor() as cur:
                if experimental:
                    cur.execute(
                        """DELETE FROM computed_features WHERE feature_id IN
                           (SELECT id FROM feature_definitions
                            WHERE name = ANY(%s))""", (experimental,))
                    deleted["feature_values"] = cur.rowcount
                    cur.execute("DELETE FROM feature_definitions "
                                "WHERE name = ANY(%s)", (experimental,))
                    cur.execute("DELETE FROM feature_functions "
                                "WHERE name = ANY(%s)", (experimental,))
                # trials cascade via FK ON DELETE CASCADE
                cur.execute("DELETE FROM experiments WHERE id = %s",
                            (experiment_id,))
        set_attributes(span, **{k: v for k, v in deleted.items()
                                if isinstance(v, int)})
        return deleted
