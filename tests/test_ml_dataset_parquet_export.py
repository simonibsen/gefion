"""
TDD tests for Parquet export format in dataset build.

These tests will initially fail and drive the implementation of Parquet export support.
"""
from pathlib import Path

import pytest

from g2.ml.dataset import export_dataset_artifacts


class _FakeCursor:
    """Mock cursor for testing without database."""

    def __init__(self):
        self._sql = None
        self._params = None
        # Mock data
        self._price_data = [
            ("AAPL", "2024-01-01", 100.0, 101.0, 99.0, 100.5, 100.5, 1000000),
            ("AAPL", "2024-01-02", 100.5, 102.0, 100.0, 101.5, 101.5, 1100000),
        ]
        self._feature_data = [
            ("AAPL", "2024-01-01", "indicator_rsi_14", 50.0),
            ("AAPL", "2024-01-02", "indicator_rsi_14", 55.0),
        ]

    def execute(self, sql, params=None):
        """Capture SQL and params, return mock data."""
        self._sql = sql
        self._params = params

        if "stock_ohlcv" in sql:
            self._rows = self._price_data
        elif "computed_features" in sql:
            self._rows = self._feature_data
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    """Mock database connection."""

    def cursor(self):
        return _FakeCursor()


def test_export_parquet_format_creates_parquet_files(tmp_path):
    """Test that format='parquet' creates .parquet files instead of .csv files."""
    pytest.importorskip("pyarrow")  # Skip if pyarrow not available
    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],  # Skip labels for simplicity
        "format": "parquet",  # NEW: specify parquet format
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    # Should create parquet files, not CSV
    assert (tmp_path / "prices.parquet").exists()
    assert (tmp_path / "features.parquet").exists()
    # Note: labels.parquet won't exist when horizons_days is empty (expected behavior)

    # CSV files should NOT exist
    assert not (tmp_path / "prices.csv").exists()
    assert not (tmp_path / "features.csv").exists()


def test_export_parquet_contains_correct_data(tmp_path):
    """Test that parquet files contain the correct data and preserve types."""
    pytest.importorskip("pyarrow")  # Skip if pyarrow not available
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas not installed")

    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],
        "format": "parquet",
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    # Read parquet files
    prices_df = pd.read_parquet(tmp_path / "prices.parquet")
    features_df = pd.read_parquet(tmp_path / "features.parquet")

    # Verify prices data
    assert len(prices_df) == 2
    assert list(prices_df.columns) == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    ]
    assert prices_df["symbol"].iloc[0] == "AAPL"
    assert prices_df["open"].iloc[0] == 100.0
    assert prices_df["volume"].dtype == "int64"  # Type preservation

    # Verify features data
    assert len(features_df) == 2
    assert list(features_df.columns) == ["symbol", "date", "feature_name", "value"]
    assert features_df["feature_name"].iloc[0] == "indicator_rsi_14"
    assert features_df["value"].iloc[0] == 50.0


def test_export_csv_still_works_by_default(tmp_path):
    """Test that CSV export still works when format is not specified (backward compatibility)."""
    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],
        # No format specified - should default to CSV
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    # Should create CSV files (default behavior)
    assert (tmp_path / "prices.csv").exists()
    assert (tmp_path / "features.csv").exists()
    # Note: labels.csv won't exist when horizons_days is empty (expected behavior)


def test_export_csv_explicit(tmp_path):
    """Test that format='csv' explicitly creates CSV files."""
    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],
        "format": "csv",
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    # Should create CSV files
    assert (tmp_path / "prices.csv").exists()
    assert (tmp_path / "features.csv").exists()
    # Note: labels.csv won't exist when horizons_days is empty (expected behavior)


def test_parquet_requires_pyarrow():
    """Test that helpful error is raised if pyarrow is not installed."""
    # This test will verify error handling when pyarrow is missing
    # Implementation should check for pyarrow and provide clear error message
    pass  # Will implement after basic parquet support is added
