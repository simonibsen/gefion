"""Unit tests for insert_predictions_batch — batching mechanics, no DB required."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import gefion.db.predictions as predictions


def _row(model_id=1, data_id=1, horizon_days=5, run_id=None, q50=0.01):
    return {
        "model_id": model_id,
        "data_id": data_id,
        "prediction_date": date(2026, 1, 1),
        "horizon_days": horizon_days,
        "prediction_type": "quantile",
        "prediction_values": {"q10": q50 - 0.01, "q50": q50, "q90": q50 + 0.01},
        "metadata": {"model_version": "v1"},
        "run_id": run_id,
    }


class CountingCursor:
    """Records each execute() call so tests can assert statement count."""

    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))


class DummySpan:
    def __init__(self, name, attrs):
        self.name = name
        self.attrs = dict(attrs)


def _fake_span_factory(spans):
    @contextmanager
    def fake_create_span(name, **attrs):
        span = DummySpan(name, attrs)
        spans.append(span)
        yield span
    return fake_create_span


def test_batch_insert_issues_one_statement_for_n_rows():
    """N rows within one batch produce exactly one execute() call, not N."""
    cur = CountingCursor()
    rows = [_row(data_id=i) for i in range(5)]

    predictions.insert_predictions_batch(cur, rows)

    assert len(cur.calls) == 1


def test_batch_insert_single_statement_contains_all_row_values():
    """The one statement's params carry all N rows' worth of columns."""
    cur = CountingCursor()
    rows = [_row(data_id=i) for i in range(5)]

    predictions.insert_predictions_batch(cur, rows)

    _, params = cur.calls[0]
    assert len(params) == 5 * 8  # 8 columns per row


def test_batch_insert_partial_final_batch_writes_every_row():
    """A row count that isn't a multiple of batch_size still writes every row."""
    cur = CountingCursor()
    rows = [_row(data_id=i) for i in range(7)]

    written = predictions.insert_predictions_batch(cur, rows, batch_size=3)

    assert written == 7
    # ceil(7 / 3) == 3 chunks: two full batches of 3, one partial batch of 1
    assert len(cur.calls) == 3
    sizes = [len(params) // 8 for _, params in cur.calls]
    assert sizes == [3, 3, 1]


def test_batch_insert_empty_rows_is_a_noop():
    """An empty row list issues no statements."""
    cur = CountingCursor()

    written = predictions.insert_predictions_batch(cur, [])

    assert written == 0
    assert cur.calls == []


def test_batch_insert_spans_scale_with_batches_not_rows():
    """One span per flush (batch), not one span per row."""
    spans: list[DummySpan] = []
    cur = CountingCursor()
    rows = [_row(data_id=i) for i in range(7)]

    from unittest.mock import patch
    with patch.object(predictions, "create_span", _fake_span_factory(spans)):
        predictions.insert_predictions_batch(cur, rows, batch_size=3)

    batch_spans = [s for s in spans if s.name == "db.predictions.insert_batch"]
    assert len(batch_spans) == 3
    assert [s.attrs["batch_size"] for s in batch_spans] == [3, 3, 1]
