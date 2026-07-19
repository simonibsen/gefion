"""Membership materialization, refresh, guard, and explain (spec 015).

Membership is stored in COMPLEMENT form (universe_exclusions): a symbol is
a member as-of D iff no interval covers (universe, symbol, D). Refresh
reconciles deterministically — unchanged intervals untouched, vanished
intervals deleted, new intervals inserted — and REFUSES (FR-010) rather
than silently gutting every downstream consumer.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from gefion.observability import create_span, set_attributes
from gefion.universe.definitions import get_universe
from gefion.universe.evaluate import STATIC_FLOOR, rule_intervals

logger = logging.getLogger(__name__)

# One refresh may not grow the excluded fraction by more than this many
# percentage points (guards against a fat-fingered rule); the very first
# refresh is exempt (populating IS the point). Empty is refused always.
SHRINK_GUARD_PP = 0.25

Row = Tuple[int, str, date, Optional[date]]  # (data_id, rule, from, to)


class UniverseGuardError(RuntimeError):
    """A refresh was refused rather than applied (FR-010)."""


def _resolve_definition(conn, name: Optional[str]) -> Dict:
    if name == "all":
        raise ValueError("'all' is the unfiltered population; it has no "
                         "membership to refresh")
    if name is None:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM universe_definitions "
                        "WHERE is_default AND enabled")
            row = cur.fetchone()
        if row is None:
            raise ValueError("no default universe defined; run db-init to "
                             "seed modeling_default")
        name = row[0]
    u = get_universe(conn, name)
    if u is None:
        raise ValueError(f"no universe named '{name}'")
    return u


def _desired_rows(conn, definition: Dict) -> Set[Row]:
    desired: Set[Row] = set()
    for rule in definition["rules"]:
        for data_id, d_from, d_to in rule_intervals(conn, rule):
            desired.add((data_id, rule["name"], d_from, d_to))
    include_ids: Set[int] = set()
    with conn.cursor() as cur:
        for pin in definition["pins"]:
            cur.execute("SELECT id FROM stocks WHERE symbol = %s",
                        (pin["symbol"],))
            row = cur.fetchone()
            if row is None:
                logger.warning("pin symbol %s not found; skipping",
                               pin["symbol"])
                continue
            if pin["action"] == "exclude":
                desired.add((row[0], f"pin:{pin['symbol']}", STATIC_FLOOR,
                             None))
            else:
                include_ids.add(row[0])
    # include pins beat rules
    return {r for r in desired if r[0] not in include_ids}


def _covers_today(row: Row, today: date) -> bool:
    return row[2] <= today and (row[3] is None or today <= row[3])


def refresh_universe(conn, name: Optional[str] = None,
                     force: bool = False) -> Dict:
    """Re-evaluate rules and reconcile exclusion intervals.

    Returns a delta report. Raises UniverseGuardError when the result would
    empty the universe (always) or shrink membership beyond the guard
    threshold in one step (unless force).
    """
    definition = _resolve_definition(conn, name)
    universe_id, uname = definition["id"], definition["name"]
    today = date.today()
    with create_span("universe.refresh", universe=uname) as span:
        desired = _desired_rows(conn, definition)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stocks")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT data_id, rule_name, excluded_from, excluded_to "
                "FROM universe_exclusions WHERE universe_id = %s",
                (universe_id,))
            existing: Set[Row] = {tuple(r) for r in cur.fetchall()}

        new_excluded = {r[0] for r in desired if _covers_today(r, today)}
        if total and len(new_excluded) >= total:
            raise UniverseGuardError(
                f"refusing: refresh would leave universe '{uname}' EMPTY "
                f"({len(new_excluded)}/{total} symbols excluded)")
        if existing:
            old_excluded = {r[0] for r in existing
                            if _covers_today(r, today)}
            old_frac = len(old_excluded) / total if total else 0.0
            new_frac = len(new_excluded) / total if total else 0.0
            if new_frac - old_frac > SHRINK_GUARD_PP and not force:
                raise UniverseGuardError(
                    f"refusing: refresh would shrink universe '{uname}' "
                    f"membership by {100 * (new_frac - old_frac):.1f} "
                    f"percentage points ({len(old_excluded)} -> "
                    f"{len(new_excluded)} of {total} excluded). "
                    "Re-run with force to apply.")

        to_add = desired - existing
        to_remove = existing - desired
        with conn.transaction():
            with conn.cursor() as cur:
                for data_id, rule, d_from, _ in to_remove:
                    cur.execute(
                        "DELETE FROM universe_exclusions WHERE universe_id = %s "
                        "AND data_id = %s AND rule_name = %s "
                        "AND excluded_from = %s",
                        (universe_id, data_id, rule, d_from))
                for data_id, rule, d_from, d_to in to_add:
                    cur.execute(
                        "INSERT INTO universe_exclusions (universe_id, data_id, "
                        "rule_name, excluded_from, excluded_to) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (universe_id, data_id, rule, d_from, d_to))
        delta = {
            "universe": uname,
            "fingerprint": definition["fingerprint"],
            "added": len(to_add),
            "removed": len(to_remove),
            "total_symbols": total,
            "currently_excluded": len(new_excluded),
            "members": total - len(new_excluded),
        }
        set_attributes(span, **{k: v for k, v in delta.items()
                                if isinstance(v, (int, str))})
    logger.info("universe %s refreshed: +%d -%d intervals, %d/%d excluded",
                uname, delta["added"], delta["removed"],
                delta["currently_excluded"], total)
    return delta


def explain_symbol(conn, symbol: str, name: Optional[str] = None,
                   as_of: Optional[date] = None) -> Dict:
    """Why is/isn't SYMBOL in the universe as of a date? (SC-003)"""
    definition = _resolve_definition(conn, name)
    as_of = as_of or date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM stocks WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown symbol '{symbol}'")
        cur.execute(
            "SELECT rule_name, excluded_from, excluded_to "
            "FROM universe_exclusions WHERE universe_id = %s AND data_id = %s "
            "AND excluded_from <= %s "
            "AND (excluded_to IS NULL OR %s <= excluded_to) "
            "ORDER BY rule_name", (definition["id"], row[0], as_of, as_of))
        covering = cur.fetchall()
    reasons = {r["name"]: r["reason"] for r in definition["rules"]}
    for pin in definition["pins"]:
        reasons[f"pin:{pin['symbol']}"] = pin["reason"]
    return {
        "symbol": symbol,
        "universe": definition["name"],
        "as_of": as_of.isoformat(),
        "member": not covering,
        "excluded_by": [
            {"rule": r[0], "reason": reasons.get(r[0], ""),
             "from": r[1].isoformat(),
             "to": r[2].isoformat() if r[2] else None}
            for r in covering
        ],
    }


