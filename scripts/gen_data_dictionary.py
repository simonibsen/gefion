#!/usr/bin/env python3
"""Generate docs/DATA_DICTIONARY.md from the SQL schema files + catalog.py.

Sources of truth:
  - sql/schema.sql and sql/migrations/*.sql (applied in order), parsed
    statically — no live database is consulted
  - src/gefion/alphavantage/catalog.ENDPOINT_DOCS (AlphaVantage source mappings)

The generated doc cannot drift from these — re-run this script after schema
or catalog changes and commit the diff.

The parser covers the DDL dialect this repo actually uses:
  CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN [IF NOT EXISTS] /
  DROP COLUMN / DROP CONSTRAINT / ADD [CONSTRAINT ...] PRIMARY KEY,
  DROP TABLE IF EXISTS, and SELECT create_hypertable(...).
Anything else (indexes, views, extensions, DML) is ignored. If you add DDL
the parser doesn't understand, extend it here — the drift test will catch
silent omissions only if the doc changes, so prefer failing loudly.

Usage:
  gen_data_dictionary.py                    # emit to stdout
  gen_data_dictionary.py --write            # write to docs/DATA_DICTIONARY.md
  gen_data_dictionary.py --check            # exit 1 if doc would change
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "DATA_DICTIONARY.md"
SCHEMA_FILE = REPO_ROOT / "sql" / "schema.sql"
MIGRATIONS_DIR = REPO_ROOT / "sql" / "migrations"

# Make `gefion.*` importable
sys.path.insert(0, str(REPO_ROOT / "src"))

from gefion.alphavantage.catalog import ENDPOINT_DOCS  # noqa: E402


# ---------------------------------------------------------------------------
# SQL DDL parsing
# ---------------------------------------------------------------------------

@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool


@dataclass
class Table:
    name: str
    columns: "Dict[str, Column]" = field(default_factory=dict)
    primary_key: List[str] = field(default_factory=list)
    is_hypertable: bool = False


_TYPE_RE = re.compile(
    r"^(BIGSERIAL|SERIAL|BIGINT|INTEGER|INT|SMALLINT|"
    r"DOUBLE\s+PRECISION|REAL|"
    r"NUMERIC(?:\s*\(\s*\d+\s*,\s*\d+\s*\))?|"
    r"CHARACTER\s+VARYING(?:\s*\(\s*\d+\s*\))?|VARCHAR(?:\s*\(\s*\d+\s*\))?|"
    r"TIMESTAMPTZ|TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|"
    r"DATE|TIME|BOOLEAN|JSONB|JSON|TEXT|UUID|BYTEA)"
    r"(\s*\[\s*\])?",
    re.IGNORECASE,
)

_TABLE_CONSTRAINT_RE = re.compile(
    r"^(PRIMARY\s+KEY|UNIQUE|CONSTRAINT|FOREIGN\s+KEY|CHECK|EXCLUDE|LIKE)\b",
    re.IGNORECASE,
)

_PK_COLS_RE = re.compile(r"PRIMARY\s+KEY\s*\(([^)]*)\)", re.IGNORECASE)


def _strip_sql_noise(sql: str) -> str:
    """Remove `--` comments and psql meta-commands (\\echo etc.)."""
    lines = []
    for line in sql.splitlines():
        if line.lstrip().startswith("\\"):
            continue
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    return "\n".join(lines)


def _statements(sql: str) -> List[str]:
    return [s.strip() for s in _strip_sql_noise(sql).split(";") if s.strip()]


def _split_top_level_commas(s: str) -> List[str]:
    parts, depth, current = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_type(raw: str, array_suffix: str) -> str:
    norm = re.sub(r"\s+", " ", raw.upper()).replace("( ", "(").replace(" )", ")")
    return norm + ("[]" if array_suffix else "")


def _parse_column_def(item: str) -> "Column | None":
    m = re.match(r'^"?([A-Za-z_][A-Za-z0-9_]*)"?\s+(.*)$', item, re.DOTALL)
    if not m:
        return None
    name, rest = m.group(1), m.group(2).strip()
    tm = _TYPE_RE.match(rest)
    if not tm:
        return None
    data_type = _normalize_type(tm.group(1), tm.group(2) or "")
    remainder = rest[tm.end():]
    is_serial = data_type in ("SERIAL", "BIGSERIAL")
    inline_pk = re.search(r"\bPRIMARY\s+KEY\b", remainder, re.IGNORECASE)
    not_null = re.search(r"\bNOT\s+NULL\b", remainder, re.IGNORECASE)
    return Column(
        name=name,
        data_type=data_type,
        nullable=not (inline_pk or not_null or is_serial),
    )


def _apply_create_table(stmt: str, tables: Dict[str, Table]) -> None:
    m = re.match(
        r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?(\w+)"?\s*\((.*)\)\s*$',
        stmt,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return
    name, body = m.group(1), m.group(2)
    if name in tables:  # CREATE TABLE IF NOT EXISTS on existing table: no-op
        return
    table = Table(name=name)
    for item in _split_top_level_commas(body):
        if _TABLE_CONSTRAINT_RE.match(item):
            pk = _PK_COLS_RE.search(item)
            if pk and not item.upper().startswith("FOREIGN"):
                table.primary_key = [c.strip().strip('"') for c in pk.group(1).split(",")]
            continue
        col = _parse_column_def(item)
        if col is None:
            continue
        if col.name not in table.columns:
            table.columns[col.name] = col
        # Inline single-column PRIMARY KEY
        rest = item[len(col.name):]
        if re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE) and not _PK_COLS_RE.search(item):
            table.primary_key = [col.name]
    tables[name] = table


def _apply_alter_table(stmt: str, tables: Dict[str, Table]) -> None:
    m = re.match(
        r'^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?"?(\w+)"?\s+(.*)$',
        stmt,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return
    table = tables.get(m.group(1))
    if table is None:  # e.g. ALTERs to legacy tables dropped by later migrations
        return
    for clause in _split_top_level_commas(m.group(2)):
        add_col = re.match(
            r"^ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(.*)$",
            clause,
            re.IGNORECASE | re.DOTALL,
        )
        if add_col:
            col = _parse_column_def(add_col.group(1).strip())
            if col is not None and col.name not in table.columns:
                table.columns[col.name] = col
            continue
        drop_col = re.match(
            r'^DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?"?(\w+)"?', clause, re.IGNORECASE
        )
        if drop_col:
            table.columns.pop(drop_col.group(1), None)
            continue
        drop_constraint = re.match(
            r'^DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?"?(\w+)"?', clause, re.IGNORECASE
        )
        if drop_constraint:
            if drop_constraint.group(1).endswith("_pkey"):
                table.primary_key = []
            continue
        add_pk = re.match(
            r"^ADD\s+(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\(([^)]*)\)",
            clause,
            re.IGNORECASE,
        )
        if add_pk:
            table.primary_key = [c.strip().strip('"') for c in add_pk.group(1).split(",")]


def _apply_statement(stmt: str, tables: Dict[str, Table]) -> None:
    upper = stmt.upper()
    if upper.startswith("CREATE TABLE"):
        _apply_create_table(stmt, tables)
    elif upper.startswith("ALTER TABLE"):
        _apply_alter_table(stmt, tables)
    elif upper.startswith("DROP TABLE"):
        m = re.match(r"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(.*)$", stmt, re.IGNORECASE | re.DOTALL)
        if m:
            for name in m.group(1).replace("CASCADE", "").replace("cascade", "").split(","):
                tables.pop(name.strip().strip('"'), None)
    else:
        hyper = re.search(r"create_hypertable\(\s*'(\w+)'", stmt, re.IGNORECASE)
        if hyper and hyper.group(1) in tables:
            tables[hyper.group(1)].is_hypertable = True


def build_schema() -> Dict[str, Table]:
    """Parse sql/schema.sql then sql/migrations/*.sql in filename order."""
    tables: Dict[str, Table] = {}
    files = [SCHEMA_FILE] + sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in files:
        for stmt in _statements(path.read_text()):
            _apply_statement(stmt, tables)
    return tables


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _build_source_map() -> Dict[Tuple[str, str], List[Tuple[str, str, str]]]:
    """Map (table, column) → list of (endpoint, av_field, description)."""
    out: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = defaultdict(list)
    for endpoint, info in ENDPOINT_DOCS.items():
        field_map = info.get("field_map", {})
        for av_field, (table, col, desc) in field_map.items():
            out[(table, col)].append((endpoint, av_field, desc))
    return out


# One-sentence purpose per table. Hand-curated because the schema files don't
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
    "schema_migrations": "Migration bookkeeping: which sql/migrations/*.sql files have been applied (managed by `src/gefion/db/migrate.py`).",
}


def render(tables: Dict[str, Table]) -> str:
    table_names = sorted(tables)
    source_map = _build_source_map()

    lines: List[str] = []
    lines.append("# Gefion Data Dictionary")
    lines.append("")
    lines.append(
        "*Generated by `scripts/gen_data_dictionary.py` from `sql/schema.sql`, "
        "`sql/migrations/*.sql`, and `src/gefion/alphavantage/catalog.py`. "
        "Do not edit by hand — re-run the script and commit the diff.*"
    )
    lines.append("")
    lines.append("## Contents")
    lines.append("")
    lines.append("- [Tables](#tables)")
    for t in table_names:
        lines.append(f"  - [`{t}`](#{t.replace('_', '-')})")
    lines.append("- [AlphaVantage endpoints → tables](#alphavantage-endpoints--tables)")
    lines.append("")

    lines.append("## Tables")
    lines.append("")

    for name in table_names:
        table = tables[name]
        pk = set(table.primary_key)
        purpose = TABLE_PURPOSE.get(name, "*(no description yet)*")

        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(purpose)
        lines.append("")
        attrs = []
        if table.is_hypertable:
            attrs.append("**TimescaleDB hypertable**")
        if pk:
            attrs.append(f"Primary key: `{', '.join(sorted(pk))}`")
        if attrs:
            lines.append(" · ".join(attrs))
            lines.append("")

        lines.append("| Column | Type | Null | Source | Notes |")
        lines.append("|---|---|---|---|---|")
        for col in table.columns.values():
            sources = source_map.get((name, col.name), [])
            if sources:
                source_str = "<br>".join(
                    f"`{ep}`.<br>`{av_field}`" for ep, av_field, _desc in sources
                )
                notes_str = "<br>".join(desc for _ep, _af, desc in sources if desc)
            else:
                source_str = ""
                notes_str = ""
            nullable = col.nullable and col.name not in pk
            null_marker = "✓" if nullable else ""
            if col.name in pk:
                col_display = f"**`{col.name}`** 🔑"
            else:
                col_display = f"`{col.name}`"
            lines.append(
                f"| {col_display} | {col.data_type} | {null_marker} | {source_str} | {notes_str} |"
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

    content = render(build_schema())

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
