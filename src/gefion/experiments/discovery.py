"""Data discovery for experiment hypothesis generation.

Inventories available data sources, features, and functions, then
cross-references against the principles catalog to identify gaps and
generate actionable experiment hypotheses.

Uses data/registry.yaml for semantic metadata about what each table/column
means and how it can be used. Live DB metadata provides actual coverage
and freshness.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


def _get_registry_path() -> Path:
    """Get path to the data source registry."""
    env_path = os.environ.get("GEFION_REGISTRY_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "registry.yaml"


def load_registry() -> List[Dict[str, Any]]:
    """Load the data source registry from YAML."""
    path = _get_registry_path()
    if not path.exists():
        logger.warning("Data registry not found: %s", path)
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, list) else []


def discover_data_sources(conn) -> List[Dict[str, Any]]:
    """Query database metadata for available data sources.

    Merges the static registry (semantic metadata) with live DB stats
    (row counts, date ranges, freshness).

    Returns list of {table, columns, row_count, date_range, coverage_pct,
    freshness_days, description, column_details}.
    """
    with create_span("experiments.discovery.data_sources"):
        sources = []
        registry = load_registry()
        registry_map = {r["table"]: r for r in registry if "table" in r}

        # Use registry tables + any additional known tables
        target_tables = list(registry_map.keys()) or [
            "stock_ohlcv", "stocks_fundamentals", "computed_features",
            "cross_sectional_features", "predictions",
        ]

        with conn.cursor() as cur:
            for table in target_tables:
                # Check table exists
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                    (table,),
                )
                if not cur.fetchone():
                    continue

                # Get columns
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table,),
                )
                columns = [row[0] for row in cur.fetchall()]

                # Get approximate row count from chunk stats (hypertable-aware)
                row_count = 0
                try:
                    cur.execute("""
                        SELECT COALESCE(SUM(s.n_live_tup), 0)
                        FROM timescaledb_information.chunks c
                        JOIN pg_stat_user_tables s ON s.relname = c.chunk_name
                        WHERE c.hypertable_name = %s
                    """, (table,))
                    row = cur.fetchone()
                    val = row[0] if row else 0
                    row_count = int(val) if val else 0
                except Exception:
                    # Fallback for non-hypertables
                    try:
                        cur.execute(
                            "SELECT COALESCE(n_live_tup, 0) FROM pg_stat_user_tables WHERE relname = %s",
                            (table,),
                        )
                        row = cur.fetchone()
                        val = row[0] if row else 0
                        row_count = int(val) if val else 0
                    except Exception:
                        pass

                # Get date range if table has a date column
                date_range = (None, None)
                freshness_days = None
                date_col = "date" if "date" in columns else "prediction_date" if "prediction_date" in columns else None
                if date_col and row_count > 0:
                    try:
                        cur.execute(f"""
                            SELECT
                                (SELECT {date_col} FROM {table} ORDER BY {date_col} ASC LIMIT 1),
                                (SELECT {date_col} FROM {table} ORDER BY {date_col} DESC LIMIT 1)
                        """)
                        min_date, max_date = cur.fetchone()
                        if min_date and max_date:
                            date_range = (str(min_date), str(max_date))
                            from datetime import date
                            freshness_days = (date.today() - max_date).days if hasattr(max_date, 'day') else None
                    except Exception:
                        pass

                # Coverage: approximate as percentage of stocks with data
                coverage_pct = 100.0 if row_count > 0 else 0.0

                # Merge with registry metadata
                reg = registry_map.get(table, {})
                source = {
                    "table": table,
                    "columns": columns,
                    "row_count": int(row_count),
                    "date_range": date_range,
                    "coverage_pct": coverage_pct,
                    "freshness_days": freshness_days if freshness_days is not None else 0,
                    "description": reg.get("description", ""),
                    "time_series": reg.get("time_series", True),
                    "update_frequency": reg.get("update_frequency", "unknown"),
                }
                # Include column-level semantic metadata from registry
                if "columns" in reg:
                    source["column_details"] = reg["columns"]

                sources.append(source)

        return sources


def discover_features(conn) -> List[Dict[str, Any]]:
    """Query feature definitions and functions from the database.

    Returns list of {name, function_name, active, params, coverage_pct}.
    """
    with create_span("experiments.discovery.features"):
        features = []
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT name, function_name, active, params
                    FROM feature_definitions
                    ORDER BY name
                """)
                for name, function_name, active, params in cur.fetchall():
                    features.append({
                        "name": name,
                        "function_name": function_name,
                        "active": active,
                        "params": params if isinstance(params, dict) else {},
                        "coverage_pct": 0.0,  # Would need per-feature count for accuracy
                    })
            except Exception as e:
                logger.warning("Could not query feature_definitions: %s", e)

        return features


