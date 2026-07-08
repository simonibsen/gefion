"""Macro-series ingestion tests (007, T017 — US2).

TDD: written FIRST. The macro home end-to-end: provider parsers (value-only
FRED CSV and OHLC INDEX_DATA), catalog upsert (rows are configuration),
idempotent value upserts, and feature materialization — `macro_<name>` with
`entity_table='macro_series'` landing in computed_features so discovery and
regimes consume it with zero equity-pipeline changes.

The family test (SC-207) is the point of the design: a second series of a
different shape (monthly, value-only) lands via a catalog row + ingest +
feature definition — zero DDL.

Provider note (T016, research.md): AlphaVantage INDEX_DATA returned
not-entitled on the production key, so the default provider is `fred:VIXCLS`
(keyless CSV). The INDEX_DATA parser is still implemented and tested — the
pivot back is a config change.
"""
import datetime
import os

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()

    def _cleanup(cur):
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_mactest%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_mactest%'")
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name = 'mactest_stockfeat')")
        cur.execute("DELETE FROM feature_definitions WHERE name = 'mactest_stockfeat'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'mactest%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


# --- parsers (pure, no DB) ------------------------------------------------------

def test_parse_fred_csv_value_only_skips_missing():
    from gefion.macro import ingest
    text = ("observation_date,VIXCLS\n"
            "1990-01-02,17.24\n"
            "1990-01-03,.\n"          # FRED's missing-value marker
            "1990-01-04,19.22\n")
    rows = ingest.parse_fred_csv(text)
    assert rows == [
        {"date": datetime.date(1990, 1, 2), "value": 17.24},
        {"date": datetime.date(1990, 1, 4), "value": 19.22},
    ]


def test_parse_index_data_ohlc_to_value():
    """INDEX_DATA is daily OHLC; the canonical value is the close."""
    from gefion.alphavantage.catalog import parse_index_data
    payload = {
        "Time Series (Daily)": {
            "2026-01-05": {"1. open": "15.1", "2. high": "16.4",
                           "3. low": "14.9", "4. close": "15.8"},
            "2026-01-06": {"1. open": "bad", "2. high": "1", "3. low": "1",
                           "4. close": "x"},  # skipped, not fatal
        }
    }
    rows = parse_index_data(payload)
    assert rows == [{
        "date": datetime.date(2026, 1, 5), "value": 15.8,
        "open": 15.1, "high": 16.4, "low": 14.9,
    }]


# --- catalog: rows are configuration ---------------------------------------------

def test_catalog_ensure_series_upserts_by_name(conn):
    from gefion.macro import catalog
    sid = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS",
                                kind="index", cadence="daily")
    again = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS2",
                                  kind="index", cadence="daily")
    assert sid == again  # same row, updated in place
    row = catalog.get_series(conn, "mactest_vix")
    assert row["provider"] == "fred:VIXCLS2"


def test_upsert_values_idempotent(conn):
    from gefion.macro import catalog, ingest
    sid = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS",
                                kind="index", cadence="daily")
    rows = [
        {"date": datetime.date(2026, 1, 5), "value": 15.8,
         "open": 15.1, "high": 16.4, "low": 14.9},
        {"date": datetime.date(2026, 1, 6), "value": 16.2},
    ]
    assert ingest.upsert_values(conn, sid, rows) == 2
    rows[1]["value"] = 16.5  # re-ingest corrects in place
    assert ingest.upsert_values(conn, sid, rows) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(value) FROM macro_series_values "
                    "WHERE series_id = %s", (sid,))
        count, mx = cur.fetchone()
    assert count == 2
    assert float(mx) == 16.5


# --- materialization: the feature-store bridge -----------------------------------

