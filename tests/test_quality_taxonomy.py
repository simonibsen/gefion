"""Sector/industry taxonomy normalization tests (issue #86 follow-on).

TDD: written FIRST. The prod census (2026-07-13) exposed taxonomy warts in
stocks.sector: provider sentinels stored as literal group names ('NONE' 94
stocks, 'OTHER' 41) and a vendor-taxonomy split ('FINANCIALS' 5 and
'CAPITAL MARKETS' 1 vs 'FINANCIAL SERVICES' 984). Sentinels must become
NULL (sector series and regime labels already exclude NULL); known aliases
must map to the canonical sector; everything else is trimmed + uppercased
so future case variance cannot split a group.

Two surfaces under test:
- normalize at the ingestion chokepoints (parse_overview, fundamentals write)
- `gefion quality normalize-taxonomy` backfill for already-stored rows
  (dry-run by default; --apply mutates stocks.sector/industry)
"""
import os

import psycopg
import pytest

from gefion.db import schema


# --- pure normalization ------------------------------------------------------

def test_normalize_sector_sentinels_become_none():
    from gefion.quality.taxonomy import normalize_sector
    for raw in ("NONE", "None", "none", "OTHER", "Other", "N/A", "-", "",
                "  ", None):
        assert normalize_sector(raw) is None, raw


def test_normalize_sector_aliases_map_to_canonical():
    from gefion.quality.taxonomy import normalize_sector
    assert normalize_sector("FINANCIALS") == "FINANCIAL SERVICES"
    assert normalize_sector("Financials") == "FINANCIAL SERVICES"
    assert normalize_sector(" FINANCIALS ") == "FINANCIAL SERVICES"
    assert normalize_sector("CAPITAL MARKETS") == "FINANCIAL SERVICES"


def test_normalize_sector_passthrough_is_trimmed_uppercase():
    from gefion.quality.taxonomy import normalize_sector
    assert normalize_sector("TECHNOLOGY") == "TECHNOLOGY"
    assert normalize_sector("Technology") == "TECHNOLOGY"
    assert normalize_sector(" Financial Services ") == "FINANCIAL SERVICES"
    assert normalize_sector("CONSUMER CYCLICAL") == "CONSUMER CYCLICAL"


def test_normalize_industry_sentinels_and_passthrough():
    from gefion.quality.taxonomy import normalize_industry
    for raw in ("NONE", "None", "OTHER", "N/A", "-", "", None):
        assert normalize_industry(raw) is None, raw
    assert normalize_industry("Software") == "SOFTWARE"
    assert normalize_industry(" BANKING SERVICES ") == "BANKING SERVICES"
    # no alias map for industry — vendor split there is out of scope
    assert normalize_industry("FINANCIAL") == "FINANCIAL"


# --- ingestion chokepoint: parse_overview -------------------------------------

def test_parse_overview_normalizes_taxonomy():
    from gefion.alphavantage.catalog import parse_overview
    parsed = parse_overview({"Symbol": "X", "Sector": "None",
                             "Industry": "None"})
    assert parsed["sector"] is None
    assert parsed["industry"] is None

    parsed = parse_overview({"Symbol": "X", "Sector": "Financials",
                             "Industry": "Banking Services"})
    assert parsed["sector"] == "FINANCIAL SERVICES"
    assert parsed["industry"] == "BANKING SERVICES"


# --- DB fixtures ---------------------------------------------------------------

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
    schema.create_stocks_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM stocks_fundamentals WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QTX%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QTX%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _seed(cur, symbol, sector, industry):
    cur.execute(
        "INSERT INTO stocks (symbol, name, sector, industry) "
        "VALUES (%s, 'X', %s, %s) RETURNING id",
        (symbol, sector, industry))
    return cur.fetchone()[0]


def _taxonomy(cur, sid):
    cur.execute("SELECT sector, industry FROM stocks WHERE id = %s", (sid,))
    return cur.fetchone()


# --- ingestion chokepoint: fundamentals write ----------------------------------

def test_write_fundamentals_normalizes_taxonomy(conn):
    from gefion.cli import _write_fundamentals_results
    with conn.cursor() as cur:
        s1 = _seed(cur, "QTX1", None, None)
        s2 = _seed(cur, "QTX2", None, None)
    results = [
        (s1, "QTX1", {"Symbol": "QTX1", "Name": "X", "Sector": "None",
                      "Industry": "None", "Exchange": "NASDAQ"}, None, False),
        (s2, "QTX2", {"Symbol": "QTX2", "Name": "X", "Sector": "FINANCIALS",
                      "Industry": "Banking Services", "Exchange": "NASDAQ"},
         None, False),
    ]
    summary = _write_fundamentals_results(conn, results)
    assert summary["updated"] == 2
    with conn.cursor() as cur:
        assert _taxonomy(cur, s1) == (None, None)
        assert _taxonomy(cur, s2) == ("FINANCIAL SERVICES", "BANKING SERVICES")


