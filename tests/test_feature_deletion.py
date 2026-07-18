"""Feature definition/function deletion door (#76 audit).

TDD: written FIRST. Dependency order: values → definition → (optionally)
function. A definition referenced by a regime expression refuses (labels
would become unrecomputable — archive/delete the regime first). Dataset
provenance (ml_datasets.feature_names) and the discovery ledger are soft
references — reported, never mutated. A function refuses while any
definition still routes to it. The market-candidates ledger
(promoted_function_id) is never touched — the audit survives the function.
"""
import os
from datetime import date

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
    schema.create_market_function_candidates_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'qfd_%')")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'qfd_%'")
        cur.execute("DELETE FROM ml_datasets WHERE name LIKE 'qfd_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'qfd_%'")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'qfd_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qfd_%'")
        cur.execute("DELETE FROM stocks WHERE symbol = 'QFD1'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _make_feature(conn, name="qfd_feat", values=2, function=True):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, name) VALUES ('QFD1', 'X') "
                    "ON CONFLICT (symbol) DO UPDATE SET name='X' RETURNING id")
        sid = cur.fetchone()[0]
        if function:
            cur.execute(
                """INSERT INTO feature_functions (name, version, status,
                       enabled, language, function_body)
                   VALUES (%s, 'v1', 'active', TRUE, 'python', '# x')
                   ON CONFLICT DO NOTHING""", (name,))
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name,
                   source_table, source_column, active)
               VALUES (%s, %s, 'stock_ohlcv', 'close', TRUE) RETURNING id""",
            (name, name))
        fid = cur.fetchone()[0]
        for i in range(values):
            cur.execute(
                "INSERT INTO computed_features (data_id, date, feature_id, "
                "value) VALUES (%s, %s, %s, 1.0)",
                (sid, date(2026, 1, 5 + i), fid))
    return fid


class TestDefinitionDelete:
    def test_plan_reports_blast_radius_changing_nothing(self, conn):
        from gefion.features import deletion
        _make_feature(conn, values=2)
        plan = deletion.plan_definition_delete(conn, "qfd_feat")
        assert plan["definition"]["name"] == "qfd_feat"
        assert plan["values"] == 2
        assert plan["regime_references"] == []
        assert plan["dataset_references"] == []
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM computed_features WHERE "
                        "feature_id = (SELECT id FROM feature_definitions "
                        "WHERE name='qfd_feat')")
            assert cur.fetchone()[0] == 2

    def test_execute_deletes_values_then_definition(self, conn):
        from gefion.features import deletion
        _make_feature(conn, values=2)
        result = deletion.execute_definition_delete(conn, "qfd_feat")
        assert result["values"] == 2
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM feature_definitions "
                        "WHERE name = 'qfd_feat'")
            assert cur.fetchone()[0] == 0
            # the FUNCTION survives a definition delete (separate door)
            cur.execute("SELECT count(*) FROM feature_functions "
                        "WHERE name = 'qfd_feat'")
            assert cur.fetchone()[0] == 1

    def test_regime_reference_refuses(self, conn):
        """A regime whose expression uses the feature would become
        unrecomputable — refuse, naming the regime."""
        from gefion.features import deletion
        _make_feature(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO regime_definitions (name, scope, expression,
                       bucketing, origin, status)
                   VALUES ('qfd_regime', 'market',
                           '{"leaf": "comparison", "feature": "qfd_feat"}'::jsonb,
                           '{}'::jsonb, 'human', 'active')""")
        with pytest.raises(ValueError, match="qfd_regime"):
            deletion.execute_definition_delete(conn, "qfd_feat")

    def test_dataset_provenance_is_soft_reported_not_blocking(self, conn):
        from gefion.features import deletion
        _make_feature(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ml_datasets (name, version, feature_names,
                       artifact_uri, lookback_days, horizons_days,
                       label_spec, split_spec)
                   VALUES ('qfd_ds', 'v1', ARRAY['qfd_feat'], 'memory://x',
                           365, ARRAY[30], '{}'::jsonb, '{}'::jsonb)""")
        plan = deletion.plan_definition_delete(conn, "qfd_feat")
        assert plan["dataset_references"] == ["qfd_ds:v1"]
        deletion.execute_definition_delete(conn, "qfd_feat")   # soft: proceeds
        with conn.cursor() as cur:
            cur.execute("SELECT feature_names FROM ml_datasets "
                        "WHERE name = 'qfd_ds'")
            assert cur.fetchone()[0] == ["qfd_feat"]   # provenance untouched

    def test_unknown_definition_refuses(self, conn):
        from gefion.features import deletion
        with pytest.raises(ValueError, match="qfd_missing"):
            deletion.plan_definition_delete(conn, "qfd_missing")


class TestFunctionDelete:
    def test_function_refuses_while_definitions_route_to_it(self, conn):
        from gefion.features import deletion
        _make_feature(conn)
        with pytest.raises(ValueError, match="qfd_feat"):
            deletion.execute_function_delete(conn, "qfd_feat")

    def test_function_deletes_when_unrouted_ledger_untouched(self, conn):
        from gefion.features import deletion
        from gefion.macro import candidates
        _make_feature(conn)
        # candidate ledger row pointing at the function (audit)
        cid = candidates.create_candidate(
            conn, name="qfd_feat", kind="cross_section",
            function_body="def compute(rows):\n    return 1.0",
            origin="template")
        deletion.execute_definition_delete(conn, "qfd_feat")
        result = deletion.execute_function_delete(conn, "qfd_feat")
        assert result["deleted"] is True
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM feature_functions "
                        "WHERE name = 'qfd_feat'")
            assert cur.fetchone()[0] == 0
        # audit ledger survives the function (no FK by design)
        assert candidates.get_candidate(conn, cid) is not None


class TestSurfaces:
    def test_cli_commands_exist(self):
        from typer.testing import CliRunner
        from gefion.cli import app
        r = CliRunner().invoke(app, ["feat-def-delete", "--help"])
        assert r.exit_code == 0 and "--confirm" in r.output
        r = CliRunner().invoke(app, ["feat-fx-delete", "--help"])
        assert r.exit_code == 0 and "--confirm" in r.output

    def test_mcp_tools_exist(self):
        from pathlib import Path
        import gefion
        server = (Path(gefion.__file__).parent.parent.parent /
                  "mcp-server" / "server.py").read_text()
        for tool in ("feature_definition_delete", "feature_function_delete"):
            assert f'name="{tool}"' in server
            assert f'name == "{tool}"' in server
