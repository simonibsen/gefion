import csv
from pathlib import Path

from g2.ml.dataset import export_dataset_artifacts


class _FakeCursor:
    def __init__(self):
        self._rows = []
        self._call_count = 0

    def execute(self, *args, **kwargs):
        self._call_count += 1
        # First call: prices query - return sample price data
        if self._call_count == 1:
            from datetime import date
            self._rows = [
                ("IBM", date(2024, 1, 1), 100.0, 105.0, 99.0, 103.0, 103.0, 1000000),
                ("IBM", date(2024, 1, 2), 103.0, 108.0, 102.0, 106.0, 106.0, 1100000),
            ]
        # Second call: features query - return empty (not testing features)
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
    def __init__(self):
        self._cursor = _FakeCursor()

    def cursor(self):
        return self._cursor


def _read_csv_header(path: Path) -> list[str]:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        return next(reader)


def test_export_dataset_artifacts_writes_csvs(tmp_path):
    manifest = {
        "universe": {"symbols": ["IBM"]},
        "horizons_days": [1],  # Use short horizon that works with 2 data points
        "label_spec": {
            "thresholds": {
                "1": {"weak": 0.02, "strong": 0.05},
            }
        },
    }
    export_dataset_artifacts(_FakeConn(), manifest=manifest, out_dir=tmp_path)

    prices = tmp_path / "prices.csv"
    feats = tmp_path / "features.csv"
    labels = tmp_path / "labels.csv"
    assert prices.exists()
    assert feats.exists()
    assert labels.exists()

    assert _read_csv_header(prices)[:2] == ["symbol", "date"]
    assert _read_csv_header(feats)[:3] == ["symbol", "date", "feature_name"]
    assert _read_csv_header(labels)[:3] == ["symbol", "date", "horizon_days"]


def test_export_dataset_artifacts_emits_progress(tmp_path):
    """Export should call progress callback with status updates."""
    progress_calls = []

    def on_progress(message: str):
        progress_calls.append(message)

    manifest = {
        "universe": {"symbols": ["IBM"]},
        "horizons_days": [1],
        "label_spec": {
            "thresholds": {
                "1": {"weak": 0.02, "strong": 0.05},
            }
        },
    }
    export_dataset_artifacts(
        _FakeConn(), manifest=manifest, out_dir=tmp_path, on_progress=on_progress
    )

    # Should have progress updates for each phase
    assert len(progress_calls) >= 3, f"Expected at least 3 progress calls, got {progress_calls}"
    assert any("price" in msg.lower() for msg in progress_calls)
    assert any("feature" in msg.lower() for msg in progress_calls)
    assert any("label" in msg.lower() for msg in progress_calls)

