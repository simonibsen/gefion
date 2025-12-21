import csv
from pathlib import Path

from g2.ml.dataset import export_dataset_artifacts


class _FakeCursor:
    def __init__(self):
        self._rows = []

    def execute(self, *args, **kwargs):
        self._rows = []

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()


def _read_csv_header(path: Path) -> list[str]:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        return next(reader)


def test_export_dataset_artifacts_writes_csvs(tmp_path):
    manifest = {
        "universe": {"symbols": ["IBM"]},
        "horizons_days": [7, 30, 90],
        "label_spec": {"thresholds": {"7": {"weak": 0.02, "strong": 0.05}}},
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

