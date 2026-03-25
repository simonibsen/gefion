"""
TDD tests for Parquet loading support in ML models.

Tests that load_dataset can load both CSV and Parquet formats.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_features_df():
    """Create sample features in long format."""
    return pd.DataFrame({
        "symbol": ["AAPL", "AAPL", "AAPL", "AAPL", "MSFT", "MSFT", "MSFT", "MSFT"],
        "date": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02",
                 "2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
        "feature_name": ["rsi_14", "macd", "rsi_14", "macd",
                         "rsi_14", "macd", "rsi_14", "macd"],
        "value": [50.0, 0.5, 55.0, 0.6, 45.0, -0.3, 48.0, -0.1],
    })


@pytest.fixture
def sample_labels_df():
    """Create sample labels."""
    return pd.DataFrame({
        "symbol": ["AAPL", "AAPL", "MSFT", "MSFT"],
        "date": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"],
        "horizon_days": [7, 7, 7, 7],
        "forward_return": [0.05, 0.03, -0.02, 0.01],
        "label": ["weak_up", "flat", "weak_down", "flat"],
    })


@pytest.fixture
def sample_manifest():
    """Create sample dataset manifest."""
    return {
        "name": "test_dataset",
        "version": "v1",
        "horizons_days": [7],
        "format": "parquet",
    }


class TestLoadDatasetParquet:
    """Tests for loading datasets from Parquet files."""

    def test_load_prefers_parquet_over_csv(self, tmp_path, sample_features_df, sample_labels_df, sample_manifest):
        """When both CSV and Parquet exist, Parquet should be preferred."""
        pytest.importorskip("pyarrow")
        from gefion.ml.models import load_dataset

        # Create both CSV and Parquet files
        sample_features_df.to_csv(tmp_path / "features.csv", index=False)
        sample_labels_df.to_csv(tmp_path / "labels.csv", index=False)

        # Create Parquet with different data to verify it's used
        features_parquet = sample_features_df.copy()
        features_parquet["value"] = features_parquet["value"] * 2  # Double values
        features_parquet.to_parquet(tmp_path / "features.parquet", index=False)
        sample_labels_df.to_parquet(tmp_path / "labels.parquet", index=False)

        # Create manifest
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(sample_manifest))

        # Load dataset
        X, y = load_dataset(manifest_path, horizon_days=7)

        # Should use Parquet (doubled values)
        assert X["rsi_14"].iloc[0] == 100.0  # 50.0 * 2

    def test_load_from_parquet_only(self, tmp_path, sample_features_df, sample_labels_df, sample_manifest):
        """Load from Parquet when only Parquet files exist."""
        pytest.importorskip("pyarrow")
        from gefion.ml.models import load_dataset

        # Create only Parquet files
        sample_features_df.to_parquet(tmp_path / "features.parquet", index=False)
        sample_labels_df.to_parquet(tmp_path / "labels.parquet", index=False)

        # Create manifest
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(sample_manifest))

        # Load dataset
        X, y = load_dataset(manifest_path, horizon_days=7)

        # Verify correct shape and data
        assert X.shape == (4, 2)  # 4 samples, 2 features (rsi_14, macd)
        assert len(y) == 4
        assert "rsi_14" in X.columns
        assert "macd" in X.columns

    def test_load_from_csv_fallback(self, tmp_path, sample_features_df, sample_labels_df, sample_manifest):
        """Load from CSV when only CSV files exist (backward compatibility)."""
        from gefion.ml.models import load_dataset

        # Create only CSV files
        sample_features_df.to_csv(tmp_path / "features.csv", index=False)
        sample_labels_df.to_csv(tmp_path / "labels.csv", index=False)

        # Create manifest
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(sample_manifest))

        # Load dataset
        X, y = load_dataset(manifest_path, horizon_days=7)

        # Verify correct shape
        assert X.shape == (4, 2)
        assert len(y) == 4

    def test_load_parquet_preserves_types(self, tmp_path, sample_manifest):
        """Parquet loading should preserve numeric types."""
        pytest.importorskip("pyarrow")
        from gefion.ml.models import load_dataset

        # Create features with specific types
        features_df = pd.DataFrame({
            "symbol": ["AAPL", "AAPL"],
            "date": ["2024-01-01", "2024-01-02"],
            "feature_name": ["volume_ratio", "volume_ratio"],
            "value": [1.5, 2.0],
        })
        features_df["value"] = features_df["value"].astype(np.float64)

        labels_df = pd.DataFrame({
            "symbol": ["AAPL", "AAPL"],
            "date": ["2024-01-01", "2024-01-02"],
            "horizon_days": [7, 7],
            "forward_return": [0.05, 0.03],
            "label": ["weak_up", "flat"],
        })

        features_df.to_parquet(tmp_path / "features.parquet", index=False)
        labels_df.to_parquet(tmp_path / "labels.parquet", index=False)

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(sample_manifest))

        X, y = load_dataset(manifest_path, horizon_days=7)

        assert X["volume_ratio"].dtype == np.float64
        assert y.dtype == np.float64

    def test_load_raises_when_no_files_exist(self, tmp_path, sample_manifest):
        """Should raise FileNotFoundError when no data files exist."""
        from gefion.ml.models import load_dataset

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(sample_manifest))

        with pytest.raises(FileNotFoundError):
            load_dataset(manifest_path, horizon_days=7)


class TestLoadDatasetFunction:
    """Tests for the load_dataset function."""

    def test_function_exists(self):
        """The function should be importable."""
        from gefion.ml.models import load_dataset
        assert callable(load_dataset)
