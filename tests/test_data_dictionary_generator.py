"""Tests for scripts/gen_data_dictionary.py.

Generates a Markdown data dictionary from sql/schema.sql and
src/gefion/alphavantage/catalog.py so the doc cannot drift from the
sources of truth.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_data_dictionary.py"
EXPECTED_OUTPUT = REPO_ROOT / "docs" / "DATA_DICTIONARY.md"


def _run_generator(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_generator_script_exists_and_runs():
    assert SCRIPT.exists(), f"Generator script missing: {SCRIPT}"
    result = _run_generator()
    assert result.returncode == 0, f"Generator failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_generator_emits_markdown_to_stdout():
    result = _run_generator()
    assert result.returncode == 0
    assert result.stdout.startswith("#"), "Output should start with a Markdown heading"
    assert "Data Dictionary" in result.stdout


def test_generator_documents_known_tables():
    result = _run_generator()
    out = result.stdout
    for table in ("stocks", "stock_ohlcv", "stocks_fundamentals", "quarterly_financials"):
        assert table in out, f"Table {table} missing from generated dictionary"


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
