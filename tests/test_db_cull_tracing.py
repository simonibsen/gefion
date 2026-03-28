"""Tests that db.cull functions emit OpenTelemetry spans with events."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch, MagicMock

import gefion.db.cull as cull


class DummySpan:
    def __init__(self, name: str, attrs: dict[str, object]) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.events: list[tuple[str, dict[str, object]]] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attrs.update(attributes)

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))


def _make_fake_span_factory(spans: list[DummySpan]):
    @contextmanager
    def fake_create_span(name: str, **attrs):
        span = DummySpan(name, attrs)
        spans.append(span)
        yield span
    return fake_create_span


def _fake_set_attributes(span, **attrs):
    span.set_attributes(attrs)


def _fake_add_event(span, name, **attrs):
    span.add_event(name, attrs)


class FakeCursor:
    """Cursor that simulates table existence and row counts for cull tests."""
    def __init__(self):
        self._last_sql = ""
        self._counts = {"predictions": 5, "stock_ohlcv": 3}
        self._rowcount = 0

    @property
    def rowcount(self):
        return self._rowcount

    def execute(self, sql, params=None):
        self._last_sql = sql
        # Simulate deletion rowcount
        if sql.strip().startswith("DELETE"):
            for table, count in self._counts.items():
                if table in sql:
                    self._rowcount = count
                    self._counts[table] = 0  # subsequent deletes return 0
                    return
            self._rowcount = 0

    def fetchall(self):
        return []

    def fetchone(self):
        if "information_schema.tables" in self._last_sql:
            return (1,)  # table exists
        if "COUNT(*)" in self._last_sql:
            for table, count in self._counts.items():
                if table in self._last_sql:
                    return (count,)
            return (0,)
        return (0,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self):
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


# ---------------------------------------------------------------------------
# plan_cull
# ---------------------------------------------------------------------------

def test_plan_cull_creates_span_with_total_rows():
    """plan_cull wraps in a db.cull.plan span and sets total_rows."""
    spans: list[DummySpan] = []
    conn = FakeConn()

    with patch.object(cull, "create_span", _make_fake_span_factory(spans)), \
         patch.object(cull, "set_attributes", _fake_set_attributes):
        result = cull.plan_cull(conn, before_date=date(2026, 1, 1))

    names = [s.name for s in spans]
    assert "db.cull.plan" in names
    span = next(s for s in spans if s.name == "db.cull.plan")
    assert span.attrs["before_date"] == "2026-01-01"
    assert "total_rows" in span.attrs


# ---------------------------------------------------------------------------
# execute_cull
# ---------------------------------------------------------------------------

def test_execute_cull_creates_span_with_events():
    """execute_cull wraps in a db.cull.execute span, emits per-table events, sets total_deleted."""
    spans: list[DummySpan] = []
    conn = FakeConn()

    with patch.object(cull, "create_span", _make_fake_span_factory(spans)), \
         patch.object(cull, "set_attributes", _fake_set_attributes), \
         patch.object(cull, "add_event", _fake_add_event):
        result = cull.execute_cull(conn, before_date=date(2026, 1, 1))

    names = [s.name for s in spans]
    assert "db.cull.execute" in names
    span = next(s for s in spans if s.name == "db.cull.execute")
    assert span.attrs["before_date"] == "2026-01-01"
    assert "total_deleted" in span.attrs

    # Should have at least one deletion event
    event_names = [e[0] for e in span.events]
    # At least one deleted_<table> event should exist
    assert any(name.startswith("deleted_") for name in event_names), (
        f"Expected at least one deleted_* event, got: {event_names}"
    )
