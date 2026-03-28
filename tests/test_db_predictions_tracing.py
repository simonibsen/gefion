"""Tests that db.predictions functions emit OpenTelemetry spans."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import gefion.db.predictions as predictions


class DummySpan:
    def __init__(self, name: str, attrs: dict[str, object]) -> None:
        self.name = name
        self.attrs = dict(attrs)

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attrs.update(attributes)


def _make_fake_span_factory(spans: list[DummySpan]):
    @contextmanager
    def fake_create_span(name: str, **attrs):
        span = DummySpan(name, attrs)
        spans.append(span)
        yield span
    return fake_create_span


def _fake_set_attributes(span, **attrs):
    span.set_attributes(attrs)


class FakeCursor:
    """Minimal cursor stub that accepts any SQL."""
    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)


# ---------------------------------------------------------------------------
# insert_prediction
# ---------------------------------------------------------------------------

def test_insert_prediction_creates_span():
    """insert_prediction wraps SQL execute in a db.predictions.insert span."""
    spans: list[DummySpan] = []
    cur = FakeCursor()

    with patch.object(predictions, "create_span", _make_fake_span_factory(spans)):
        predictions.insert_prediction(
            cur, model_id=1, data_id=2,
            prediction_date=date(2026, 1, 1), horizon_days=5,
            prediction_type="quantile", values_dict={"q50": 0.01},
        )

    names = [s.name for s in spans]
    assert "db.predictions.insert" in names
    span = next(s for s in spans if s.name == "db.predictions.insert")
    assert span.attrs["prediction_type"] == "quantile"
    assert span.attrs["model_id"] == 1


# ---------------------------------------------------------------------------
# insert_quantile_prediction
# ---------------------------------------------------------------------------

def test_insert_quantile_prediction_creates_span():
    """insert_quantile_prediction wraps in a db.predictions.insert_quantile span."""
    spans: list[DummySpan] = []
    cur = FakeCursor()

    with patch.object(predictions, "create_span", _make_fake_span_factory(spans)):
        predictions.insert_quantile_prediction(
            cur, model_id=10, data_id=20,
            prediction_date=date(2026, 1, 1), horizon_days=5,
            q10=-0.02, q50=0.01, q90=0.04,
        )

    names = [s.name for s in spans]
    assert "db.predictions.insert_quantile" in names
    span = next(s for s in spans if s.name == "db.predictions.insert_quantile")
    assert span.attrs["model_id"] == 10
    assert span.attrs["data_id"] == 20


# ---------------------------------------------------------------------------
# insert_trend_prediction
# ---------------------------------------------------------------------------

def test_insert_trend_prediction_creates_span():
    """insert_trend_prediction wraps in a db.predictions.insert_trend span."""
    spans: list[DummySpan] = []
    cur = FakeCursor()

    with patch.object(predictions, "create_span", _make_fake_span_factory(spans)):
        predictions.insert_trend_prediction(
            cur, model_id=10, data_id=20,
            prediction_date=date(2026, 1, 1), horizon_days=5,
            predicted_class="neutral",
            class_probs={"p_up": 0.5, "p_down": 0.5},
            entropy=1.0, margin=0.0,
        )

    names = [s.name for s in spans]
    assert "db.predictions.insert_trend" in names
    span = next(s for s in spans if s.name == "db.predictions.insert_trend")
    assert span.attrs["model_id"] == 10
    assert span.attrs["data_id"] == 20


# ---------------------------------------------------------------------------
# query_predictions
# ---------------------------------------------------------------------------

def test_query_predictions_creates_span_with_result_count():
    """query_predictions wraps in a db.predictions.query span and sets result_count."""
    spans: list[DummySpan] = []
    cur = FakeCursor()

    with patch.object(predictions, "create_span", _make_fake_span_factory(spans)), \
         patch.object(predictions, "set_attributes", _fake_set_attributes):
        rows = predictions.query_predictions(cur, prediction_type="quantile")

    names = [s.name for s in spans]
    assert "db.predictions.query" in names
    span = next(s for s in spans if s.name == "db.predictions.query")
    assert span.attrs["prediction_type"] == "quantile"
    assert span.attrs["result_count"] == 0  # FakeCursor.fetchall returns []
