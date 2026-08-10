import csv
from pathlib import Path

from gefion.ml.dataset import export_dataset_artifacts


class _FakeCursor:
    """Dispatches on SQL content (not call order) so adding queries — e.g.
    the #209 batch-feasibility guardrail COUNT(*) — doesn't silently
    misdirect an unrelated query's canned response to it."""

    def __init__(self):
        self._sql = ""
        self._price_rows = []

    def execute(self, sql, params=None):
        self._sql = sql
        if self._price_rows:
            return
        from datetime import date
        self._price_rows = [
            ("IBM", date(2024, 1, 1), 100.0, 105.0, 99.0, 103.0, 103.0, 1000000),
            ("IBM", date(2024, 1, 2), 103.0, 108.0, 102.0, 106.0, 106.0, 1100000),
        ]

    def _rows(self):
        if "COUNT(*)" in self._sql:
            return [(len(self._price_rows),)]
        if "stock_ohlcv" in self._sql:
            return list(self._price_rows)
        return []  # features query - not under test here

    def fetchall(self):
        return self._rows()

    def __iter__(self):
        return iter(self._rows())

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


def test_export_handles_decimal_prices(tmp_path):
    """Export should handle Decimal types from PostgreSQL without error.

    PostgreSQL returns NUMERIC columns as Python Decimal objects.
    The label computation must convert these to float for arithmetic.
    """
    from decimal import Decimal
    from datetime import date

    class DecimalCursor:
        """Dispatches on SQL content (see _FakeCursor above for why)."""

        _PRICE_ROWS = [
            ("TEST", date(2024, 1, 1), Decimal("100.00"), Decimal("105.00"),
             Decimal("99.00"), Decimal("103.00"), Decimal("103.00"), 1000000),
            ("TEST", date(2024, 1, 2), Decimal("103.00"), Decimal("108.00"),
             Decimal("102.00"), Decimal("106.00"), Decimal("106.00"), 1100000),
            ("TEST", date(2024, 1, 3), Decimal("106.00"), Decimal("110.00"),
             Decimal("105.00"), Decimal("109.00"), Decimal("109.00"), 1200000),
        ]

        def __init__(self):
            self._sql = ""

        def execute(self, sql, params=None):
            self._sql = sql

        def _rows(self):
            if "COUNT(*)" in self._sql:
                return [(len(self._PRICE_ROWS),)]
            if "stock_ohlcv" in self._sql:
                return list(self._PRICE_ROWS)
            return []

        def fetchall(self):
            return self._rows()

        def __iter__(self):
            return iter(self._rows())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DecimalConn:
        def cursor(self):
            return DecimalCursor()

    manifest = {
        "universe": {"symbols": ["TEST"]},
        "horizons_days": [1],
        "label_spec": {
            "thresholds": {"1": {"weak": 0.02, "strong": 0.05}}
        },
    }

    # Should not raise TypeError: unsupported operand type(s) for -: 'decimal.Decimal' and 'float'
    export_dataset_artifacts(DecimalConn(), manifest=manifest, out_dir=tmp_path)

    labels = tmp_path / "labels.csv"
    assert labels.exists(), "Labels file should be created even with Decimal prices"

    # Verify labels were computed
    import pandas as pd
    df = pd.read_csv(labels)
    assert len(df) > 0, "Should have computed labels"
    assert "forward_return" in df.columns
    assert "label" in df.columns

