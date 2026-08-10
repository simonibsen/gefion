"""TDD tests for chunked (by-symbol) dataset-build export (#209).

Peak memory for `export_dataset_artifacts` must stay bounded regardless of
universe size: prices and labels are exported in batches of symbols, never
all-at-once. Chunking MUST be by symbol, never by date — labels come from a
forward shift within each symbol's own series, so date-chunking would
truncate the forward window at every chunk boundary and silently produce
wrong labels near each seam.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from gefion.ml.dataset import (DatasetBuildTooLargeError,
                               export_dataset_artifacts)

PRICES_HEADER = [
    "symbol", "date", "open", "high", "low", "close", "adjusted_close",
    "volume",
]


class _FakeCursor:
    """Cursor dispatching on SQL content (robust to added queries), and on
    the bound symbol-batch param for prices/count — so a real query per
    symbol-batch is exercised, not one fixed canned response."""

    def __init__(self, price_rows, feature_rows=None):
        self._price_rows = price_rows
        self._feature_rows = feature_rows or []
        self._sql = None
        self._params = None

    def execute(self, sql, params=None):
        self._sql = sql
        self._params = params or ()

    def _price_rows_for_batch(self):
        symbols = self._params[0] if self._params else None
        rows = self._price_rows
        if symbols:
            symset = set(symbols)
            rows = [r for r in rows if r[0] in symset]
        return rows

    def fetchall(self):
        sql = self._sql
        if "COUNT(*)" in sql:
            return [(len(self._price_rows_for_batch()),)]
        if "stock_ohlcv" in sql:
            return list(self._price_rows_for_batch())
        if "computed_features" in sql:
            return list(self._feature_rows)
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def __iter__(self):
        return iter(self.fetchall())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, price_rows, feature_rows=None):
        self._price_rows = price_rows
        self._feature_rows = feature_rows or []
        self.execute_log = []

    def cursor(self):
        cur = _FakeCursor(self._price_rows, self._feature_rows)
        real_execute = cur.execute

        def logged_execute(sql, params=None):
            self.execute_log.append((sql, params))
            return real_execute(sql, params)

        cur.execute = logged_execute
        return cur


def _make_price_rows(symbols, n_days=10, base=100.0):
    """n_days of daily OHLCV per symbol, deterministic and distinct per
    symbol so mixing up rows between symbols would be detectable."""
    rows = []
    start = date(2024, 1, 1)
    for si, sym in enumerate(symbols):
        for d in range(n_days):
            px = base + si * 1000 + d  # symbol-distinct, day-increasing
            rows.append((
                sym, start + timedelta(days=d),
                px, px + 1, px - 1, px, px, 1000 + d,
            ))
    return rows


def _manifest(symbols, *, format="csv", symbol_batch_size=None,
             horizons_days=(3,), thresholds=None, **extra):
    m = {
        "universe": {"symbols": list(symbols)},
        "horizons_days": list(horizons_days),
        "label_spec": {
            "thresholds": thresholds or {
                str(h): {"weak": 0.01, "strong": 0.05} for h in horizons_days
            },
        },
        "format": format,
    }
    if symbol_batch_size is not None:
        m["symbol_batch_size"] = symbol_batch_size
    m.update(extra)
    return m


def _read_csv_rows(path: Path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _sorted_key(row):
    return (row["symbol"], row["date"], str(row.get("horizon_days", "")))


class TestIdenticalOutput:
    """A dataset built in many small batches is row-for-row identical to one
    built in effectively one big batch."""

    @pytest.mark.parametrize("fmt", ["csv", "parquet"])
    def test_chunked_matches_whole_build(self, tmp_path, fmt):
        symbols = [f"SYM{i}" for i in range(7)]
        price_rows = _make_price_rows(symbols, n_days=10)

        whole_dir = tmp_path / "whole"
        chunked_dir = tmp_path / "chunked"
        whole_dir.mkdir()
        chunked_dir.mkdir()

        export_dataset_artifacts(
            _FakeConn(price_rows),
            manifest=_manifest(symbols, format=fmt, symbol_batch_size=1000),
            out_dir=whole_dir,
        )
        export_dataset_artifacts(
            _FakeConn(price_rows),
            manifest=_manifest(symbols, format=fmt, symbol_batch_size=2),
            out_dir=chunked_dir,
        )

        for name in ("prices", "labels"):
            whole_path = whole_dir / f"{name}.{fmt}"
            chunked_path = chunked_dir / f"{name}.{fmt}"
            assert whole_path.exists()
            assert chunked_path.exists()
            if fmt == "csv":
                whole_rows = sorted(_read_csv_rows(whole_path), key=_sorted_key)
                chunked_rows = sorted(_read_csv_rows(chunked_path), key=_sorted_key)
            else:
                whole_rows = sorted(
                    pd.read_parquet(whole_path).astype(str).to_dict("records"),
                    key=_sorted_key)
                chunked_rows = sorted(
                    pd.read_parquet(chunked_path).astype(str).to_dict("records"),
                    key=_sorted_key)
            assert chunked_rows == whole_rows, f"{name}.{fmt} diverged under chunking"
            assert len(chunked_rows) > 0


class TestBoundaryCorrectness:
    """The seam-correctness test: chunking by symbol must never truncate a
    symbol's own forward-return window. A date-chunked implementation would
    fail this (the shift crossing a chunk boundary would go stale/null)."""

    def test_forward_return_at_seam_matches_reference(self, tmp_path):
        symbols = [f"SEAM{i}" for i in range(5)]
        n_days = 12
        price_rows = _make_price_rows(symbols, n_days=n_days)
        horizon = 3

        # Ground-truth forward return/label computed directly (whole-history,
        # no chunking at all) as an independent reference.
        df = pd.DataFrame(price_rows, columns=PRICES_HEADER)
        df["close_for_label"] = df["close"].astype(float)
        df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        shifted = df.groupby("symbol")["close_for_label"].shift(-horizon)
        ref_ret = (shifted / df["close_for_label"]) - 1.0
        ref = pd.DataFrame({
            "symbol": df["symbol"], "date": df["date"].astype(str),
            "forward_return": ref_ret,
        }).dropna(subset=["forward_return"])
        ref = ref.set_index(["symbol", "date"])["forward_return"]

        # Force a batch size that splits the symbol list into several small
        # batches, so the boundary near the END of each symbol's own history
        # (the date range most at risk under mistaken date-chunking) is
        # exercised for every symbol.
        out_dir = tmp_path / "seam"
        export_dataset_artifacts(
            _FakeConn(price_rows),
            manifest=_manifest(symbols, symbol_batch_size=2,
                               horizons_days=[horizon],
                               thresholds={str(horizon): {"weak": 1e-6, "strong": 1e-3}}),
            out_dir=out_dir,
        )

        got = pd.read_csv(out_dir / "labels.csv")
        got["date"] = got["date"].astype(str)
        got_idx = got.set_index(["symbol", "date"])["forward_return"]

        assert len(got_idx) == len(ref), (
            "row count mismatch — chunking dropped or duplicated seam rows"
        )
        for key, ref_val in ref.items():
            assert key in got_idx.index, f"missing seam row {key}"
            assert got_idx.loc[key] == pytest.approx(ref_val), (
                f"forward_return at seam {key} diverged: "
                f"{got_idx.loc[key]} != {ref_val}"
            )


class TestBoundedMemoryStructural:
    """RSS assertions are flaky under CI load, so this asserts the structural
    property instead: symbols are fetched in batch_size-sized chunks (never
    one query for the whole universe), and every batch actually observed on
    the wire is <= batch_size symbols — i.e. peak per-batch working set is
    flat, and batch COUNT grows with symbol count instead."""

    def test_price_queries_are_batched_by_symbol(self, tmp_path):
        symbols = [f"BATCH{i}" for i in range(23)]
        price_rows = _make_price_rows(symbols, n_days=3)
        batch_size = 5
        conn = _FakeConn(price_rows)

        export_dataset_artifacts(
            conn,
            manifest=_manifest(symbols, symbol_batch_size=batch_size,
                               horizons_days=[]),
            out_dir=tmp_path,
        )

        price_query_batches = [
            params[0] for sql, params in conn.execute_log
            if params and "stock_ohlcv" in sql and "COUNT(*)" not in sql
        ]
        assert len(price_query_batches) > 1, (
            "expected multiple batched price queries, got a single "
            f"whole-universe query: {price_query_batches}"
        )
        for batch in price_query_batches:
            assert len(batch) <= batch_size, (
                f"batch of {len(batch)} symbols exceeds symbol_batch_size="
                f"{batch_size} — per-batch working set is not bounded"
            )
        # every symbol covered exactly once across all batches
        seen = [s for batch in price_query_batches for s in batch]
        assert sorted(seen) == sorted(symbols)


class TestLabelErrorHandling:
    """A mid-build failure in label computation (e.g. a bug in the label
    math for one batch) must not leave a partial, silently-truncated labels
    artifact on disk — pre-#209 label computation was all-or-nothing, and
    that invariant must hold even though labels are now written batch by
    batch."""

    def test_mid_batch_label_failure_removes_partial_labels_file(
        self, tmp_path, monkeypatch
    ):
        import gefion.ml.dataset as dataset_mod

        symbols = [f"ERR{i}" for i in range(4)]
        price_rows = _make_price_rows(symbols, n_days=10)

        real_compute = dataset_mod._compute_batch_labels
        calls = {"n": 0}

        def flaky_compute(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("simulated label math bug")
            return real_compute(*args, **kwargs)

        monkeypatch.setattr(dataset_mod, "_compute_batch_labels", flaky_compute)

        messages = []
        out_dir = tmp_path / "flaky"
        dataset_mod.export_dataset_artifacts(
            _FakeConn(price_rows),
            manifest=_manifest(symbols, symbol_batch_size=1),
            out_dir=out_dir,
            on_progress=messages.append,
        )

        # At least one batch succeeded before the failure (proving this
        # exercises the "partial data already written" path, not just an
        # immediate first-batch failure).
        assert calls["n"] >= 2

        assert not (out_dir / "labels.csv").exists(), (
            "a labels file survived a mid-build label failure — it covers "
            "only some symbols but nothing on disk signals that"
        )
        # Prices are unaffected by a labels-only failure.
        assert (out_dir / "prices.csv").exists()
        assert len(_read_csv_rows(out_dir / "prices.csv")) == len(price_rows)

        assert any("Failed to compute labels" in m for m in messages)

    def test_zero_label_rows_with_valid_horizons_leaves_no_labels_file(
        self, tmp_path
    ):
        """Every symbol's history is shorter than every horizon, so each
        batch's forward shift is all-NaN and dropna() empties every batch's
        label frame. valid_horizons is non-empty (thresholds are fine), so
        the writer is opened eagerly and unconditionally writes a header —
        without the fix, a header-only labels.csv/.parquet would survive
        where pre-#209 (`if not labels_df.empty:`) never wrote one."""
        symbols = [f"SHORT{i}" for i in range(3)]
        # 2 days of history, horizon 3: shift(-3) is NaN for every row.
        price_rows = _make_price_rows(symbols, n_days=2)

        messages = []
        for fmt in ("csv", "parquet"):
            out_dir = tmp_path / f"zero_{fmt}"
            export_dataset_artifacts(
                _FakeConn(price_rows),
                manifest=_manifest(symbols, format=fmt, symbol_batch_size=1,
                                   horizons_days=(3,)),
                out_dir=out_dir,
                on_progress=messages.append,
            )
            assert not (out_dir / f"labels.{fmt}").exists(), (
                f"an empty-but-present labels.{fmt} survived a zero-label "
                "build — pre-#209 no file was written in this case"
            )
            assert (out_dir / f"prices.{fmt}").exists()

        assert any("No labels computed" in m for m in messages)


