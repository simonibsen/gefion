"""Causal regime label computation (spec 005, T012).

Produces one causal, persistent label per (date, entity) for a RegimeDefinition.
Every label at time t depends only on data at or before t (FR-004). Supports the
US1 forms: a single market-scope quantile leaf (multi-bucket) and a boolean
composite of comparison leaves (binary). Detector-function and reference leaves,
and per-entity (non-market) scopes, are deferred to later increments.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from psycopg.types.json import Json  # noqa: F401  (reserved for future per-entity writes)

from gefion.observability import create_span, set_attributes
from gefion.regimes.definitions import (
    RegimeDefinition,
    has_detector_leaf,
    iter_leaves,
)

UNDEFINED = "undefined"

Series = List[Tuple[Any, float]]           # [(date, value), ...] sorted by date
LabelSeries = List[Tuple[Any, str]]        # [(date, label), ...]


# --- causal bucketing -----------------------------------------------------

def rolling_tercile_labels(series: Series, labels: List[str], window: int) -> LabelSeries:
    """Assign a bucket per point using terciles of the trailing `window` values.

    Causal: boundaries at date t use only values in (t-window, t]. The first
    `window - 1` points are UNDEFINED (insufficient history).
    """
    if len(labels) != 3:
        raise ValueError("rolling_tercile_labels expects exactly 3 bucket labels")
    out: LabelSeries = []
    values = [v for _, v in series]
    for i, (d, v) in enumerate(series):
        if i < window - 1:
            out.append((d, UNDEFINED))
            continue
        w = values[i - window + 1: i + 1]
        q1, q2 = np.quantile(w, [1 / 3, 2 / 3])
        if v <= q1:
            out.append((d, labels[0]))
        elif v <= q2:
            out.append((d, labels[1]))
        else:
            out.append((d, labels[2]))
    return out


def _comparison_bool(series: Series, cmp: str, value: float) -> List[Tuple[Any, bool]]:
    """Pointwise causal boolean for a comparison leaf."""
    ops = {
        "<": lambda x: x < value, "<=": lambda x: x <= value,
        ">": lambda x: x > value, ">=": lambda x: x >= value,
        "==": lambda x: x == value,
    }
    if cmp not in ops:
        raise ValueError(f"threshold comparison does not support cmp={cmp!r}")
    fn = ops[cmp]
    return [(d, bool(fn(v))) for d, v in series]


def _eval_bool_node(node: Dict[str, Any], features: Dict[str, Series]) -> List[Tuple[Any, bool]]:
    """Evaluate a boolean AST (AND/OR/NOT over comparison leaves) to a date→bool series."""
    if "leaf" in node:
        if node["leaf"] != "comparison":
            raise NotImplementedError(f"leaf type {node['leaf']!r} not supported in US1")
        series = features[node["feature"]]
        return _comparison_bool(series, node["cmp"], node["value"])
    op = node["op"]
    child_series = [_eval_bool_node(c, features) for c in node["children"]]
    dates = [d for d, _ in child_series[0]]
    combined = []
    for i, d in enumerate(dates):
        vals = [cs[i][1] for cs in child_series]
        if op == "AND":
            combined.append((d, all(vals)))
        elif op == "OR":
            combined.append((d, any(vals)))
        elif op == "NOT":
            combined.append((d, not vals[0]))
    return combined


# --- persistence / episodes ----------------------------------------------

def apply_min_dwell(labels: LabelSeries, min_dwell: int) -> LabelSeries:
    """Debounce: a new label is confirmed only after it persists `min_dwell` periods."""
    if min_dwell <= 1 or not labels:
        return list(labels)
    out: LabelSeries = []
    confirmed: Optional[str] = None
    candidate: Optional[str] = None
    run = 0
    for d, lab in labels:
        if confirmed is None:
            confirmed = lab
            out.append((d, lab))
            continue
        if lab == confirmed:
            candidate, run = None, 0
            out.append((d, confirmed))
        else:
            if lab == candidate:
                run += 1
            else:
                candidate, run = lab, 1
            if run >= min_dwell:
                confirmed = lab
                candidate, run = None, 0
            out.append((d, confirmed))
    return out


def episodes(labels: LabelSeries):
    """Contiguous runs of a non-UNDEFINED label: list of (label, start, end, length)."""
    eps = []
    cur_lab = None
    start = None
    length = 0
    prev_d = None
    for d, lab in labels:
        if lab == UNDEFINED:
            if cur_lab is not None:
                eps.append((cur_lab, start, prev_d, length))
                cur_lab, start, length = None, None, 0
            continue
        if lab != cur_lab:
            if cur_lab is not None:
                eps.append((cur_lab, start, prev_d, length))
            cur_lab, start, length = lab, d, 1
        else:
            length += 1
        prev_d = d
    if cur_lab is not None:
        eps.append((cur_lab, start, prev_d, length))
    return eps


def mean_dwell(labels: LabelSeries) -> float:
    eps = episodes(labels)
    return float(np.mean([e[3] for e in eps])) if eps else 0.0


def effective_n(labels: LabelSeries, bucket: str) -> int:
    """Number of independent episodes labeled `bucket` (not raw day-count)."""
    return sum(1 for e in episodes(labels) if e[0] == bucket)


def is_flicker(labels: LabelSeries, floor: float = 2.0) -> bool:
    md = mean_dwell(labels)
    return md > 0 and md < floor


# --- top-level ------------------------------------------------------------

def compute_labels(
    defn: RegimeDefinition,
    features: Dict[str, Series],
    window: int = 60,
    dataset_version: str = "dev",
) -> List[Tuple[Any, int, str]]:
    """Compute (date, entity_id, label) rows for a definition (US1: market scope)."""
    with create_span("regimes.labels.compute", regime=defn.name) as span:
        if has_detector_leaf(defn.expression):
            raise NotImplementedError("detector_function leaves require the 006 gated path")
        if defn.scope != "market":
            raise NotImplementedError("per-entity scopes are a later increment; US1 is market scope")

        expr = defn.expression
        bucket_labels = defn.bucketing.get("labels", [])

        if "leaf" in expr and expr.get("cmp") == "quantile":
            series = features[expr["feature"]]
            lab_series = rolling_tercile_labels(series, bucket_labels, window)
        else:
            bool_series = _eval_bool_node(expr, features)
            lab_series = [(d, "true" if b else "false") for d, b in bool_series]

        persistence = defn.persistence or {}
        min_dwell = persistence.get("min_dwell")
        if min_dwell:
            lab_series = apply_min_dwell(lab_series, int(min_dwell))

        set_attributes(span, n=len(lab_series), mean_dwell=mean_dwell(lab_series),
                       flicker=is_flicker(lab_series))
        return [(d, 0, lab) for d, lab in lab_series]


def load_market_feature_series(conn, defn: RegimeDefinition) -> Dict[str, Series]:
    """Load a market-level daily series (mean across entities) for each feature the
    definition references, from computed_features. Raises LookupError if a referenced
    feature is unknown or has no data (honest error — no silent empty regime)."""
    with create_span("regimes.labels.load_market_features", regime=defn.name):
        features: Dict[str, Series] = {}
        refs = {leaf["feature"] for leaf in iter_leaves(defn.expression)
                if leaf.get("leaf") == "comparison"}
        with conn.cursor() as cur:
            for ref in refs:
                cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (ref,))
                found = cur.fetchone()
                if not found:
                    raise LookupError(f"feature {ref!r} is not defined")
                cur.execute(
                    # median, not mean: the cross-sectional mean is dominated by
                    # outliers (penny-stock vol, bad split returns) — found when
                    # the first production vol regime ranked Oct 2019 above the
                    # COVID crash
                    """SELECT date, percentile_cont(0.5) WITHIN GROUP (ORDER BY value)
                       FROM computed_features
                       WHERE feature_id = %s GROUP BY date ORDER BY date""",
                    (found[0],),
                )
                rows = [(d, float(v)) for d, v in cur.fetchall() if v is not None]
                if not rows:
                    raise LookupError(f"feature {ref!r} has no computed data")
                features[ref] = rows
        return features


def compute_and_store(
    conn,
    defn: RegimeDefinition,
    features: Dict[str, Series],
    window: int = 60,
    dataset_version: str = "dev",
) -> int:
    """Compute labels and upsert them into regime_labels; return row count."""
    rows = compute_labels(defn, features, window=window, dataset_version=dataset_version)
    with create_span("regimes.labels.store", regime=defn.name) as span:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM regime_definitions WHERE name = %s", (defn.name,))
            found = cur.fetchone()
            if not found:
                raise ValueError(f"definition {defn.name!r} not stored")
            regime_id = found[0]
            for d, entity_id, label in rows:
                cur.execute(
                    """
                    INSERT INTO regime_labels (regime_id, date, entity_id, label, dataset_version)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (regime_id, entity_id, date)
                    DO UPDATE SET label = EXCLUDED.label, dataset_version = EXCLUDED.dataset_version
                    """,
                    (regime_id, d, entity_id, label, dataset_version),
                )
        set_attributes(span, rows=len(rows))
    return len(rows)
