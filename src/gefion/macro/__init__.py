"""Macro series — the first non-stock entity (spec 007).

A catalog of named market-level time series (VIX, CPI, rates, …) and their raw
values. Rows are configuration, not schema (SC-207: the second series is an
INSERT, never DDL). Modules:

    catalog — macro_series catalog CRUD
    ingest  — provider fetch (AlphaVantage INDEX_DATA; FRED fallback), value
              upsert, and feature materialization into the store
"""
from gefion.observability import create_span, set_attributes  # noqa: F401
