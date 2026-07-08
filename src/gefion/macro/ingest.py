"""Macro-series ingestion + feature materialization (007, T018 — US2).

Provider dispatch is the catalog row's `provider` string:

    fred:<SERIES>              — FRED's keyless CSV endpoint (value-only).
                                 The default for VIX: the AlphaVantage
                                 INDEX_DATA endpoint returned not-entitled on
                                 the production key (T016, research.md).
    alphavantage:INDEX_DATA    — premium OHLC endpoint; a config change away
                                 if the key is ever upgraded.

Materialization bridges to the feature store: `macro_<name>` declares
`entity_table='macro_series'`, and values land in computed_features keyed by
the series id — discovery and regimes consume them with zero equity-pipeline
changes.
"""
from __future__ import annotations

import csv
import datetime
import io
from typing import Any, Callable, Dict, List, Optional

from gefion.macro import catalog
from gefion.observability import create_span, set_attributes

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


class MacroIngestError(ValueError):
    """Raised on an unknown provider or an unavailable endpoint."""


def parse_fred_csv(text: str) -> List[Dict[str, Any]]:
    """FRED CSV (observation_date,<SERIES>) → [{date, value}].

    Missing observations are '.' and are skipped, not fatal.
    """
    rows: List[Dict[str, Any]] = []
    for record in csv.reader(io.StringIO(text)):
        if len(record) != 2:
            continue
        date_str, value_str = record
        try:
            rows.append({
                "date": datetime.date.fromisoformat(date_str),
                "value": float(value_str),
            })
        except ValueError:
            continue  # header row or '.' missing marker
    return rows


def _fetch_fred(series: str) -> str:
    import urllib.request
    with urllib.request.urlopen(FRED_CSV_URL.format(series=series),
                                timeout=60) as resp:
        return resp.read().decode("utf-8")


def _fetch_rows(provider: str, full: bool) -> List[Dict[str, Any]]:
    """Dispatch on the provider string; honest refusals name what works."""
    scheme, _, arg = provider.partition(":")
    if scheme == "fred" and arg:
        return parse_fred_csv(_fetch_fred(arg))
    if provider == "alphavantage:INDEX_DATA":
        from gefion.alphavantage.catalog import parse_index_data
        from gefion.alphavantage.client import AlphaVantageClient
        payload = AlphaVantageClient().fetch_index_data(
            outputsize="full" if full else "compact")
        rows = parse_index_data(payload)
        if not rows:
            raise MacroIngestError(
                "alphavantage:INDEX_DATA returned no data (premium endpoint — "
                "the production key is not entitled). Use provider "
                "'fred:VIXCLS' instead (the default; keyless).")
        return rows
    raise MacroIngestError(
        f"unknown provider {provider!r} — supported: 'fred:<SERIES>' "
        "(e.g. fred:VIXCLS), 'alphavantage:INDEX_DATA'")


def upsert_values(conn, series_id: int, rows: List[Dict[str, Any]]) -> int:
    """Idempotent upsert into macro_series_values; returns rows written."""
    with create_span("macro.ingest.upsert_values", series_id=series_id) as span:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO macro_series_values
                           (series_id, date, value, open, high, low)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (series_id, date) DO UPDATE SET
                           value = EXCLUDED.value, open = EXCLUDED.open,
                           high = EXCLUDED.high, low = EXCLUDED.low""",
                    (series_id, r["date"], r["value"],
                     r.get("open"), r.get("high"), r.get("low")),
                )
        set_attributes(span, n_rows=len(rows))
        return len(rows)


def materialize_feature(conn, name: str) -> Dict[str, Any]:
    """Ensure the `macro_<name>` feature definition and copy the series into
    computed_features (data_id = the series id). Idempotent."""
    from gefion.db.ingest import ensure_feature_definitions

    with create_span("macro.ingest.materialize_feature", series=name) as span:
        series = catalog.get_series(conn, name)
        if series is None:
            raise MacroIngestError(f"no macro series named {name!r} — "
                                   "create the catalog row first (macro ingest)")
        feature_name = f"macro_{name}"
        ids = ensure_feature_definitions(conn, [{
            "name": feature_name, "function_name": "macro_value",
            "params": None, "source_table": "macro_series_values",
            "source_column": "value", "store_table": "computed_features",
            "store_column": "value", "store_type": "double precision",
            "active": True, "entity_table": "macro_series",
        }])
        feature_id = ids[feature_name]
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO computed_features (data_id, date, feature_id, value)
                   SELECT series_id, date, %s, value
                   FROM macro_series_values WHERE series_id = %s
                   ON CONFLICT (feature_id, data_id, date)
                   DO UPDATE SET value = EXCLUDED.value""",
                (feature_id, series["id"]),
            )
            cur.execute(
                "SELECT count(*) FROM computed_features "
                "WHERE feature_id = %s AND data_id = %s",
                (feature_id, series["id"]))
            n_values = cur.fetchone()[0]
        set_attributes(span, feature_id=feature_id, n_values=n_values)
        return {"feature": feature_name, "values": n_values}


def ingest_series(conn, name: str, provider: str, kind: str, cadence: str,
                  description: Optional[str] = None, full: bool = False,
                  fetch: Optional[Callable[[str, bool], List[Dict[str, Any]]]] = None,
                  ) -> Dict[str, Any]:
    """The pipeline: catalog upsert → fetch → value upsert → materialize.

    `fetch(provider, full)` is injectable for tests; the default dispatches on
    the provider string.
    """
    with create_span("macro.ingest.ingest_series", series=name,
                     provider=provider) as span:
        rows = (fetch or _fetch_rows)(provider, full)
        series_id = catalog.ensure_series(conn, name, provider=provider,
                                          kind=kind, cadence=cadence,
                                          description=description)
        n = upsert_values(conn, series_id, rows)
        summary = materialize_feature(conn, name)
        set_attributes(span, series_id=series_id, values_upserted=n)
        return {"series": name, "series_id": series_id, "provider": provider,
                "values_upserted": n, **summary}
