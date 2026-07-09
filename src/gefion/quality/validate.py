"""Batch validation pass (008, T011 — US1).

Rides the covered write paths as a bounded, in-memory pass over just-written
values: tier 1 (definitional bounds) always, tier 2 (cross-field recompute
against trusted stored data) when a derivation and its inputs are available.
Corroboration tiers (3–4) run in the backfill where full history/cross-section
is at hand (US5).

The write paths call this inside a guard: a validation error is counted and
reported, never raised into the write (FR-303) — silent garbage through is the
defect this fixes, but a validator bug must never cost an ingest.
"""
from __future__ import annotations

import ast
import operator
from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes
from gefion.quality import rules
from gefion.quality.catalog import Catalog, Metric

_OPS = {ast.Div: operator.truediv, ast.Mult: operator.mul,
        ast.Add: operator.add, ast.Sub: operator.sub}


def _safe_eval(expr: str, ns: Dict[str, Optional[float]]) -> Optional[float]:
    """Evaluate a restricted arithmetic expression (names, +−*/, unary minus).
    Returns None if any operand is missing or a division by zero occurs."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if left is None or right is None:
                return None
            try:
                return _OPS[type(node.op)](left, right)
            except ZeroDivisionError:
                return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            v = ev(node.operand)
            return None if v is None else -v
        if isinstance(node, ast.Name):
            return ns.get(node.id)
        if isinstance(node, ast.Constant):
            return float(node.value)
        raise ValueError(f"unsupported expression: {expr!r}")
    return ev(ast.parse(expr, mode="eval"))


def _resolve_input(conn, source: str, entity_id: int, target_date,
                   overview: Dict[str, Any],
                   parsed: Dict[str, Any]) -> Optional[float]:
    """Resolve one derivation input `<source_table>.<field>` to a float."""
    table, _, field = source.partition(".")
    if table == "overview":
        try:
            return float(overview.get(field))
        except (TypeError, ValueError):
            return None
    if table == "stock_ohlcv":
        with conn.cursor() as cur:
            cur.execute(
                "SELECT close FROM stock_ohlcv WHERE data_id = %s AND date <= %s "
                "ORDER BY date DESC LIMIT 1", (entity_id, target_date))
            row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None
    if table == "stocks_fundamentals":
        v = parsed.get(field)
        return float(v) if v is not None else None
    return None


def _evaluate(conn, cat: Catalog, metric: Metric, value: float,
              entity_id: int, target_date, overview: Dict[str, Any],
              parsed: Dict[str, Any]):
    """Tier 1 then (if it passed and a derivation exists) tier 2. First
    convicting tier wins — one finding per value."""
    r = rules.check_bounds(metric, value)
    if r is not None:
        return r
    if metric.derivation:
        ns = {var: _resolve_input(conn, src, entity_id, target_date,
                                  overview, parsed)
              for var, src in metric.derivation["inputs"].items()}
        recomputed = _safe_eval(metric.derivation["expression"], ns)
        tol = metric.derivation.get("tolerance_factor",
                                    cat.defaults["tolerance_factor"])
        return rules.check_cross_field(metric, value, recomputed, tol)
    return None


def validate_stock_values(conn, cat: Catalog, entity_id: int, target_date,
                          parsed: Dict[str, Any],
                          overview: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Findings entries for one stock's just-written fundamentals values."""
    with create_span("quality.validate.stock", entity_id=entity_id) as span:
        entries: List[Dict[str, Any]] = []
        for name, metric in cat.metrics.items():
            if metric.entity_table != "stocks":
                continue
            raw = parsed.get(metric.column)
            if raw is None:
                continue
            r = _evaluate(conn, cat, metric, float(raw), entity_id,
                          target_date, overview, parsed)
            if r is not None:
                entries.append({"entity_table": "stocks", "entity_id": entity_id,
                                "metric": name, "date": target_date, "result": r})
        set_attributes(span, n_findings=len(entries))
        return entries


def validate_macro_values(cat: Catalog, series_name: str, series_id: int,
                          rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Findings entries for a macro series' just-ingested values (tier 1)."""
    with create_span("quality.validate.macro", series=series_name) as span:
        entries: List[Dict[str, Any]] = []
        for name, metric in cat.metrics.items():
            if metric.entity_table != "macro_series" or metric.series != series_name:
                continue
            for row in rows:
                value = row.get("value")
                if value is None:
                    continue
                r = rules.check_bounds(metric, float(value))
                if r is not None:
                    entries.append({"entity_table": "macro_series",
                                    "entity_id": series_id, "metric": name,
                                    "date": row["date"], "result": r})
        set_attributes(span, n_findings=len(entries))
        return entries
