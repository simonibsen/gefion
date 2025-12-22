from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import g2.cli as cli
import g2.ingest.universe as universe
import g2.db.ingest as ingest


class DummySpan:
    def __init__(self, name: str, attrs: dict[str, object]) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.events: list[tuple[str, dict[str, object]]] = []
        self.exceptions: list[Exception] = []

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attrs.update(attributes)

    def record_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)


class DummyCursor:
    def __init__(self, symbols: list[str], active_feature_defs: int) -> None:
        self.symbols = symbols
        self.active_feature_defs = active_feature_defs
        self._last_sql = ""

    def execute(self, sql: str, params=None) -> None:  # pragma: no cover - used in tests
        self._last_sql = sql

    def fetchall(self):  # pragma: no cover - used in tests
        if "SELECT DISTINCT symbol FROM stocks" in self._last_sql:
            return [(symbol,) for symbol in self.symbols]
        return []

    def fetchone(self):  # pragma: no cover - used in tests
        if "SELECT count(*) FROM feature_definitions" in self._last_sql:
            return (self.active_feature_defs,)
        return (0,)

    def __enter__(self):  # pragma: no cover - used in tests
        return self

    def __exit__(self, exc_type, exc, tb):  # pragma: no cover - used in tests
        return False


class DummyConn:
    def __init__(self, symbols: list[str], active_feature_defs: int) -> None:
        self._symbols = symbols
        self._active_feature_defs = active_feature_defs

    def cursor(self):  # pragma: no cover - used in tests
        return DummyCursor(self._symbols, self._active_feature_defs)


