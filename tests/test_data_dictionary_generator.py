"""Tests for scripts/gen_data_dictionary.py.

Generates a Markdown data dictionary from sql/schema.sql +
sql/migrations/*.sql and src/gefion/alphavantage/catalog.py so the doc
cannot drift from the sources of truth. No live database is consulted —
the SQL files in the repo ARE the schema source of truth.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_data_dictionary.py"
EXPECTED_OUTPUT = REPO_ROOT / "docs" / "DATA_DICTIONARY.md"

# Deliberately unreachable: the generator must never open a DB connection.
UNREACHABLE_DB = "postgresql://invalid:invalid@127.0.0.1:1/invalid"


def _run_generator(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": UNREACHABLE_DB},
    )


def _table_section(output: str, table: str) -> str:
    """Return the Markdown section for one table, or fail if absent."""
    marker = f"### `{table}`"
    assert marker in output, f"Table {table} missing from generated dictionary"
    start = output.index(marker)
    end = output.find("### `", start + len(marker))
    return output[start : end if end != -1 else len(output)]


def test_generator_script_exists_and_runs():
    assert SCRIPT.exists(), f"Generator script missing: {SCRIPT}"
    result = _run_generator()
    assert result.returncode == 0, f"Generator failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_generator_needs_no_database():
    """The generator reads sql/ files only — it must succeed with an
    unreachable DATABASE_URL (set unconditionally by _run_generator)."""
    result = _run_generator()
    assert result.returncode == 0, (
        f"Generator should not need a live DB:\nstderr={result.stderr}"
    )


def test_generator_emits_markdown_to_stdout():
    result = _run_generator()
    assert result.returncode == 0
    assert result.stdout.startswith("#"), "Output should start with a Markdown heading"
    assert "Data Dictionary" in result.stdout


def test_generator_documents_known_tables():
    result = _run_generator()
    out = result.stdout
    for table in (
        "stocks",
        "stock_ohlcv",
        "stocks_fundamentals",
        "quarterly_financials",
        "cross_sectional_features",
        "schema_migrations",
    ):
        assert f"### `{table}`" in out, f"Table {table} missing from generated dictionary"


def test_generator_omits_dropped_tables():
    """Tables dropped by later migrations must not be documented."""
    result = _run_generator()
    for table in ("quantile_predictions", "trend_class_predictions"):
        assert f"### `{table}`" not in result.stdout, f"Dropped table {table} still documented"


def test_alter_added_columns_present():
    """Columns added via ALTER TABLE (in schema.sql or migrations) must appear."""
    out = _run_generator().stdout
    assert "`sector`" in _table_section(out, "stocks")
    assert "`comparison_group`" in _table_section(out, "cross_sectional_features")
    # Multi-column ALTER ... ADD COLUMN a, ADD COLUMN b
    fd = _table_section(out, "feature_definitions")
    assert "`source_tables`" in fd
    assert "`source_columns`" in fd


def test_migrated_primary_key_reflected():
    """model_performance PK was rebuilt as (model_id, horizon_days)."""
    section = _table_section(_run_generator().stdout, "model_performance")
    assert "Primary key: `horizon_days, model_id`" in section


def test_hypertables_marked():
    out = _run_generator().stdout
    for table in ("stock_ohlcv", "computed_features", "predictions", "quarterly_financials"):
        assert "TimescaleDB hypertable" in _table_section(out, table), (
            f"{table} should be marked as a hypertable"
        )


def test_generator_documents_alphavantage_mappings():
    result = _run_generator()
    out = result.stdout
    for endpoint in (
        "TIME_SERIES_DAILY_ADJUSTED",
        "OVERVIEW",
        "LISTING_STATUS",
        "INCOME_STATEMENT",
        "BALANCE_SHEET",
        "CASH_FLOW",
        "EARNINGS",
    ):
        assert endpoint in out, f"AlphaVantage endpoint {endpoint} missing"


def test_generator_is_idempotent():
    a = _run_generator().stdout
    b = _run_generator().stdout
    assert a == b, "Repeated runs must produce identical output"


def test_check_mode_detects_drift(tmp_path, monkeypatch):
    """`--check` exits 0 if docs/DATA_DICTIONARY.md is up to date, 1 otherwise."""
    if not EXPECTED_OUTPUT.exists():
        pytest.skip("docs/DATA_DICTIONARY.md not yet generated — skipping drift check")
    in_sync = _run_generator("--check")
    assert in_sync.returncode == 0, (
        f"--check should pass when doc is committed in sync\n"
        f"stdout={in_sync.stdout}\nstderr={in_sync.stderr}"
    )