# --- backfill over already-stored rows ------------------------------------------

def _seed_warts(cur):
    return {
        "sentinel": _seed(cur, "QTX3", "NONE", "NONE"),
        "other": _seed(cur, "QTX4", "OTHER", "OTHER"),
        "alias": _seed(cur, "QTX5", "FINANCIALS", "BANKING SERVICES"),
        "capmkt": _seed(cur, "QTX6", "CAPITAL MARKETS", "FINANCIAL"),
        "clean": _seed(cur, "QTX7", "TECHNOLOGY", "SOFTWARE"),
        "null": _seed(cur, "QTX8", None, None),
    }


def test_backfill_dry_run_reports_but_changes_nothing(conn):
    from gefion.quality import taxonomy
    with conn.cursor() as cur:
        ids = _seed_warts(cur)
    summary = taxonomy.backfill(conn, apply=False)
    assert summary["applied"] is False
    assert summary["rows_changed"] >= 4  # the four wart rows above
    # dry-run reports the mapping breakdown
    changes = {(c["column"], c["from"], c["to"]): c["count"]
               for c in summary["changes"]}
    assert changes[("sector", "NONE", None)] >= 1
    assert changes[("sector", "OTHER", None)] >= 1
    assert changes[("sector", "FINANCIALS", "FINANCIAL SERVICES")] >= 1
    assert changes[("sector", "CAPITAL MARKETS", "FINANCIAL SERVICES")] >= 1
    with conn.cursor() as cur:  # nothing stored changed
        assert _taxonomy(cur, ids["sentinel"]) == ("NONE", "NONE")
        assert _taxonomy(cur, ids["alias"]) == ("FINANCIALS", "BANKING SERVICES")


def test_backfill_apply_fixes_rows_and_is_idempotent(conn):
    from gefion.quality import taxonomy
    with conn.cursor() as cur:
        ids = _seed_warts(cur)
    summary = taxonomy.backfill(conn, apply=True)
    assert summary["applied"] is True
    assert summary["rows_changed"] >= 4
    with conn.cursor() as cur:
        assert _taxonomy(cur, ids["sentinel"]) == (None, None)
        assert _taxonomy(cur, ids["other"]) == (None, None)
        assert _taxonomy(cur, ids["alias"]) == ("FINANCIAL SERVICES",
                                                "BANKING SERVICES")
        assert _taxonomy(cur, ids["capmkt"]) == ("FINANCIAL SERVICES",
                                                 "FINANCIAL")
        assert _taxonomy(cur, ids["clean"]) == ("TECHNOLOGY", "SOFTWARE")
        assert _taxonomy(cur, ids["null"]) == (None, None)
    # second run finds nothing left to change
    again = taxonomy.backfill(conn, apply=True)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stocks WHERE symbol LIKE 'QTX%' "
                    "AND (sector IN ('NONE', 'OTHER', 'FINANCIALS', "
                    "'CAPITAL MARKETS') OR industry IN ('NONE', 'OTHER'))")
        assert cur.fetchone()[0] == 0
    assert all(c["count"] == 0 or not _touches_qtx(conn, c)
               for c in again["changes"]) or again["rows_changed"] == 0


# --- CLI + MCP surfaces ----------------------------------------------------------

def test_cli_normalize_taxonomy_dry_run_then_apply(conn):
    from typer.testing import CliRunner

    from gefion.cli import app
    with conn.cursor() as cur:
        ids = _seed_warts(cur)
    runner = CliRunner()
    dry = runner.invoke(app, ["quality", "normalize-taxonomy",
                              "--db-url", schema.test_db_url(), "--json"])
    assert dry.exit_code == 0, dry.output
    with conn.cursor() as cur:  # dry-run by default: nothing changed
        assert _taxonomy(cur, ids["sentinel"]) == ("NONE", "NONE")
    applied = runner.invoke(app, ["quality", "normalize-taxonomy", "--apply",
                                  "--db-url", schema.test_db_url(), "--json"])
    assert applied.exit_code == 0, applied.output
    with conn.cursor() as cur:
        assert _taxonomy(cur, ids["sentinel"]) == (None, None)
        assert _taxonomy(cur, ids["alias"]) == ("FINANCIAL SERVICES",
                                                "BANKING SERVICES")


def test_mcp_normalize_taxonomy_surface_exists():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    assert 'name="quality_normalize_taxonomy"' in server
    assert 'name == "quality_normalize_taxonomy"' in server


def _touches_qtx(conn, change):
    """Idempotence guard: a second apply may still report rows outside this
    test's QTX universe (a shared dev DB); only QTX rows must be settled."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM stocks WHERE symbol LIKE 'QTX%' AND "
            f"{change['column']} = %s", (change["from"],))
        return cur.fetchone()[0] > 0