def discover_gaps(
    data_sources: List[Dict],
    features: List[Dict],
    principles: List[Dict],
) -> List[Dict[str, Any]]:
    """Cross-reference available data against principles to find gaps.

    Pure function — no database access.

    A gap exists when a principle requires data that:
    - Doesn't exist at all (missing table/column) → marked in 'missing'
    - Exists but has no derived feature → marked as available with hypothesis

    Returns list of {principle_id, required_data, available, missing, hypothesis}.
    """
    # Build lookup: "table.column" → True for available data
    available_data = set()
    for source in data_sources:
        table = source["table"]
        for col in source["columns"]:
            available_data.add(f"{table}.{col}")
        # Also add just the table name for table-level requirements
        available_data.add(table)

    gaps = []
    for principle in principles:
        requirements = principle.get("data_requirements", [])
        if not requirements:
            continue

        available = []
        missing = []
        for req in requirements:
            # Check if requirement is satisfied (table.column or just table)
            if req in available_data:
                available.append(req)
            else:
                # Check if at least the table exists
                table_part = req.split(".")[0] if "." in req else req
                if any(s["table"] == table_part for s in data_sources):
                    # Table exists but specific column might not
                    col_part = req.split(".")[1] if "." in req else None
                    if col_part and any(
                        col_part in s["columns"]
                        for s in data_sources
                        if s["table"] == table_part
                    ):
                        available.append(req)
                    else:
                        missing.append(req)
                else:
                    missing.append(req)

        # Only report as gap if something is missing
        if missing:
            gaps.append({
                "principle_id": principle["id"],
                "required_data": requirements,
                "available": available,
                "missing": missing,
                "hypothesis": principle.get("experiment_design") if not missing else None,
            })

    return gaps


def generate_hypotheses(
    gaps: List[Dict],
    principles: List[Dict],
) -> List[Dict[str, Any]]:
    """Generate actionable experiment hypotheses from gaps.

    Pure function — no database access.

    Returns list of {principle_id, description, experiment_type, feasibility}.
    """
    if not gaps:
        return []

    # Build principle lookup by id
    principle_map = {p["id"]: p for p in principles}

    hypotheses = []
    for gap in gaps:
        pid = gap["principle_id"]
        principle = principle_map.get(pid)
        if not principle:
            continue

        has_missing = bool(gap.get("missing"))
        feasibility = "blocked" if has_missing else "ready"

        exp_types = principle.get("experiment_types", ["feature_engineering"])
        description = principle.get("experiment_design", principle.get("claim", ""))

        hypotheses.append({
            "principle_id": pid,
            "description": description,
            "experiment_type": exp_types[0] if exp_types else "feature_engineering",
            "feasibility": feasibility,
        })

    return hypotheses


def run_discovery(
    conn,
    principles: List[Dict],
) -> Dict[str, Any]:
    """Orchestrate full data discovery: sources, features, gaps, hypotheses.

    Returns {data_sources, features, gaps, hypotheses}.
    """
    with create_span("experiments.discovery.run") as span:
        data_sources = discover_data_sources(conn)
        features = discover_features(conn)
        gaps = discover_gaps(data_sources, features, principles)
        hypotheses = generate_hypotheses(gaps, principles)

        set_attributes(span,
                       sources_count=len(data_sources),
                       features_count=len(features),
                       gaps_count=len(gaps),
                       hypotheses_count=len(hypotheses))

        return {
            "data_sources": data_sources,
            "features": features,
            "gaps": gaps,
            "hypotheses": hypotheses,
        }
