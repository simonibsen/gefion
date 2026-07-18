"""Validation-catalog loader (008, T003 — Foundational).

The catalog is configuration, not code (SC-306): covering a new metric is a
YAML edit. The loader is strict — unknown keys, non-numeric bounds, or a
bounded metric without its definitional `why` refuse the whole catalog at
load. Coverage is enumerable: covered metrics AND uncovered numeric columns
on validated tables, so there is never a coverage illusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from gefion.observability import create_span, set_attributes

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "data-quality" / "catalog.yaml"
)

_DEFAULT_KEYS = {"tolerance_factor", "spike_factor", "robust_z_threshold"}
_METRIC_KEYS = {"entity_table", "table", "column", "bounds", "derivation",
                "series", "series_range", "why"}
_BOUNDS_KEYS = {"min", "max"}
_SERIES_RANGE_KEYS = {"max_ratio"}
_DERIVATION_KEYS = {"expression", "inputs", "tolerance_factor"}
_UNIVERSE_KEYS = {"test_tickers", "selectors"}


class CatalogError(ValueError):
    """Raised when the catalog is structurally invalid — refused whole."""


@dataclass
class Metric:
    name: str
    entity_table: str
    table: str
    column: str
    why: str
    bounds: Optional[Tuple[float, float]] = None
    derivation: Optional[Dict[str, Any]] = None
    series: Optional[str] = None
    # max/min-positive dynamic-range ceiling over the whole per-entity series
    # (issue #136); scanned as a SQL aggregate, suspect-only
    series_range: Optional[float] = None


@dataclass
class Catalog:
    defaults: Dict[str, float]
    metrics: Dict[str, Metric]
    universe: Dict[str, Any] = field(default_factory=dict)


def _require_keys(mapping: Dict[str, Any], allowed: set, where: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise CatalogError(f"unknown key(s) {sorted(unknown)} in {where}")


def _parse_metric(name: str, raw: Dict[str, Any]) -> Metric:
    if not isinstance(raw, dict):
        raise CatalogError(f"metric {name!r} must be a mapping")
    _require_keys(raw, _METRIC_KEYS, f"metric {name!r}")
    for key in ("entity_table", "table", "column"):
        if not raw.get(key):
            raise CatalogError(f"metric {name!r} is missing {key!r}")
    bounds = None
    if "bounds" in raw:
        b = raw["bounds"]
        if not isinstance(b, dict):
            raise CatalogError(f"metric {name!r}: bounds must be a mapping")
        _require_keys(b, _BOUNDS_KEYS, f"metric {name!r} bounds")
        try:
            bounds = (float(b["min"]), float(b["max"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogError(
                f"metric {name!r}: bounds min/max must be numeric") from exc
        if not raw.get("why"):
            raise CatalogError(
                f"metric {name!r} declares bounds without a 'why' — every "
                "envelope must carry its definitional argument")
    series_range = None
    if "series_range" in raw:
        sr = raw["series_range"]
        if not isinstance(sr, dict):
            raise CatalogError(f"metric {name!r}: series_range must be a mapping")
        _require_keys(sr, _SERIES_RANGE_KEYS, f"metric {name!r} series_range")
        try:
            series_range = float(sr["max_ratio"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogError(
                f"metric {name!r}: series_range max_ratio must be numeric") from exc
        if not raw.get("why"):
            raise CatalogError(
                f"metric {name!r} declares series_range without a 'why' — "
                "every envelope must carry its definitional argument")
        if bounds is not None:
            raise CatalogError(
                f"metric {name!r} declares both bounds and series_range — "
                "series_range metrics scan SQL aggregates, never rows; "
                "pick one detector per stanza")
    derivation = None
    if "derivation" in raw:
        d = raw["derivation"]
        if not isinstance(d, dict):
            raise CatalogError(f"metric {name!r}: derivation must be a mapping")
        _require_keys(d, _DERIVATION_KEYS, f"metric {name!r} derivation")
        if not d.get("expression") or not d.get("inputs"):
            raise CatalogError(
                f"metric {name!r}: derivation needs expression and inputs")
        derivation = dict(d)
    return Metric(name=name, entity_table=raw["entity_table"],
                  table=raw["table"], column=raw["column"],
                  why=raw.get("why", ""), bounds=bounds,
                  derivation=derivation, series=raw.get("series"),
                  series_range=series_range)


def load(path: Path) -> Catalog:
    """Load and strictly validate a catalog file; refuse whole on any error."""
    with create_span("quality.catalog.load", path=str(path)) as span:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        _require_keys(raw, {"defaults", "metrics", "universe"}, "catalog root")
        defaults_raw = raw.get("defaults") or {}
        _require_keys(defaults_raw, _DEFAULT_KEYS, "defaults")
        defaults = {k: float(v) for k, v in defaults_raw.items()}
        metrics_raw = raw.get("metrics") or {}
        metrics = {name: _parse_metric(name, m) for name, m in metrics_raw.items()}
        universe = raw.get("universe") or {}
        _require_keys(universe, _UNIVERSE_KEYS, "universe")
        universe.setdefault("test_tickers", [])
        universe.setdefault("selectors", {})
        set_attributes(span, n_metrics=len(metrics))
        return Catalog(defaults=defaults, metrics=metrics, universe=universe)


def load_default() -> Catalog:
    """The repo's shipped catalog (data-quality/catalog.yaml)."""
    return load(DEFAULT_CATALOG_PATH)


def verify_against_db(conn, cat: Catalog) -> None:
    """Refuse metrics naming nonexistent table/column pairs (run at command
    startup for DB-touching operations, not at pure load)."""
    with conn.cursor() as cur:
        for m in cat.metrics.values():
            cur.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""",
                (m.table, m.column),
            )
            if cur.fetchone() is None:
                raise CatalogError(
                    f"metric {m.name!r} names {m.table}.{m.column}, "
                    "which does not exist")


_NUMERIC_TYPES = ("numeric", "double precision", "real", "bigint")
_NON_VALUE_COLUMNS = {"id", "data_id", "series_id", "feature_id", "entity_id"}


def coverage(conn, cat: Catalog) -> Dict[str, Any]:
    """Covered metrics AND uncovered numeric value columns on validated
    tables — the gap is enumerable, never silent."""
    with create_span("quality.catalog.coverage") as span:
        covered = sorted(cat.metrics)
        covered_cols = {(m.table, m.column) for m in cat.metrics.values()}
        tables = sorted({m.table for m in cat.metrics.values()})
        uncovered: List[Tuple[str, str]] = []
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name = %s AND data_type = ANY(%s)
                       ORDER BY column_name""",
                    (table, list(_NUMERIC_TYPES)),
                )
                for (col,) in cur.fetchall():
                    if col in _NON_VALUE_COLUMNS or col.endswith("_id"):
                        continue
                    if (table, col) not in covered_cols:
                        uncovered.append((table, col))
        set_attributes(span, n_covered=len(covered), n_uncovered=len(uncovered))
        return {"covered": covered, "uncovered": uncovered}
