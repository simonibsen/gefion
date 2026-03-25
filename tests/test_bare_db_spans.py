"""Tests that bare DB spans are wrapped under descriptive app-level parent spans.

These tests verify that DB operations in the data-update pipeline have
intermediate tracing spans for trace readability, rather than appearing
as bare SELECT/INSERT spans directly under high-level app spans.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import gefion.cli as cli
import gefion.ingest.universe as universe
import gefion.db.ingest as ingest
import gefion.features.dispatcher as dispatcher


class DummySpan:
    def __init__(self, name: str, attrs: dict[str, object]) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.events: list[tuple[str, dict[str, object]]] = []

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attrs.update(attributes)

    def record_exception(self, exc: Exception) -> None:
        pass


def _install_stubs(monkeypatch, *, spans: list[DummySpan], symbols=None):
    """Install common stubs for data-update tracing tests."""
    if symbols is None:
        symbols = ["AAA"]

    @contextmanager
    def fake_create_span(name: str, **attrs):
        span = DummySpan(name, attrs)
        spans.append(span)
        yield span

    class FakeCursor:
        def __init__(self):
            self._last_sql = ""

        def execute(self, sql, params=None):
            self._last_sql = sql

        def fetchall(self):
            if "SELECT DISTINCT symbol FROM stocks" in self._last_sql:
                return [(s,) for s in symbols]
            return []

        def fetchone(self):
            if "count(*) FROM feature_definitions" in self._last_sql:
                return (1,)
            return (0,)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_db_connection(_url):
        yield FakeConn()

    main_span = DummySpan("cli.data-update", {})
    monkeypatch.setattr(cli, "create_span", fake_create_span)
    monkeypatch.setattr(cli, "add_event", lambda span, name, **attrs: span.add_event(name, attrs))
    monkeypatch.setattr(cli, "set_attributes", lambda span, **attrs: span.set_attributes(attrs))
    monkeypatch.setattr(cli, "get_current_span", lambda: main_span)
    monkeypatch.setattr(cli, "db_connection", fake_db_connection)
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_available_connections", lambda _url: None)
    monkeypatch.setattr(universe, "_expected_market_date", lambda: date(2025, 12, 19))
    monkeypatch.setattr(universe, "filter_symbols_needing_update", lambda c, s, d: s[:1])
    monkeypatch.setattr(cli, "AlphaVantageClient", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", lambda **kw: 0)
    monkeypatch.setattr(ingest, "upsert_stock", lambda _c, _s: 42)
    monkeypatch.setattr(cli, "compute_features", lambda *a, **kw: {"summary": {"total_inserted": 0}})
    return main_span


def test_check_migrations_has_span(monkeypatch):
    """check_pending_migrations() should be wrapped in a cli.check_migrations span."""
    spans: list[DummySpan] = []
    _install_stubs(monkeypatch, spans=spans)

    cli._update_all_impl(
        exchange=None, status="Active", timeframe="auto",
        feature_batch_size=200, refresh_existing=False, refresh=False,
        limit=None, max_workers=None, writer_workers=None,
        calls_per_minute=75, db_url="postgresql://example",
        listings_file=None, progress=False, json_output=True,
    )

    span_names = [s.name for s in spans]
    assert "cli.check_migrations" in span_names, (
        f"Expected 'cli.check_migrations' span, got: {span_names}"
    )


def test_price_filter_has_schema_init_span(monkeypatch):
    """init_schema_tables() inside price_filter should have a schema_init sub-span."""
    spans: list[DummySpan] = []
    _install_stubs(monkeypatch, spans=spans)

    cli._update_all_impl(
        exchange=None, status="Active", timeframe="auto",
        feature_batch_size=200, refresh_existing=False, refresh=False,
        limit=None, max_workers=None, writer_workers=None,
        calls_per_minute=75, db_url="postgresql://example",
        listings_file=None, progress=False, json_output=True,
    )

    span_names = [s.name for s in spans]
    assert "price_filter.schema_init" in span_names, (
        f"Expected 'price_filter.schema_init' span, got: {span_names}"
    )


def test_price_filter_has_filter_symbols_span(monkeypatch):
    """filter_symbols_needing_update() should have a filter_symbols sub-span."""
    spans: list[DummySpan] = []
    _install_stubs(monkeypatch, spans=spans)

    cli._update_all_impl(
        exchange=None, status="Active", timeframe="auto",
        feature_batch_size=200, refresh_existing=False, refresh=False,
        limit=None, max_workers=None, writer_workers=None,
        calls_per_minute=75, db_url="postgresql://example",
        listings_file=None, progress=False, json_output=True,
    )

    span_names = [s.name for s in spans]
    assert "price_filter.filter_symbols" in span_names, (
        f"Expected 'price_filter.filter_symbols' span, got: {span_names}"
    )


def test_compute_features_has_fetch_definitions_span():
    """_fetch_feature_definitions() should be wrapped in a compute_features.fetch_definitions span."""
    spans: list[str] = []

    @contextmanager
    def fake_create_span(name, **attrs):
        spans.append(name)
        yield DummySpan(name, attrs)

    class FakeCursor:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    with patch.object(dispatcher, "create_span", fake_create_span):
        dispatcher.compute_features(FakeConn(), data_id=1)

    assert "compute_features.fetch_definitions" in spans, (
        f"Expected 'compute_features.fetch_definitions' span, got: {spans}"
    )


def test_compute_features_has_latest_dates_span():
    """_latest_dates_for_features() should be wrapped in a compute_features.latest_dates span."""
    spans: list[str] = []

    @contextmanager
    def fake_create_span(name, **attrs):
        spans.append(name)
        yield DummySpan(name, attrs)

    call_count = [0]

    class FakeCursor:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            if call_count[0] == 0:
                # First call: _fetch_feature_definitions returns one feature
                call_count[0] += 1
                return [(1, "feat", "indicator", "{}", "stock_ohlcv", "close", "computed_features", "value")]
            # Second call: _latest_dates_for_features
            return [(1, date(2025, 1, 1))]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    with patch.object(dispatcher, "create_span", fake_create_span), \
         patch.object(dispatcher, "_group_by_function_name", return_value={}):
        dispatcher.compute_features(FakeConn(), data_id=1, incremental=True)

    assert "compute_features.latest_dates" in spans, (
        f"Expected 'compute_features.latest_dates' span, got: {spans}"
    )
