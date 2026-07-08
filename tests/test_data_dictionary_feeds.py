"""Feeds-graph + Mermaid ERD in the data dictionary (007, T021 — US3).

TDD: written FIRST. The registry is the feeds graph: the generator renders
"what feeds what" hermetically — schema.sql foreign keys (solid edges) plus
the feature registry exports in feature-definitions/*.json (dashed edges) —
with no live database. Raw tables with no declared consumers are flagged
(SC-204: stocks_fundamentals today); the ERD groups tables by declared layer
with solid (FK) vs dashed (registry) edges (SC-204a).
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_data_dictionary.py"

# Deliberately unreachable: the generator must never open a DB connection.
UNREACHABLE_DB = "postgresql://invalid:invalid@127.0.0.1:1/invalid"


def _generate() -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL": UNREACHABLE_DB},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_feeds_section_exists_and_is_hermetic():
    output = _generate()
    assert "## Feeds graph" in output


def test_consumers_listed_per_raw_table():
    """The registry's source_table edges become the consumer list: 18 of the
    21 exported features read stock_ohlcv, 3 read quarterly_financials."""
    output = _generate()
    feeds = output[output.index("## Feeds graph"):]
    assert "`stock_ohlcv`" in feeds
    assert "computed_features` (18 feature" in feeds
    assert "computed_features` (3 feature" in feeds  # quarterly_financials


def test_consumerless_raw_tables_flagged():
    """SC-204: a raw table nothing declares as a source is loudly flagged —
    stocks_fundamentals today."""
    output = _generate()
    feeds = output[output.index("## Feeds graph"):]
    line = next(l for l in feeds.splitlines() if "`stocks_fundamentals`" in l)
    assert "no declared consumers" in line


def test_mermaid_erd_solid_fk_dashed_registry_edges():
    """SC-204a: the ERD is generated, solid for hard FKs, dashed for
    registry (declared) edges, grouped by layer."""
    output = _generate()
    assert "```mermaid" in output
    mermaid = output[output.index("```mermaid"):]
    mermaid = mermaid[:mermaid.index("```", 10)]
    # solid FK edges from schema.sql
    assert "macro_series_values --> macro_series" in mermaid
    assert "stock_ohlcv --> stocks" in mermaid
    # the retired FK must NOT reappear as solid
    assert "computed_features --> stocks" not in mermaid
    # dashed registry edges: source_table feeds, entity_table identity
    assert "stock_ohlcv -.->" in mermaid
    assert "computed_features -.-> stocks" in mermaid
    # grouped by declared layer
    assert "subgraph" in mermaid


def test_erd_layers_are_declared_not_guessed():
    """Every layered table is in the generator's TABLE_LAYER map — the
    add-a-table checklist requires declaring the layer."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import gen_data_dictionary as gen
    finally:
        sys.path.pop(0)
    for table in ("stocks", "macro_series", "stock_ohlcv", "macro_series_values",
                  "stocks_fundamentals", "quarterly_financials",
                  "computed_features", "cross_sectional_features"):
        assert table in gen.TABLE_LAYER, f"{table} has no declared layer"
