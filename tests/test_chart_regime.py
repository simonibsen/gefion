"""Tests for the regime chart type (chart regime CLI + renderer + query + MCP).

TDD: written FIRST. The chart overlays a symbol's price line with colored
regime-episode bands (spec 005 visualization).
"""
import datetime as dt
import json
import os
import pathlib

import pytest
from typer.testing import CliRunner

from gefion.cli import app

REPO = pathlib.Path(__file__).parent.parent
runner = CliRunner()


# --- renderer (pure) -------------------------------------------------------

def _price(n=30):
    d0 = dt.date(2024, 1, 1)
    return [{"date": str(d0 + dt.timedelta(days=i)), "close": 100.0 + i} for i in range(n)]


def _episodes():
    return [
        {"label": "calm", "start": "2024-01-01", "end": "2024-01-10"},
        {"label": "stressed", "start": "2024-01-11", "end": "2024-01-20"},
        {"label": "normal", "start": "2024-01-21", "end": "2024-01-30"},
    ]


def test_renderer_produces_html_with_bands():
    from gefion.charts.d3.renderers import create_regime_chart
    html = create_regime_chart(_price(), _episodes(), regime_name="vol-regime", symbol="SPY")
    assert "<html" in html.lower()
    assert "vol-regime" in html and "SPY" in html
    for label in ("calm", "normal", "stressed"):
        assert label in html


def test_renderer_requires_data():
    from gefion.charts.d3.renderers import create_regime_chart
    with pytest.raises(ValueError):
        create_regime_chart([], _episodes(), regime_name="r", symbol="S")


# --- query (DB) -------------------------------------------------------------

@pytest.fixture
def conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled")
    import psycopg
    from gefion.db import schema
    try:
        c = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_labels")
        cur.execute("DELETE FROM regime_definitions")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_labels")
        cur.execute("DELETE FROM regime_definitions")
    c.close()


def test_fetch_regime_chart_data(conn):
    from gefion.charts.queries import fetch_regime_chart_data
    from gefion.regimes.definitions import RegimeDefinition, store_definition

    defn = RegimeDefinition(
        name="vol-regime", scope="market",
        expression={"leaf": "comparison", "feature": "realized_vol_20",
                    "cmp": "quantile", "value": "tercile", "scope": "market"},
        bucketing={"labels": ["calm", "normal", "stressed"]},
    )
    rid = store_definition(conn, defn)

    d0 = dt.date(2024, 1, 1)
    with conn.cursor() as cur:
        # symbol with prices
        cur.execute("INSERT INTO stocks (symbol) VALUES ('TEST') "
                    "ON CONFLICT (symbol) DO UPDATE SET symbol=EXCLUDED.symbol RETURNING id")
        sid = cur.fetchone()[0]
        for i in range(9):
            cur.execute(
                "INSERT INTO stock_ohlcv (data_id, date, close) VALUES (%s, %s, %s) "
                "ON CONFLICT (data_id, date) DO UPDATE SET close = EXCLUDED.close",
                (sid, d0 + dt.timedelta(days=i), 100.0 + i),
            )
        # labels: two episodes
        for i in range(9):
            label = "calm" if i < 5 else "stressed"
            cur.execute(
                "INSERT INTO regime_labels (regime_id, date, entity_id, label, dataset_version) "
                "VALUES (%s, %s, 0, %s, 'test')",
                (rid, d0 + dt.timedelta(days=i), label),
            )

    out = fetch_regime_chart_data(conn, "vol-regime", "TEST")
    assert len(out["price"]) == 9
    eps = out["episodes"]
    assert [e["label"] for e in eps] == ["calm", "stressed"]
    assert eps[0]["start"] == str(d0)
    assert out["regime"] == "vol-regime"


def test_fetch_unknown_regime_raises(conn):
    from gefion.charts.queries import fetch_regime_chart_data
    with pytest.raises(LookupError):
        fetch_regime_chart_data(conn, "no-such-regime", "TEST")


# --- CLI ---------------------------------------------------------------------

def test_chart_regime_in_help():
    r = runner.invoke(app, ["chart", "--help"])
    assert r.exit_code == 0
    assert "regime" in r.output


def test_chart_regime_requires_args():
    r = runner.invoke(app, ["chart", "regime"])
    assert r.exit_code != 0


# --- MCP ----------------------------------------------------------------------

def test_mcp_chart_regime_tool():
    src = (REPO / "mcp-server" / "server.py").read_text()
    assert 'name="chart_regime"' in src
    assert 'name == "chart_regime"' in src
    assert "async def _chart_regime(" in src
    assert '"chart", "regime"' in src


def test_chart_regime_end_to_end(conn, tmp_path, monkeypatch):
    """Full render path against the test DB — catches call-signature drift the
    --help tests cannot (found when the first production render failed)."""
    from gefion.db import schema as dbschema
    from gefion.regimes.definitions import RegimeDefinition, store_definition

    defn = RegimeDefinition(
        name="e2e-regime", scope="market",
        expression={"leaf": "comparison", "feature": "realized_vol_20",
                    "cmp": "quantile", "value": "tercile", "scope": "market"},
        bucketing={"labels": ["calm", "normal", "stressed"]},
    )
    rid = store_definition(conn, defn)
    d0 = dt.date(2024, 1, 1)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol) VALUES ('E2E') "
                    "ON CONFLICT (symbol) DO UPDATE SET symbol=EXCLUDED.symbol RETURNING id")
        sid = cur.fetchone()[0]
        for i in range(6):
            cur.execute("INSERT INTO stock_ohlcv (data_id, date, close) VALUES (%s,%s,%s) "
                        "ON CONFLICT (data_id, date) DO UPDATE SET close=EXCLUDED.close",
                        (sid, d0 + dt.timedelta(days=i), 100.0 + i))
            cur.execute("INSERT INTO regime_labels (regime_id, date, entity_id, label, dataset_version) "
                        "VALUES (%s,%s,0,'calm','test') ON CONFLICT DO NOTHING",
                        (rid, d0 + dt.timedelta(days=i)))
    monkeypatch.setenv("HOME", str(tmp_path))  # charts land under ~/.gefion/charts
    r = runner.invoke(app, ["chart", "regime", "e2e-regime", "--symbol", "E2E",
                            "--no-open", "--db-url", dbschema.test_db_url(), "--json"])
    assert r.exit_code == 0, r.output
    # CLI JSON mode emits JSONL (status lines + payload); the payload is last
    payload = json.loads([l for l in r.output.splitlines() if l.strip()][-1])
    payload = payload.get("data", payload)
    assert payload["episodes"] == 1 and payload["bars"] == 6
    assert pathlib.Path(payload["path"]).exists()
