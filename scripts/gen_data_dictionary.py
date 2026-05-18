#!/usr/bin/env python3
"""Generate docs/DATA_DICTIONARY.md from the live DB schema + catalog.py.

Sources of truth:
  - PostgreSQL information_schema (live tables/columns)
  - src/gefion/alphavantage/catalog.ENDPOINT_DOCS (AlphaVantage source mappings)

The generated doc cannot drift from these — re-run this script after schema
or catalog changes and commit the diff.

Usage:
  gen_data_dictionary.py                    # emit to stdout
  gen_data_dictionary.py --write            # write to docs/DATA_DICTIONARY.md
  gen_data_dictionary.py --check            # exit 1 if doc would change
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "DATA_DICTIONARY.md"

# Make `gefion.*` importable
sys.path.insert(0, str(REPO_ROOT / "src"))

import psycopg  # noqa: E402
from gefion.alphavantage.catalog import ENDPOINT_DOCS  # noqa: E402


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Fallback to local dev default
        url = "postgresql://gefion:gefionpass@localhost:6432/gefion"
    return url


def _fetch_tables(conn) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        return [r[0] for r in cur.fetchall()]


def _fetch_columns(conn, table: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """Returns (column_name, data_type, nullable, default) per column."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return list(cur.fetchall())


def _fetch_hypertables(conn) -> set:
    """Return set of TimescaleDB hypertable names, or empty set if extension absent."""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")
            return {r[0] for r in cur.fetchall()}
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            return set()
        except Exception:
            conn.rollback()
            return set()


