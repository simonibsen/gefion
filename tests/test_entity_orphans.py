"""Entity-integrity orphan scan tests (007, T006 — US4).

TDD: written FIRST. With the hard FK retired, an orphaned feature value (a
data_id with no home in its feature's DECLARED entity table) must be loudly
detectable — db-health gains an entity_integrity section in the
dimension-coverage style.

Fixture note: this runs BEFORE Migration B (safety ordering: detection ships
before the constraint drop), so manufactured orphans must stay FK-legal
against stocks. The construction: a disposable entity table in the TEST
database, a feature declaring it, and a computed row whose data_id exists in
stocks (FK satisfied) but not in the disposable table — an orphan relative to
the DECLARED table, which is the only definition that matters post-007.
"""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema
from gefion.entities import orphans


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
    with c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS orphtest_entities")
        cur.execute("CREATE TABLE orphtest_entities (id SERIAL PRIMARY KEY, name TEXT)")
        cur.execute("DELETE FROM stocks WHERE symbol = 'ORPHT1'")
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('ORPHT1', 'Anchor') RETURNING id")
        anchor_id = cur.fetchone()[0]
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'orphtest_%'")
        cur.execute(
            """INSERT INTO feature_definitions
                   (name, function_name, source_table, source_column, entity_table)
               VALUES ('orphtest_feature', 'indicator', 'orphtest_entities', 'name',
                       'orphtest_entities')
               RETURNING id""")
        feature_id = cur.fetchone()[0]
        # A stocks-declaring feature of our own: earlier suite modules may have
        # deleted the seeded definitions (shared test DB — issue #29 lesson),
        # so 'stocks' being declared must not be assumed, it must be arranged.
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name, entity_table)
               VALUES ('orphtest_stock_feature', 'indicator', 'stocks')""")
    yield c, anchor_id, feature_id
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id = %s", (feature_id,))
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'orphtest_%'")
        cur.execute("DELETE FROM stocks WHERE symbol = 'ORPHT1'")
        cur.execute("DROP TABLE IF EXISTS orphtest_entities")
    c.close()


def test_clean_database_reports_zero_orphans(conn):
    c, anchor_id, feature_id = conn
    report = orphans.scan(c)
    assert isinstance(report, dict)
    assert all(count == 0 for count in report.values()), report
    assert "stocks" in report  # every declared entity table is scanned


def test_manufactured_orphan_is_detected_with_table_and_count(conn):
    c, anchor_id, feature_id = conn
    with c.cursor() as cur:
        # data_id exists in stocks (FK-legal) but NOT in the declared table:
        # an orphan by the only definition that matters
        cur.execute(
            """INSERT INTO computed_features (data_id, date, feature_id, value)
               VALUES (%s, %s, %s, 1.0)""",
            (anchor_id, date(2026, 1, 5), feature_id),
        )
    report = orphans.scan(c)
    assert report["orphtest_entities"] == 1
    assert report["stocks"] == 0


def test_matching_entity_row_is_not_an_orphan(conn):
    c, anchor_id, feature_id = conn
    with c.cursor() as cur:
        # force the disposable table's id to align with the anchor so the
        # declared lookup succeeds
        cur.execute("INSERT INTO orphtest_entities (id, name) VALUES (%s, 'home')",
                    (anchor_id,))
        cur.execute(
            """INSERT INTO computed_features (data_id, date, feature_id, value)
               VALUES (%s, %s, %s, 1.0)""",
            (anchor_id, date(2026, 1, 6), feature_id),
        )
    report = orphans.scan(c)
    assert report["orphtest_entities"] == 0


def test_db_health_carries_entity_integrity_with_actionable_warning(conn):
    c, anchor_id, feature_id = conn
    with c.cursor() as cur:
        cur.execute(
            """INSERT INTO computed_features (data_id, date, feature_id, value)
               VALUES (%s, %s, %s, 1.0)""",
            (anchor_id, date(2026, 1, 7), feature_id),
        )
    import json
    from typer.testing import CliRunner
    from gefion.cli import app
    runner = CliRunner()
    result = runner.invoke(app, ["db-health", "--db-url", schema.test_db_url(), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["entity_integrity"]["orphtest_entities"] == 1
    warnings = " ".join(payload["warnings"])
    assert "orphan" in warnings.lower()
    assert "orphtest_entities" in warnings