class TestGuardrail:
    """The guardrail refuses an impossible build up front rather than
    inviting the OOM killer, and names the limit + a way out."""

    def test_refuses_when_single_batch_would_still_be_too_big(self, tmp_path):
        symbols = [f"HUGE{i}" for i in range(3)]
        # 500 rows/symbol average; a batch of even 1 symbol exceeds a
        # max_batch_price_rows guardrail of 10.
        price_rows = _make_price_rows(symbols, n_days=500)

        manifest = _manifest(symbols, symbol_batch_size=1, horizons_days=[])
        manifest["max_batch_price_rows"] = 10

        with pytest.raises(DatasetBuildTooLargeError) as exc_info:
            export_dataset_artifacts(
                _FakeConn(price_rows), manifest=manifest, out_dir=tmp_path)

        msg = str(exc_info.value)
        assert "10" in msg  # names the limit
        assert ("window" in msg.lower() or "start-date" in msg.lower()
                or "start_date" in msg.lower())  # suggests a way out

    def test_refuses_before_writing_any_files(self, tmp_path):
        symbols = [f"HUGE{i}" for i in range(3)]
        price_rows = _make_price_rows(symbols, n_days=500)
        manifest = _manifest(symbols, symbol_batch_size=1, horizons_days=[])
        manifest["max_batch_price_rows"] = 10

        with pytest.raises(DatasetBuildTooLargeError):
            export_dataset_artifacts(
                _FakeConn(price_rows), manifest=manifest, out_dir=tmp_path)

        assert not (tmp_path / "prices.csv").exists()

    def test_reasonable_build_does_not_trip_guardrail(self, tmp_path):
        symbols = [f"OK{i}" for i in range(4)]
        price_rows = _make_price_rows(symbols, n_days=5)
        manifest = _manifest(symbols, symbol_batch_size=2, horizons_days=[])

        # Should not raise.
        export_dataset_artifacts(
            _FakeConn(price_rows), manifest=manifest, out_dir=tmp_path)
        assert (tmp_path / "prices.csv").exists()