def _fetch_primary_key(conn, table: str) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kc.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kc
              ON kc.table_name = tc.table_name AND kc.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'public'
              AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kc.ordinal_position
            """,
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def _build_source_map() -> Dict[Tuple[str, str], List[Tuple[str, str, str]]]:
    """Map (table, column) → list of (endpoint, av_field, description)."""
    out: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = defaultdict(list)
    for endpoint, info in ENDPOINT_DOCS.items():
        field_map = info.get("field_map", {})
        for av_field, (table, col, desc) in field_map.items():
            out[(table, col)].append((endpoint, av_field, desc))
    return out


# One-sentence purpose per table. Hand-curated because the DB doesn't
# carry semantic descriptions. Add an entry here when introducing a new
# table; the generator emits "(no description yet)" otherwise.
TABLE_PURPOSE: Dict[str, str] = {
    "stocks": "Universe membership. One row per symbol, with listing metadata.",
    "stock_ohlcv": "Daily OHLCV price data. TimescaleDB hypertable, the foundation every feature and model is built on.",
    "stocks_fundamentals": "Point-in-time company fundamentals (no history — overwritten on update).",
    "quarterly_financials": "Quarterly financial statements (income, balance sheet, cash flow, earnings). One row per (symbol, fiscal_date, statement_type). Non-core fields in `raw` JSONB.",
    "computed_features": "Computed feature values. TimescaleDB hypertable keyed by (data_id, feature_id, date). Tall format.",
    "cross_sectional_features": "Market-relative feature rankings (percentile, z-score) computed across the universe per date.",
    "feature_definitions": "Configuration: what feature to compute, with what params, from what source table/column.",
    "feature_functions": "Sandboxed Python function bodies that implement features. Versioned, enable/disable-able.",
    "predictions": "Model outputs. Quantile (q10/q50/q90) and trend-class predictions stored in JSONB.",
    "prediction_outcomes": "Realized returns paired with predictions, used for evaluation.",
    "model_performance": "Aggregated model evaluation metrics (coverage, pinball loss) per evaluation window.",
    "ml_models": "Trained model metadata. Points at file artifacts on disk.",
    "ml_runs": "Training run history with hyperparameters, dataset reference, metrics.",
    "ml_datasets": "Dataset manifests: which symbols + features + time range used for an ML run.",
    "strategy_registry": "Catalogue of available trading strategies.",
    "strategy_configs": "Saved strategy parameter sets for reuse across backtests.",
    "volatility_thresholds": "Per-symbol volatility thresholds used by trend-classifier label generation.",
    "experiments": "Autonomous experimentation framework: proposed/approved/run experiments.",
    "experiment_cycles": "Top-level autonomous experiment cycles (discover → propose → run → evaluate).",
    "experiment_trials": "Individual trials within an experiment (e.g. one hyperparameter combination).",
}


def _format_type(data_type: str) -> str:
    # Compact display: information_schema returns 'integer', 'numeric', etc.
    return data_type.upper()


def render(conn) -> str:
    tables = _fetch_tables(conn)
    hypertables = _fetch_hypertables(conn)
    source_map = _build_source_map()

    lines: List[str] = []
    lines.append("# Gefion Data Dictionary")
    lines.append("")
    lines.append(
        "*Generated by `scripts/gen_data_dictionary.py` from the live PostgreSQL "
        "schema and `src/gefion/alphavantage/catalog.py`. Do not edit by hand — "
        "re-run the script and commit the diff.*"
    )
    lines.append("")
    lines.append("## Contents")
    lines.append("")
    lines.append("- [Tables](#tables)")
    for t in tables:
        lines.append(f"  - [`{t}`](#{t.replace('_', '-')})")
    lines.append("- [AlphaVantage endpoints → tables](#alphavantage-endpoints--tables)")
    lines.append("")

    lines.append("## Tables")
    lines.append("")

    for table in tables:
        cols = _fetch_columns(conn, table)
        pk = set(_fetch_primary_key(conn, table))
        is_hyper = table in hypertables
        purpose = TABLE_PURPOSE.get(table, "*(no description yet)*")

        lines.append(f"### `{table}`")
        lines.append("")
        lines.append(purpose)
        lines.append("")
        attrs = []
        if is_hyper:
            attrs.append("**TimescaleDB hypertable**")
        if pk:
            attrs.append(f"Primary key: `{', '.join(sorted(pk))}`")
        if attrs:
            lines.append(" · ".join(attrs))
            lines.append("")

        lines.append("| Column | Type | Null | Source | Notes |")
        lines.append("|---|---|---|---|---|")
        for col_name, data_type, is_nullable, _default in cols:
            sources = source_map.get((table, col_name), [])
            if sources:
                source_str = "<br>".join(
                    f"`{ep}`.<br>`{av_field}`" for ep, av_field, _desc in sources
                )
                notes_str = "<br>".join(desc for _ep, _af, desc in sources if desc)
            else:
                source_str = ""
                notes_str = ""
            null_marker = "✓" if is_nullable == "YES" else ""
            type_str = _format_type(data_type)
            if col_name in pk:
                col_display = f"**`{col_name}`** 🔑"
            else:
                col_display = f"`{col_name}`"
            lines.append(
                f"| {col_display} | {type_str} | {null_marker} | {source_str} | {notes_str} |"
            )
        lines.append("")

    lines.append("## AlphaVantage endpoints → tables")
    lines.append("")
    lines.append(
        "Each entry below comes from `ENDPOINT_DOCS` in "
        "`src/gefion/alphavantage/catalog.py`. **Every new endpoint that lands "
        "data in our DB must add an entry there** so the data dictionary stays "
        "accurate."
    )
    lines.append("")
    lines.append("| Endpoint | Cadence | Tables | CLI |")
    lines.append("|---|---|---|---|")
    for endpoint, info in sorted(ENDPOINT_DOCS.items()):
        cadence = info.get("cadence", "")
        tables_str = ", ".join(f"`{t}`" for t in info.get("tables", [])) or "*(none yet)*"
        cli = info.get("cli", "")
        lines.append(f"| `{endpoint}` | {cadence} | {tables_str} | {cli} |")
    lines.append("")

    for endpoint, info in sorted(ENDPOINT_DOCS.items()):
        field_map = info.get("field_map", {})
        if not field_map:
            continue
        lines.append(f"### `{endpoint}`")
        lines.append("")
        notes = info.get("notes")
        if notes:
            lines.append(f"> {notes}")
            lines.append("")
        lines.append("| AlphaVantage field | Lands in | Description |")
        lines.append("|---|---|---|")
        for av_field, (table, col, desc) in field_map.items():
            lines.append(f"| `{av_field}` | `{table}`.`{col}` | {desc} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write to docs/DATA_DICTIONARY.md")
    parser.add_argument("--check", action="store_true", help="Exit 1 if doc would change")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output path (default docs/DATA_DICTIONARY.md)")
    args = parser.parse_args()

    with psycopg.connect(_database_url()) as conn:
        content = render(conn)

    if args.check:
        if not args.output.exists():
            print(f"missing: {args.output}", file=sys.stderr)
            return 1
        existing = args.output.read_text()
        if existing != content:
            print(f"drift detected in {args.output} — re-run without --check to update", file=sys.stderr)
            return 1
        return 0

    if args.write:
        args.output.write_text(content)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
