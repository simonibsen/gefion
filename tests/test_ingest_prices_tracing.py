from __future__ import annotations

from contextlib import contextmanager

import g2.ingest.universe as universe


def test_ingest_prices_schema_init_span(monkeypatch):
    entered = {"span": False}

    @contextmanager
    def fake_create_span(name: str, **attrs):
        entered["span"] = True
        assert name == "ingest_prices.schema_init"
        yield object()

    def fail_connect(*args, **kwargs):
        raise RuntimeError("stop after schema init span")

    monkeypatch.setattr(universe, "create_span", fake_create_span)
    monkeypatch.setattr(universe.psycopg, "connect", fail_connect)

    try:
        universe.ingest_prices_for_symbols(
            db_url="postgresql://example",
            client=object(),
            symbols=["AAA"],
            max_workers=1,
            writer_workers=1,
        )
    except RuntimeError as exc:
        assert "stop after schema init span" in str(exc)
    else:  # pragma: no cover - should not reach
        raise AssertionError("Expected connect failure")

    assert entered["span"] is True
