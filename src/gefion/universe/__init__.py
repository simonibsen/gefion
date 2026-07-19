"""The modeling-universe chokepoint (spec 015).

One gate through which every modeling cross-section consumer obtains its
population. Resolution: explicit name wins, reserved name 'all' bypasses
filtering, otherwise the default universe. Unknown/disabled universes
REFUSE loudly — never a silent fallback to "everything". Ingestion,
quality scanning, and raw price storage are never filtered (FR-006): the
system observes everything and models a subset.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

ALL_UNIVERSE = "all"


class UniverseResolutionError(ValueError):
    """A consumer named a universe that cannot be resolved."""


@dataclass(frozen=True)
class ResolvedUniverse:
    """A resolved population choice; universe_id None means unfiltered."""
    name: str
    universe_id: Optional[int]
    fingerprint: Optional[str]

    def provenance(self) -> dict:
        """The stamp recorded on datasets/experiments/model artifacts."""
        return {"universe_name": self.name,
                "universe_fingerprint": self.fingerprint}


def resolve_universe(conn, name: Optional[str] = None) -> ResolvedUniverse:
    """Resolve a universe choice: name > 'all' > default (FR-005)."""
    if name == ALL_UNIVERSE:
        return ResolvedUniverse(ALL_UNIVERSE, None, None)
    with conn.cursor() as cur:
        if name is None:
            cur.execute("SELECT id, name, fingerprint FROM universe_definitions "
                        "WHERE is_default AND enabled")
            row = cur.fetchone()
            if row is None:
                raise UniverseResolutionError(
                    "no default universe defined — run 'gefion db-init' to "
                    "seed modeling_default, or name a universe explicitly "
                    "('all' for the unfiltered population)")
            return ResolvedUniverse(row[1], row[0], row[2])
        cur.execute("SELECT id, name, fingerprint, enabled "
                    "FROM universe_definitions WHERE name = %s", (name,))
        row = cur.fetchone()
        if row is None or not row[3]:
            cur.execute("SELECT name FROM universe_definitions "
                        "WHERE enabled ORDER BY name")
            valid = [r[0] for r in cur.fetchall()] + [ALL_UNIVERSE]
            state = "disabled" if row else "unknown"
            raise UniverseResolutionError(
                f"universe '{name}' is {state}. Valid universes: "
                f"{', '.join(valid)}")
    return ResolvedUniverse(row[1], row[0], row[2])


def universe_exclusion_clause(universe_id: Optional[int], date_expr: str,
                              data_id_expr: str) -> Tuple[str, list]:
    """SQL fragment for streaming consumers: TRUE for the unfiltered
    population, else a NOT EXISTS probe against covering exclusion
    intervals. date_expr/data_id_expr are trusted SQL expressions from the
    calling query (e.g. 'o.date', 'o.data_id'), never user input."""
    if universe_id is None:
        return "TRUE", []
    sql = (
        "NOT EXISTS (SELECT 1 FROM universe_exclusions ue "
        "WHERE ue.universe_id = %s "
        f"AND ue.data_id = {data_id_expr} "
        f"AND ue.excluded_from <= {date_expr} "
        f"AND (ue.excluded_to IS NULL OR {date_expr} <= ue.excluded_to))"
    )
    return sql, [universe_id]


def _member_query(universe_id: Optional[int], as_of: date,
                  select_expr: str) -> Tuple[str, list]:
    clause, params = universe_exclusion_clause(universe_id, "%s::date", "s.id")
    if universe_id is None:
        return f"SELECT {select_expr} FROM stocks s ORDER BY s.symbol", []
    # the clause uses two date placeholders when filtered
    return (f"SELECT {select_expr} FROM stocks s WHERE " + clause +
            " ORDER BY s.symbol"), [params[0], as_of, as_of]


def universe_members(conn, name: Optional[str] = None,
                     as_of: Optional[date] = None) -> List[str]:
    """Member symbols of a universe as of a date (default: today)."""
    resolved = resolve_universe(conn, name)
    as_of = as_of or date.today()
    with create_span("universe.members", universe=resolved.name) as span:
        sql, params = _member_query(resolved.universe_id, as_of, "s.symbol")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            out = [r[0] for r in cur.fetchall()]
        set_attributes(span, member_count=len(out))
    return out


def universe_member_ids(conn, name: Optional[str] = None,
                        as_of: Optional[date] = None) -> List[int]:
    """Member stock ids of a universe as of a date (default: today)."""
    resolved = resolve_universe(conn, name)
    as_of = as_of or date.today()
    sql, params = _member_query(resolved.universe_id, as_of, "s.id")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]