def membership_summary(conn, name: Optional[str] = None) -> Dict:
    """Headline counts for a universe: members, excluded, by rule, flaps."""
    definition = _resolve_definition(conn, name)
    today = date.today()
    with create_span("universe.summary", universe=definition["name"]) as span:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stocks")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT rule_name, COUNT(DISTINCT data_id) "
                "FROM universe_exclusions WHERE universe_id = %s "
                "AND excluded_from <= %s "
                "AND (excluded_to IS NULL OR %s <= excluded_to) "
                "GROUP BY rule_name", (definition["id"], today, today))
            by_rule = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "SELECT COUNT(DISTINCT data_id) FROM universe_exclusions "
                "WHERE universe_id = %s AND excluded_from <= %s "
                "AND (excluded_to IS NULL OR %s <= excluded_to)",
                (definition["id"], today, today))
            excluded = cur.fetchone()[0]
            # flap count: total intervals under a rule for symbols that
            # entered/exited more than once (surfaces churn — spec edge case)
            cur.execute(
                "SELECT rule_name, SUM(n) FROM ("
                "  SELECT rule_name, data_id, COUNT(*) AS n "
                "  FROM universe_exclusions WHERE universe_id = %s "
                "  GROUP BY rule_name, data_id HAVING COUNT(*) > 1"
                ") t GROUP BY rule_name", (definition["id"],))
            flaps = {r[0]: int(r[1]) for r in cur.fetchall()}
        summary = {
            "universe": definition["name"],
            "fingerprint": definition["fingerprint"],
            "total_symbols": total,
            "currently_excluded": excluded,
            "members": total - excluded,
            "by_rule": by_rule,
            "flaps": flaps,
        }
        set_attributes(span, excluded=excluded, members=summary["members"])
    return summary
