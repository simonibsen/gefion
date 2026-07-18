"""Composite market functions — macro-of-macro (spec 014, US2).

A composite is a market function whose declared inputs are named macro
series ({"series": [...]}); the input shape is the executor discriminator —
no new scope value. This module owns registration (input validation, cycle
refusal at the door) and derive ordering (composites after their inputs).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from gefion.observability import create_span, set_attributes


def _composite_graph(conn) -> Dict[str, List[str]]:
    """Output series name -> declared input series names, for every
    market-scope function that declares series inputs."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, inputs FROM feature_functions "
                    "WHERE scope = 'market' AND inputs IS NOT NULL")
        graph: Dict[str, List[str]] = {}
        for name, inputs in cur.fetchall():
            if isinstance(inputs, str):
                inputs = json.loads(inputs)
            series = (inputs or {}).get("series") or []
            if series:
                graph[name] = list(series)
    return graph


def _reaches(graph: Dict[str, List[str]], src: str, target: str) -> bool:
    """DFS: can `src` reach `target` following input edges through
    composite-produced series?"""
    seen, stack = set(), [src]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


def validate_composite_inputs(conn, name: str, series: List[str],
                              graph: Optional[Dict[str, List[str]]] = None) -> None:
    """Refuse loudly at the door: empty inputs, unknown series, and
    dependency cycles (direct or transitive through composite-produced
    series) are all registration-time errors, never run-time surprises."""
    if not series:
        raise ValueError(
            f"composite {name!r} declares no input series — series is required")
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM macro_series WHERE name = ANY(%s)",
                    (list(series),))
        known = {r[0] for r in cur.fetchall()}
    unknown = [s for s in series if s not in known and s != name]
    if unknown:
        raise ValueError(
            f"composite {name!r} declares unknown input series {unknown} — "
            "every input must exist in the macro-series catalog")
    graph = dict(graph if graph is not None else _composite_graph(conn))
    graph[name] = list(series)
    for s in series:
        if s == name or _reaches(graph, s, name):
            raise ValueError(
                f"composite {name!r} would create a dependency cycle via "
                f"{s!r} — cycles refuse at registration")


def register_composite(conn, name: str, series: List[str], body: str,
                       description: Optional[str] = None,
                       allow_existing_series: bool = False) -> int:
    """Owner-authored composite: direct registration into feature_functions
    (the gate is for GENERATED code). Validates inputs and refuses cycles
    and name collisions. Returns the feature_functions id."""
    with create_span("macro.composites.register", composite=name) as span:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM feature_functions WHERE name = %s",
                        (name,))
            if cur.fetchone():
                raise ValueError(
                    f"a function named {name!r} already exists — "
                    "registration refuses to overwrite it")
        # A composite whose name matches an EXISTING series would become
        # that series' producer — a deliberate act, not a default
        if not allow_existing_series:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM macro_series WHERE name = %s", (name,))
                if cur.fetchone():
                    raise ValueError(
                        f"a macro series named {name!r} already exists — "
                        "pass allow_existing_series to deliberately become "
                        "its producer")
        validate_composite_inputs(conn, name, series)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feature_functions
                       (name, version, status, enabled, description, language,
                        function_body, inputs, scope)
                   VALUES (%s, 'v1', 'active', TRUE, %s, 'python', %s, %s,
                           'market')
                   RETURNING id""",
                (name, description or f"Composite over {', '.join(series)}",
                 body, json.dumps({"series": list(series)})))
            (fid,) = cur.fetchone()
        conn.commit()
        set_attributes(span, function_id=fid, n_inputs=len(series))
        return fid


def disabled_input_producers(conn, series: List[str]) -> List[str]:
    """Input series whose PRODUCING market function is disabled — a
    downstream composite must be a reported skip, never silent staleness.
    Provider-ingested series (no producing function) count as enabled."""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions "
                    "WHERE scope = 'market' AND enabled = FALSE "
                    "AND name = ANY(%s)", (list(series),))
        return sorted(r[0] for r in cur.fetchall())


def order_for_derive(conn, names: List[str]) -> List[str]:
    """Derive order: non-composites first (as given), then composites in
    topological order of their input graph — same-night inputs are fresh
    before any composite reads them."""
    graph = _composite_graph(conn)
    composites = [n for n in names if n in graph]
    rest = [n for n in names if n not in graph]

    ordered: List[str] = []
    state: Dict[str, int] = {}   # 0=unvisited 1=in-stack 2=done

    def visit(node: str) -> None:
        if state.get(node) == 2 or node not in composites:
            return
        if state.get(node) == 1:
            return               # cycle would have refused at registration
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep)
        state[node] = 2
        ordered.append(node)

    for n in composites:
        visit(n)
    return rest + ordered
