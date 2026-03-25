"""
TDD tests for feature selection during dataset build.

These tests will initially fail and drive the implementation.
"""
import csv
from pathlib import Path

from gefion.ml.dataset import export_dataset_artifacts


class _FakeCursor:
    """Mock cursor for testing without database."""
    def __init__(self):
        self._sql = None
        self._params = None
        # Mock feature data: 3 features (rsi, macd, bb) for 2 symbols, 2 dates
        self._feature_data = [
            ("AAPL", "2024-01-01", "indicator_rsi_14", 50.0),
            ("AAPL", "2024-01-01", "indicator_macd", 0.5),
            ("AAPL", "2024-01-01", "indicator_bollinger_bands", 2.0),
            ("AAPL", "2024-01-02", "indicator_rsi_14", 55.0),
            ("AAPL", "2024-01-02", "indicator_macd", 0.6),
            ("AAPL", "2024-01-02", "indicator_bollinger_bands", 2.1),
            ("MSFT", "2024-01-01", "indicator_rsi_14", 60.0),
            ("MSFT", "2024-01-01", "indicator_macd", 0.7),
            ("MSFT", "2024-01-01", "indicator_bollinger_bands", 1.9),
        ]
        self._price_data = []  # Empty for these tests

    def execute(self, sql, params=None):
        """Capture SQL and params, return filtered data."""
        self._sql = sql
        self._params = params

        # Detect which query is being run
        if "stock_ohlcv" in sql:
            # Price query - return empty
            self._rows = self._price_data
        elif "computed_features" in sql:
            # Feature query - filter if WHERE clause includes feature names
            self._rows = self._feature_data

            # If there's a feature name filter, apply it
            if params and "fd.name" in sql and "ANY" in sql:
                # Extract feature names from params
                feature_names = params[1] if len(params) > 1 else params[0]
                if isinstance(feature_names, list):
                    # Include mode: fd.name = ANY(%s)
                    if "fd.name = ANY" in sql:
                        self._rows = [row for row in self._feature_data if row[2] in feature_names]
                    # Exclude mode: fd.name != ALL(%s)
                    elif "fd.name != ALL" in sql:
                        self._rows = [row for row in self._feature_data if row[2] not in feature_names]
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


def _read_csv_rows(path: Path) -> list[list[str]]:
    """Read all rows from CSV file."""
    with path.open(newline="") as f:
        reader = csv.reader(f)
        return list(reader)


def test_export_with_feature_names_whitelist(tmp_path):
    """Test that specifying feature_names only exports those features."""
    manifest = {
        "universe": {"symbols": ["AAPL", "MSFT"]},
        "horizons_days": [],  # Skip labels
        "feature_names": ["indicator_rsi_14", "indicator_macd"],  # Only these two
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    # Read features CSV
    features_csv = tmp_path / "features.csv"
    assert features_csv.exists()

    rows = _read_csv_rows(features_csv)
    header = rows[0]
    data_rows = rows[1:]

    # Verify header
    assert header == ["symbol", "date", "feature_name", "value"]

    # Verify only specified features are present
    feature_names = [row[2] for row in data_rows]
    assert "indicator_rsi_14" in feature_names
    assert "indicator_macd" in feature_names
    assert "indicator_bollinger_bands" not in feature_names  # Should be excluded

    # Verify we got data for both symbols
    symbols = set(row[0] for row in data_rows)
    assert symbols == {"AAPL", "MSFT"}


def test_export_with_exclude_features_blacklist(tmp_path):
    """Test that specifying exclude_features exports all except those."""
    manifest = {
        "universe": {"symbols": ["AAPL", "MSFT"]},
        "horizons_days": [],
        "exclude_features": ["indicator_bollinger_bands"],  # Exclude this one
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    features_csv = tmp_path / "features.csv"
    rows = _read_csv_rows(features_csv)
    data_rows = rows[1:]

    feature_names = [row[2] for row in data_rows]
    assert "indicator_rsi_14" in feature_names
    assert "indicator_macd" in feature_names
    assert "indicator_bollinger_bands" not in feature_names  # Should be excluded


def test_export_without_feature_selection_exports_all(tmp_path):
    """Test that without feature selection, all features are exported (default behavior)."""
    manifest = {
        "universe": {"symbols": ["AAPL", "MSFT"]},
        "horizons_days": [],
        # No feature_names or exclude_features
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    features_csv = tmp_path / "features.csv"
    rows = _read_csv_rows(features_csv)
    data_rows = rows[1:]

    # Should have all 9 rows (3 features x 2 symbols x ~1.5 dates avg)
    assert len(data_rows) == 9

    feature_names = set(row[2] for row in data_rows)
    assert feature_names == {"indicator_rsi_14", "indicator_macd", "indicator_bollinger_bands"}


def test_export_with_empty_feature_names_exports_all(tmp_path):
    """Test that empty feature_names list exports all features."""
    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],
        "feature_names": [],  # Empty list should export all
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    features_csv = tmp_path / "features.csv"
    rows = _read_csv_rows(features_csv)
    data_rows = rows[1:]

    # Should have all features
    feature_names = set(row[2] for row in data_rows)
    assert "indicator_rsi_14" in feature_names
    assert "indicator_macd" in feature_names
    assert "indicator_bollinger_bands" in feature_names


def test_export_with_nonexistent_feature_name(tmp_path):
    """Test that requesting non-existent features returns empty (no error)."""
    manifest = {
        "universe": {"symbols": ["AAPL"]},
        "horizons_days": [],
        "feature_names": ["nonexistent_feature"],
    }

    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    features_csv = tmp_path / "features.csv"
    rows = _read_csv_rows(features_csv)

    # Should only have header, no data rows
    assert len(rows) == 1
    assert rows[0] == ["symbol", "date", "feature_name", "value"]
