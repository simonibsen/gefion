"""Rule evaluation: predicates → exclusion intervals (spec 015).

The attribute registry declares what rules may reference — static identity
attributes on `stocks` and time-varying market attributes with true daily
history. Static rules yield one open-ended interval per matching symbol;
time-varying rules yield gaps-and-islands intervals whose trailing island
stays open-ended. `market_cap` et al. join the registry only when
fundamentals vintages exist (research R3) — evaluating today's snapshot
against history would be look-ahead bias.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

# Exclusions from static attributes cover all time; a fixed floor keeps
# materialization deterministic even for symbols without price history.
STATIC_FLOOR = date(1900, 1, 1)

CATEGORICAL_OPS = {"eq", "ne", "in", "is_missing"}
NUMERIC_OPS = {"lt", "lte", "gt", "gte", "between"}

ATTRIBUTES: Dict[str, Dict] = {
    "asset_type": {"kind": "static", "column": "asset_type",
                   "type": "categorical"},
    "industry": {"kind": "static", "column": "industry",
                 "type": "categorical"},
    "sector": {"kind": "static", "column": "sector", "type": "categorical"},
    "exchange": {"kind": "static", "column": "exchange",
                 "type": "categorical"},
    "status": {"kind": "static", "column": "status", "type": "categorical"},
    "close": {"kind": "time_varying", "type": "numeric"},
}

_NUMERIC_SQL_OPS = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}

Interval = Tuple[int, date, Optional[date]]  # (data_id, from, to|None)


def ops_for_attribute(attribute: str) -> set:
    """Valid operators for a registered attribute."""
    kind = ATTRIBUTES[attribute]["type"]
    return CATEGORICAL_OPS if kind == "categorical" else NUMERIC_OPS


def _static_intervals(conn, rule: Dict) -> List[Interval]:
    col = ATTRIBUTES[rule["attribute"]]["column"]  # registry-owned, not user input
    op, value = rule["op"], rule.get("value")
    if op == "eq":
        cond, params = f"{col} = %s", [value]
    elif op == "ne":
        # NULL never matches ne: absence of data is not evidence of exclusion
        cond, params = f"{col} <> %s", [value]
    elif op == "in":
        cond, params = f"{col} = ANY(%s)", [value]
    else:  # is_missing (validated upstream)
        cond, params = f"{col} IS NULL", []
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM stocks WHERE {cond}", params)
        return [(r[0], STATIC_FLOOR, None) for r in cur.fetchall()]


def _close_intervals(conn, rule: Dict) -> List[Interval]:
    op, value = rule["op"], rule["value"]
    if op == "between":
        pred, params = "o.close BETWEEN %s AND %s", list(value)
    else:
        pred, params = f"o.close {_NUMERIC_SQL_OPS[op]} %s", [value]
    # Gaps-and-islands over each symbol's trading dates; the island touching
    # the symbol's last bar stays open-ended (the exclusion is ongoing).
    sql = f"""
        WITH pred AS (
            SELECT o.data_id, o.date, ({pred}) AS excl
            FROM stock_ohlcv o
            WHERE o.close IS NOT NULL
        ), lagged AS (
            SELECT data_id, date, excl,
                   LAG(excl) OVER (PARTITION BY data_id ORDER BY date) AS prev_excl
            FROM pred
        ), runs AS (
            SELECT data_id, date, excl,
                   SUM(CASE WHEN prev_excl IS NULL OR excl <> prev_excl
                            THEN 1 ELSE 0 END)
                       OVER (PARTITION BY data_id ORDER BY date) AS grp
            FROM lagged
        ), islands AS (
            SELECT data_id, grp, excl,
                   MIN(date) AS from_d, MAX(date) AS to_d
            FROM runs GROUP BY data_id, grp, excl
        ), last_bar AS (
            SELECT data_id, MAX(date) AS sym_last FROM pred GROUP BY data_id
        )
        SELECT i.data_id, i.from_d,
               CASE WHEN i.to_d = lb.sym_last THEN NULL ELSE i.to_d END
        FROM islands i JOIN last_bar lb USING (data_id)
        WHERE i.excl
        ORDER BY i.data_id, i.from_d
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def rule_intervals(conn, rule: Dict) -> List[Interval]:
    """Evaluate one exclude-rule to its exclusion intervals."""
    spec = ATTRIBUTES[rule["attribute"]]
    with create_span("universe.evaluate_rule", rule=rule["name"],
                     attribute=rule["attribute"]) as span:
        if spec["kind"] == "static":
            out = _static_intervals(conn, rule)
        else:
            out = _close_intervals(conn, rule)
        set_attributes(span, interval_count=len(out))
    return out