def test_materialize_feature_declares_the_entity_axis(conn):
    from gefion.macro import catalog, ingest
    sid = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS",
                                kind="index", cadence="daily")
    ingest.upsert_values(conn, sid, [
        {"date": datetime.date(2026, 1, 5), "value": 15.8},
        {"date": datetime.date(2026, 1, 6), "value": 16.2},
    ])
    summary = ingest.materialize_feature(conn, "mactest_vix")
    assert summary["feature"] == "macro_mactest_vix"
    assert summary["values"] == 2
    with conn.cursor() as cur:
        cur.execute(
            """SELECT entity_table, source_table, source_column
               FROM feature_definitions WHERE name = 'macro_mactest_vix'""")
        assert cur.fetchone() == ("macro_series", "macro_series_values", "value")
        cur.execute(
            """SELECT count(*) FROM computed_features cf
               JOIN feature_definitions fd ON fd.id = cf.feature_id
               WHERE fd.name = 'macro_mactest_vix' AND cf.data_id = %s""", (sid,))
        assert cur.fetchone()[0] == 2
    # idempotent: re-materializing neither duplicates nor errors
    again = ingest.materialize_feature(conn, "mactest_vix")
    assert again["values"] == 2


def test_family_second_series_lands_with_zero_ddl(conn):
    """SC-207: a monthly value-only series is a catalog row + values + feature
    definition — no DDL, and the discovery loader serves it untouched."""
    from gefion.macro import catalog, ingest
    from gefion.regimes.discovery import signals
    sid = catalog.ensure_series(conn, "mactest_cpi", provider="fred:CPIAUCSL",
                                kind="rate", cadence="monthly")
    ingest.upsert_values(conn, sid, [
        {"date": datetime.date(2026, m, 1), "value": 300.0 + m} for m in (1, 2, 3)
    ])
    ingest.materialize_feature(conn, "mactest_cpi")
    with conn.cursor() as cur:
        series = signals._feature_series(cur, "macro_mactest_cpi", ["ANY", "UNIVERSE"])
    assert [v for _, v in series] == [301.0, 302.0, 303.0]


# --- the pipeline + honest refusals -----------------------------------------------

def test_ingest_series_end_to_end_with_injected_fetch(conn):
    from gefion.macro import ingest
    rows = [{"date": datetime.date(2026, 1, 5), "value": 15.8}]
    summary = ingest.ingest_series(
        conn, "mactest_vix", provider="fred:VIXCLS", kind="index",
        cadence="daily", fetch=lambda provider, full: rows)
    assert summary["series"] == "mactest_vix"
    assert summary["values_upserted"] == 1
    assert summary["feature"] == "macro_mactest_vix"


def test_unknown_provider_refused_honestly(conn):
    from gefion.macro import ingest
    with pytest.raises(ingest.MacroIngestError) as exc:
        ingest.ingest_series(conn, "mactest_vix", provider="bloomberg:VIX",
                             kind="index", cadence="daily")
    assert "fred:" in str(exc.value)  # the refusal names what IS supported


# --- CLI + MCP surfaces (T019 interface assertions) --------------------------------

