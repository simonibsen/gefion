"""Feature definition/function deletion doors (#76 audit).

Dependency order: values → definition; a function deletes only when no
definition routes to it. A definition referenced by a regime expression
refuses — its labels would become unrecomputable (archive or delete the
regime first). Dataset provenance (ml_datasets.feature_names) and the
discovery ledger are soft references: reported, never mutated. The
market-candidates ledger has no FK by design and survives the function.
"""
from __future__ import annotations

from typing import Any, Dict

from gefion.observability import create_span, set_attributes


def _definition_row(conn, name: str) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, function_name, entity_table, active, "
            "is_experimental FROM feature_definitions WHERE name = %s",
            (name,))
        r = cur.fetchone()
    if r is None:
        raise ValueError(f"no feature definition named {name!r}")
    return {"id": r[0], "name": r[1], "function_name": r[2],
            "entity_table": r[3], "active": r[4], "is_experimental": r[5]}


def plan_definition_delete(conn, name: str) -> Dict[str, Any]:
    """Dry-run: the full blast radius, changing nothing."""
    with create_span("features.deletion.plan_definition",
                     feature=name) as span:
        d = _definition_row(conn, name)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM computed_features "
                        "WHERE feature_id = %s", (d["id"],))
            values = cur.fetchone()[0]
            # regimes whose expression mentions the feature name — blocker
            cur.execute(
                "SELECT name FROM regime_definitions "
                "WHERE expression::text LIKE %s ORDER BY name",
                (f'%"{name}"%',))
            regimes = [r[0] for r in cur.fetchall()]
            # dataset provenance — soft, reported only
            cur.execute(
                "SELECT name, version FROM ml_datasets "
                "WHERE %s = ANY(feature_names) ORDER BY name, version",
                (name,))
            datasets = [f"{r[0]}:{r[1]}" for r in cur.fetchall()]
        plan = {"definition": d, "values": values,
                "regime_references": regimes,
                "dataset_references": datasets}
        set_attributes(span, values=values, n_regimes=len(regimes))
        return plan


def execute_definition_delete(conn, name: str) -> Dict[str, Any]:
    """Delete values then the definition. Regime references refuse; the
    routed function survives (its own door)."""
    with create_span("features.deletion.execute_definition",
                     feature=name) as span:
        plan = plan_definition_delete(conn, name)
        if plan["regime_references"]:
            raise ValueError(
                f"feature {name!r} is referenced by regime definition(s) "
                f"{plan['regime_references']} — their labels would become "
                "unrecomputable; archive or delete the regime first")
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM computed_features "
                            "WHERE feature_id = %s", (plan["definition"]["id"],))
                values = cur.rowcount
                cur.execute("DELETE FROM feature_definitions WHERE id = %s",
                            (plan["definition"]["id"],))
        set_attributes(span, values=values)
        return {"values": values, "definition": name,
                "dataset_references": plan["dataset_references"]}


def plan_function_delete(conn, name: str) -> Dict[str, Any]:
    with create_span("features.deletion.plan_function",
                     function=name) as span:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, scope, enabled "
                        "FROM feature_functions WHERE name = %s", (name,))
            r = cur.fetchone()
            if r is None:
                raise ValueError(f"no feature function named {name!r}")
            cur.execute("SELECT name FROM feature_definitions "
                        "WHERE function_name = %s ORDER BY name", (name,))
            routed = [x[0] for x in cur.fetchall()]
        set_attributes(span, n_routed=len(routed))
        return {"function": {"id": r[0], "name": r[1], "scope": r[2],
                             "enabled": r[3]},
                "routed_definitions": routed}


def execute_function_delete(conn, name: str) -> Dict[str, Any]:
    """Delete a function only when no definition routes to it. The
    candidates ledger (promoted_function_id, no FK by design) is never
    touched — the audit survives the artifact."""
    with create_span("features.deletion.execute_function",
                     function=name) as span:
        plan = plan_function_delete(conn, name)
        if plan["routed_definitions"]:
            raise ValueError(
                f"function {name!r} is routed to by definition(s) "
                f"{plan['routed_definitions']} — delete those definitions "
                "first (dependency order)")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM feature_functions WHERE name = %s",
                        (name,))
        conn.commit()
        set_attributes(span, deleted=True)
        return {"deleted": True, "function": name}
