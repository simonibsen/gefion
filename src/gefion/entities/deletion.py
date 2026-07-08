"""Registry-driven entity deletion (007, T009 — US5).

Deletion is first-class (owner principle): anything created must be cleanly
deletable with its associated data. This replaces the retired FK cascade —
which only ever covered stocks — with a uniform, dependency-aware delete
across entity kinds: dry-run by default reporting the FULL blast radius
(registry edges + hard-FK dependents from pg_constraint), confirm-to-execute
deleting feature values (per the registry) before the entity row, refusing on
RESTRICT/NO-ACTION blockers with the list. Audit ledgers are never in scope —
deleting an artifact never deletes accounting (issue #76's declared
exception).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from psycopg import sql

from gefion.entities.registry import entity_identifier
from gefion.observability import create_span, set_attributes

# Natural keys per entity table; anything else resolves by integer id.
_NATURAL_KEYS = {"stocks": "symbol", "macro_series": "name"}

# FK delete rules that block deletion when dependent rows exist
_BLOCKING_RULES = {"a": "NO ACTION", "r": "RESTRICT"}


class EntityDeleteError(ValueError):
    """Raised on an unknown entity, unknown table, or blocked deletion."""


def _resolve(conn, entity_table: str, key: str) -> Tuple[int, str]:
    """(entity id, display key) — natural key where declared, else integer id."""
    from gefion.entities.registry import EntityTableError
    try:
        ident = entity_identifier(conn, entity_table)  # validates the table
    except EntityTableError as exc:
        raise EntityDeleteError(str(exc)) from exc
    natural = _NATURAL_KEYS.get(entity_table)
    with conn.cursor() as cur:
        if natural and not key.isdigit():
            cur.execute(
                sql.SQL("SELECT id FROM {} WHERE {} = %s").format(
                    ident, sql.Identifier(natural)),
                (key,),
            )
        else:
            if not key.isdigit():
                raise EntityDeleteError(
                    f"{entity_table!r} has no natural key — pass the integer id")
            cur.execute(
                sql.SQL("SELECT id FROM {} WHERE id = %s").format(ident),
                (int(key),),
            )
        row = cur.fetchone()
    if row is None:
        raise EntityDeleteError(f"no {entity_table} entity for key {key!r}")
    return row[0], key


def _fk_dependents(conn, entity_table: str, entity_id: int) -> List[Dict[str, Any]]:
    """Hard-FK dependents of this entity row, with delete rules and row counts.

    TimescaleDB chunk clones of a parent hypertable constraint are filtered —
    they are internals of the parent table's own constraint.
    """
    out: List[Dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.conrelid::regclass::text AS child,
                   a.attname AS fk_column,
                   c.confdeltype
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid
                               AND a.attnum = c.conkey[1]
            WHERE c.contype = 'f' AND c.confrelid = %s::regclass
            """,
            (entity_table,),
        )
        constraints = [
            (child, col, rule) for child, col, rule in cur.fetchall()
            if not child.startswith("_timescaledb")
        ]
        for child, col, rule in constraints:
            cur.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(
                    sql.Identifier(*child.split(".")), sql.Identifier(col)),
                (entity_id,),
            )
            count = cur.fetchone()[0]
            out.append({
                "table": child,
                "fk_column": col,
                "on_delete": _BLOCKING_RULES.get(rule, "CASCADE" if rule == "c" else rule),
                "rows": count,
            })
    return out


def plan_delete(conn, entity_table: str, key: str) -> Dict[str, Any]:
    """The dry-run: the FULL blast radius, changing nothing.

    Registry edges (feature values per feature declaring this entity table),
    hard-FK dependents with their delete rules, and the blockers that would
    refuse execution.
    """
    with create_span("entities.deletion.plan",
                     entity_table=entity_table) as span:
        entity_id, display = _resolve(conn, entity_table, key)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT fd.name, count(cf.*)
                   FROM feature_definitions fd
                   LEFT JOIN computed_features cf
                     ON cf.feature_id = fd.id AND cf.data_id = %s
                   WHERE fd.entity_table = %s
                   GROUP BY fd.name ORDER BY fd.name""",
                (entity_id, entity_table),
            )
            feature_values = [{"feature": name, "count": count}
                              for name, count in cur.fetchall()]
        dependents = _fk_dependents(conn, entity_table, entity_id)
        blockers = [d for d in dependents
                    if d["on_delete"] in _BLOCKING_RULES.values() and d["rows"] > 0]
        set_attributes(span, entity_id=entity_id,
                       n_feature_values=sum(f["count"] for f in feature_values),
                       n_blockers=len(blockers))
        return {
            "entity": {"table": entity_table, "id": entity_id, "key": display},
            "feature_values": feature_values,
            "fk_dependents": dependents,
            "blockers": blockers,
        }


def execute_delete(conn, entity_table: str, key: str) -> Dict[str, Any]:
    """Delete for real: feature values (per registry) first, then the entity
    row. Refuses with the blocker list if any RESTRICT/NO-ACTION dependent has
    rows; CASCADE dependents are handled by the database."""
    with create_span("entities.deletion.execute",
                     entity_table=entity_table) as span:
        plan = plan_delete(conn, entity_table, key)
        if plan["blockers"]:
            names = [f"{b['table']} ({b['rows']} rows, {b['on_delete']})"
                     for b in plan["blockers"]]
            raise EntityDeleteError(
                f"deletion blocked by dependents: {', '.join(names)} — remove "
                "those rows first (or via their own lifecycle commands)")
        entity_id = plan["entity"]["id"]
        ident = entity_identifier(conn, entity_table)
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM computed_features
                   WHERE data_id = %s AND feature_id IN (
                       SELECT id FROM feature_definitions WHERE entity_table = %s)""",
                (entity_id, entity_table),
            )
            values_deleted = cur.rowcount
            cur.execute(
                sql.SQL("DELETE FROM {} WHERE id = %s").format(ident),
                (entity_id,),
            )
        set_attributes(span, entity_id=entity_id,
                       feature_values_deleted=values_deleted)
        return {
            "entity": plan["entity"],
            "feature_values_deleted": values_deleted,
            "fk_dependents": plan["fk_dependents"],
        }