def _install_common_stubs(
    monkeypatch,
    *,
    symbols: list[str],
    active_feature_defs: int,
    spans: list[DummySpan],
    filter_func=None,
    ingest_return=None,
    main_span: DummySpan | None = None,
) -> None:
    @contextmanager
    def fake_create_span(name: str, **attrs):
        span = DummySpan(name, attrs)
        spans.append(span)
        yield span

    def fake_db_connection(_url: str):
        @contextmanager
        def _ctx():
            yield DummyConn(symbols, active_feature_defs)
        return _ctx()

    monkeypatch.setattr(cli, "create_span", fake_create_span)
    monkeypatch.setattr(cli, "add_event", lambda span, name, **attrs: span.add_event(name, attrs))
    monkeypatch.setattr(cli, "set_attributes", lambda span, **attrs: span.set_attributes(attrs))
    if main_span is None:
        main_span = DummySpan("cli.data-update", {})
    monkeypatch.setattr(cli, "get_current_span", lambda: main_span)
    monkeypatch.setattr(cli, "db_connection", fake_db_connection)
    monkeypatch.setattr(cli, "init_schema_tables", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_available_connections", lambda _url: None)
    monkeypatch.setattr(universe, "_expected_market_date", lambda: date(2025, 12, 19))
    if filter_func is None:
        filter_func = lambda conn, symbols, target_date: symbols[:1]
    monkeypatch.setattr(universe, "filter_symbols_needing_update", filter_func)

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    if ingest_return is None:
        ingest_return = lambda **kwargs: 0
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", ingest_return)
    monkeypatch.setattr(ingest, "upsert_stock", lambda _conn, _symbol: 42)


def test_data_update_skips_feature_compute_when_no_active_defs(monkeypatch):
    spans: list[DummySpan] = []
    _install_common_stubs(monkeypatch, symbols=["AAA", "BBB"], active_feature_defs=0, spans=spans)

    def fail_compute(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("compute_features should be skipped when no active feature definitions exist")

    monkeypatch.setattr(cli, "compute_features", fail_compute)

    cli._update_all_impl(
        exchange=None,
        status="Active",
        timeframe="auto",
        feature_batch_size=200,
        refresh_existing=False,
        refresh=False,
        limit=None,
        max_workers=None,
        writer_workers=None,
        calls_per_minute=75,
        db_url="postgresql://example",
        listings_file=None,
        progress=False,
        json_output=True,
    )

    feature_spans = [span for span in spans if span.name == "data_update.feature_compute"]
    assert len(feature_spans) == 1
    assert feature_spans[0].attrs.get("active_feature_defs") == 0
    assert feature_spans[0].attrs.get("skipped") is True


def test_data_update_creates_feature_symbol_spans(monkeypatch):
    spans: list[DummySpan] = []
    _install_common_stubs(monkeypatch, symbols=["AAA", "BBB"], active_feature_defs=2, spans=spans)

    compute_calls: list[int] = []

    def fake_compute(_conn, **kwargs):
        compute_calls.append(kwargs.get("data_id"))
        return {"summary": {"total_inserted": 0}}

    monkeypatch.setattr(cli, "compute_features", fake_compute)

    cli._update_all_impl(
        exchange=None,
        status="Active",
        timeframe="auto",
        feature_batch_size=200,
        refresh_existing=False,
        refresh=False,
        limit=None,
        max_workers=None,
        writer_workers=None,
        calls_per_minute=75,
        db_url="postgresql://example",
        listings_file=None,
        progress=False,
        json_output=True,
    )

    symbol_spans = [span for span in spans if span.name == "data_update.feature_symbol"]
    assert {span.attrs.get("symbol") for span in symbol_spans} == {"AAA", "BBB"}
    assert all(span.attrs.get("data_id") == 42 for span in symbol_spans)
    assert all(span.attrs.get("inserted") == 0 for span in symbol_spans)
    assert all(span.attrs.get("error") is False for span in symbol_spans)
    assert len(compute_calls) == 2


def test_data_update_records_price_chunk_events(monkeypatch):
    spans: list[DummySpan] = []
    symbols = [f"SYM{i:02d}" for i in range(60)]

    def ingest_return(**kwargs):
        return len(kwargs["symbols"])

    _install_common_stubs(
        monkeypatch,
        symbols=symbols,
        active_feature_defs=0,
        spans=spans,
        filter_func=lambda conn, symbols, target_date: symbols,
        ingest_return=ingest_return,
    )

    cli._update_all_impl(
        exchange=None,
        status="Active",
        timeframe="auto",
        feature_batch_size=200,
        refresh_existing=False,
        refresh=False,
        limit=None,
        max_workers=None,
        writer_workers=None,
        calls_per_minute=75,
        db_url="postgresql://example",
        listings_file=None,
        progress=False,
        json_output=True,
    )

    price_span = next(span for span in spans if span.name == "data_update.price_ingest")
    events = [event for event in price_span.events if event[0] == "price_chunk_complete"]
    assert [event[1]["chunk_size"] for event in events] == [50, 10]
    assert [event[1]["inserted"] for event in events] == [50, 10]


def test_data_update_sets_main_span_attributes(monkeypatch):
    spans: list[DummySpan] = []
    main_span = DummySpan("cli.data-update", {})
    _install_common_stubs(
        monkeypatch,
        symbols=["AAA", "BBB", "CCC"],
        active_feature_defs=1,
        spans=spans,
        filter_func=lambda conn, symbols, target_date: symbols[:1],
        main_span=main_span,
    )

    monkeypatch.setattr(cli, "compute_features", lambda *args, **kwargs: {"summary": {"total_inserted": 0}})

    cli._update_all_impl(
        exchange=None,
        status="Active",
        timeframe="auto",
        feature_batch_size=200,
        refresh_existing=False,
        refresh=False,
        limit=None,
        max_workers=None,
        writer_workers=None,
        calls_per_minute=75,
        db_url="postgresql://example",
        listings_file=None,
        progress=False,
        json_output=True,
    )

    assert main_span.attrs.get("active_feature_defs") == 1
    assert main_span.attrs.get("symbol_count") == 3
    assert main_span.attrs.get("price_symbols") == 1
    assert main_span.attrs.get("price_skipped") == 2


def test_feature_symbol_span_records_error(monkeypatch):
    spans: list[DummySpan] = []
    _install_common_stubs(monkeypatch, symbols=["AAA"], active_feature_defs=1, spans=spans)

    def fail_compute(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "compute_features", fail_compute)

    cli._update_all_impl(
        exchange=None,
        status="Active",
        timeframe="auto",
        feature_batch_size=200,
        refresh_existing=False,
        refresh=False,
        limit=None,
        max_workers=None,
        writer_workers=None,
        calls_per_minute=75,
        db_url="postgresql://example",
        listings_file=None,
        progress=False,
        json_output=True,
    )

    symbol_span = next(span for span in spans if span.name == "data_update.feature_symbol")
    assert symbol_span.attrs.get("error") is True
    assert symbol_span.exceptions


def test_span_check_warns_when_otel_disabled(monkeypatch):
    spans: list[DummySpan] = []
    main_span = DummySpan("cli.data-update", {})
    _install_common_stubs(
        monkeypatch,
        symbols=["AAA"],
        active_feature_defs=0,
        spans=spans,
        main_span=main_span,
    )

    monkeypatch.setattr(cli.os, "getenv", lambda key, default=None: "false" if key == "OTEL_ENABLED" else default)
    monkeypatch.setattr(cli, "_tempo_get_json", lambda *args, **kwargs: {"traces": [{"traceID": "t1"}], "metrics": {"inspectedTraces": 1}})
    warnings: list[str] = []

    def fake_emit(message, *args, **kwargs):
        warnings.append(message)

    monkeypatch.setattr(cli, "emit", fake_emit)
    monkeypatch.setattr(cli, "emit_json", lambda *args, **kwargs: None)

    cli.span_check(
        backend="tempo",
        tempo_url="http://localhost:3200",
        service_name="g2",
        limit=1,
        trace_id="t1",
        show_spans=False,
        json_output=True,
    )

    assert any("OTEL_ENABLED" in msg for msg in warnings)