def test_cli_macro_list_reports_coverage(conn):
    import json
    from typer.testing import CliRunner
    from gefion.cli import app
    from gefion.macro import catalog, ingest
    sid = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS",
                                kind="index", cadence="daily")
    ingest.upsert_values(conn, sid, [
        {"date": datetime.date(2026, 1, 5), "value": 15.8},
        {"date": datetime.date(2026, 1, 6), "value": 16.2},
    ])
    runner = CliRunner()
    result = runner.invoke(app, ["macro", "list",
                                 "--db-url", schema.test_db_url(), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    data = payload.get("data", payload)
    mine = [s for s in data["series"] if s["name"] == "mactest_vix"]
    assert mine and mine[0]["values"] == 2
    assert mine[0]["first_date"] == "2026-01-05"
    assert mine[0]["last_date"] == "2026-01-06"
    assert mine[0]["materialized"] is False


def test_cli_macro_ingest_interface():
    from typer.testing import CliRunner
    from gefion.cli import app
    result = CliRunner().invoke(app, ["macro", "ingest", "--help"])
    assert result.exit_code == 0
    for opt in ("--name", "--provider", "--kind", "--cadence", "--full"):
        assert opt in result.output


def test_mcp_surface_exists():
    """T019: macro_ingest (mutating) and macro_list wrap the CLI."""
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server" / "server.py").read_text()
    for tool in ("macro_ingest", "macro_list"):
        assert f'name="{tool}"' in server
        assert f'name == "{tool}"' in server
    assert "async def _macro_ingest(" in server
    assert "async def _macro_list(" in server


# --- consumption proof (T020, SC-202/203 test-level) --------------------------------

@pytest.fixture
def consumption_world(conn):
    """A small DB world: 3 stocks with OHLCV + a feature, and a macro series
    materialized as macro_mactest_vix — enough history for a discovery run."""
    import numpy as np
    from gefion.macro import catalog, ingest
    rng = np.random.default_rng(7)
    n_days = 400
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MACC%'")
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('MACC1', 'A', 'Common Stock'), ('MACC2', 'B', 'Common Stock'),
               ('MACC3', 'C', 'Common Stock') RETURNING id""")
        stock_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name, entity_table) "
            "VALUES ('mactest_stockfeat', 'indicator', 'stocks') RETURNING id")
        stock_feat = cur.fetchone()[0]
        base = datetime.date(2024, 1, 1)
        vix_rows = []
        for i in range(n_days):
            d = base + datetime.timedelta(days=i)
            for j, sid in enumerate(stock_ids):
                close = 100.0 * (1 + 0.001 * j) + float(rng.normal(0, 1))
                cur.execute(
                    """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, 1000) ON CONFLICT DO NOTHING""",
                    (sid, d, close, close, close, close))
                cur.execute(
                    """INSERT INTO computed_features (data_id, date, feature_id, value)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sid, d, stock_feat, float(rng.normal(0, 1))))
            vix_rows.append({"date": d, "value": 15.0 + float(rng.normal(0, 3))})
    sid = catalog.ensure_series(conn, "mactest_vix", provider="fred:VIXCLS",
                                kind="index", cadence="daily")
    ingest.upsert_values(conn, sid, vix_rows)
    ingest.materialize_feature(conn, "mactest_vix")
    yield conn
    with conn.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions "
                    " WHERE name = 'mactest_stockfeat')")
        cur.execute("DELETE FROM feature_definitions "
                    "WHERE name = 'mactest_stockfeat'")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'MACC%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MACC%'")


def test_discovery_atom_over_macro_feature_evaluates(consumption_world):
    """SC-202/203: a {'feature': 'macro_mactest_vix', 'form': 'tercile'} atom
    enumerates and evaluates against DB-loaded market data, and the run
    records ZERO uncomputable-proposal diagnostics."""
    from gefion.regimes.discovery import ledger, runner, signals
    conn = consumption_world
    market = signals.load_market_data(
        conn, ["mactest_stockfeat", "macro_mactest_vix"])
    config = runner.DiscoveryConfig(
        name="mactest-consumption", seed=7,
        atoms=[{"feature": "macro_mactest_vix", "form": "tercile"}],
        signals=["mactest_stockfeat"],
        depth=1, budget=5, tiers=("interaction",),
        holdout_weeks=13, universe_filter="passthrough")
    summary = runner.run_discovery(conn, config, market)
    cands = ledger.list_candidates(conn, summary["run_id"])
    assert len(cands) >= 1  # the VIX atom was enumerated and evaluated
    diags = ledger.list_diagnostics(conn, summary["run_id"])
    assert [d for d in diags if d["kind"] == "uncomputable_proposal"] == []


def test_regime_interaction_by_macro_feature_answers(consumption_world):
    """`regime interaction --by macro_mactest_vix` has a data path."""
    from gefion.regimes.interaction import (continuous_interaction,
                                            load_market_interaction_data)
    conn = consumption_world
    s, c, r = load_market_interaction_data(
        conn, "mactest_stockfeat", "macro_mactest_vix", horizon_days=1)
    result = continuous_interaction(s, c, r)
    assert result["n"] >= 300
    assert result["interaction_pvalue"] is not None
