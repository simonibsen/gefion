"""Universe CLI surface (015, US2/US3 + polish).

TDD: written FIRST. Full `gefion universe` command group with --json
everywhere; define consumes a YAML rules file; refresh prints the delta and
honors the FR-010 guard; explain answers SC-003 in one command; db-health
carries the universe headline.
"""
import json
import os
from datetime import date

import psycopg
import pytest
from typer.testing import CliRunner

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


def _cleanup(c):
    with c.cursor() as cur:
        cur.execute("DELETE FROM universe_definitions WHERE name LIKE 'qcl_%'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QCL_%'")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    _cleanup(c)
    with c.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol, status, industry, asset_type) "
                    "VALUES ('QCL_SPAC', 'Active', 'SHELL COMPANIES', 'Stock')")
        cur.execute("INSERT INTO stocks (symbol, status, industry, asset_type) "
                    "VALUES ('QCL_OK', 'Active', 'BIOTECHNOLOGY', 'Stock')")
    yield c
    _cleanup(c)
    c.close()


def _invoke(*args):
    """CliRunner + --db-url for test-DB isolation (house idiom, matches
    test_market_candidates_cli). --help invocations skip the flag."""
    from gefion.cli import app
    args = list(args)
    if "--help" not in args:
        args += ["--db-url", schema.test_db_url()]
    return CliRunner().invoke(app, args)


RULES_YAML = """\
- name: no-shells
  attribute: industry
  op: eq
  value: SHELL COMPANIES
  reason: cash boxes
"""

BAD_RULES_YAML = """\
- name: bad
  attribute: favorite_color
  op: eq
  value: blue
  reason: nope
"""


class TestDefineAndShow:
    def test_define_from_rules_file_and_show(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        r = _invoke("universe", "define", "qcl_main",
                    "--rules-file", str(rules), "--json")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["fingerprint"].startswith("sha256:")
        r = _invoke("universe", "show", "qcl_main", "--json")
        assert r.exit_code == 0, r.output
        shown = json.loads(r.output)
        assert shown["rules"][0]["name"] == "no-shells"
        assert shown["rules"][0]["reason"] == "cash boxes"

    def test_define_refusal_names_valid_attributes(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(BAD_RULES_YAML)
        r = _invoke("universe", "define", "qcl_bad",
                    "--rules-file", str(rules))
        assert r.exit_code != 0
        assert "industry" in r.output      # refusal lists valid attributes

    def test_list_shows_universes(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_lst", "--rules-file", str(rules))
        r = _invoke("universe", "list", "--json")
        assert r.exit_code == 0, r.output
        names = [u["name"] for u in json.loads(r.output)["universes"]]
        assert "qcl_lst" in names


class TestRefreshAndMembers:
    def test_refresh_reports_delta_and_members_asof(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_ref", "--rules-file", str(rules))
        r = _invoke("universe", "refresh", "qcl_ref", "--json")
        assert r.exit_code == 0, r.output
        delta = json.loads(r.output)
        assert delta["added"] >= 1 and "members" in delta
        r = _invoke("universe", "members", "qcl_ref", "--json")
        members = json.loads(r.output)["members"]
        assert "QCL_OK" in members and "QCL_SPAC" not in members
        # as-of is accepted (static rules: excluded across all time)
        r = _invoke("universe", "members", "qcl_ref",
                    "--as-of", "2015-06-30", "--json")
        assert r.exit_code == 0, r.output
        assert "QCL_SPAC" not in json.loads(r.output)["members"]

    def test_explain(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_exp", "--rules-file", str(rules))
        _invoke("universe", "refresh", "qcl_exp")
        r = _invoke("universe", "explain", "QCL_SPAC",
                    "--universe", "qcl_exp", "--json")
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["member"] is False
        assert out["excluded_by"][0]["rule"] == "no-shells"

    def test_enable_disable(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_tog", "--rules-file", str(rules))
        assert _invoke("universe", "disable", "qcl_tog").exit_code == 0
        r = _invoke("universe", "members", "qcl_tog", "--json")
        assert r.exit_code != 0                # disabled → refusal
        assert _invoke("universe", "enable", "qcl_tog").exit_code == 0


class TestHelpSurface:
    def test_all_commands_exist(self):
        for verb in ("define", "list", "show", "members", "explain",
                     "refresh", "enable", "disable", "export", "import",
                     "delete"):
            r = _invoke("universe", verb, "--help")
            assert r.exit_code == 0, f"missing: universe {verb}"

    def test_consumer_universe_flags(self):
        r = _invoke("ml", "dataset-build", "--help")
        assert "--universe" in r.output
        r = _invoke("backtest", "run", "--help")
        assert "--universe" in r.output


class TestExportImport:
    def test_yaml_round_trip(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_rt", "--rules-file", str(rules))
        out_file = tmp_path / "universes.yaml"
        r = _invoke("universe", "export", "-o", str(out_file))
        assert r.exit_code == 0, r.output
        assert "qcl_rt" in out_file.read_text()
        # dry-run reports unchanged; delete + import restores
        r = _invoke("universe", "import", str(out_file), "--dry-run", "--json")
        assert "qcl_rt" in json.loads(r.output)["unchanged"]
        with conn.cursor() as cur:
            cur.execute("DELETE FROM universe_definitions WHERE name = 'qcl_rt'")
        r = _invoke("universe", "import", str(out_file), "--json")
        assert r.exit_code == 0, r.output
        assert "qcl_rt" in json.loads(r.output)["created"]

    def test_import_validates_before_writing(self, conn, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("universes:\n- name: qcl_badimp\n  rules:\n"
                       "  - name: r\n    attribute: nope\n    op: eq\n"
                       "    value: x\n    reason: r\n")
        r = _invoke("universe", "import", str(bad), "--json")
        assert r.exit_code != 0
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM universe_definitions "
                        "WHERE name = 'qcl_badimp'")
            assert cur.fetchone() is None    # nothing written


class TestDeletionDoor:
    def test_dry_run_default_and_confirm(self, conn, tmp_path):
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_del", "--rules-file", str(rules))
        _invoke("universe", "refresh", "qcl_del")
        r = _invoke("universe", "delete", "qcl_del", "--json")   # dry-run
        assert r.exit_code == 0, r.output
        plan = json.loads(r.output)
        assert plan["deletable"] is True
        with conn.cursor() as cur:   # dry-run changed nothing
            cur.execute("SELECT 1 FROM universe_definitions "
                        "WHERE name = 'qcl_del'")
            assert cur.fetchone() is not None
        r = _invoke("universe", "delete", "qcl_del", "--confirm", "--json")
        assert r.exit_code == 0, r.output
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM universe_definitions "
                        "WHERE name = 'qcl_del'")
            assert cur.fetchone() is None

    def test_referenced_universe_refuses(self, conn, tmp_path):
        from gefion.db import schema as dbschema
        dbschema.create_ml_datasets_table(conn)
        rules = tmp_path / "rules.yaml"
        rules.write_text(RULES_YAML)
        _invoke("universe", "define", "qcl_ref2", "--rules-file", str(rules))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ml_datasets (name, version, universe, "
                "feature_names, lookback_days, horizons_days, label_spec, "
                "split_spec, artifact_uri) VALUES ('qcl_ds', 'v1', "
                "'{\"universe_name\": \"qcl_ref2\"}', '{}', 200, '{7}', "
                "'{}', '{}', '/tmp/x') ON CONFLICT DO NOTHING")
        try:
            r = _invoke("universe", "delete", "qcl_ref2", "--confirm",
                        "--json")
            assert r.exit_code != 0
            assert "provenance" in r.output
            with conn.cursor() as cur:   # refusal changed nothing
                cur.execute("SELECT 1 FROM universe_definitions "
                            "WHERE name = 'qcl_ref2'")
                assert cur.fetchone() is not None
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ml_datasets WHERE name = 'qcl_ds'")

    def test_default_universe_refuses_delete(self, conn):
        from gefion.universe.definitions import seed_default_universe
        seed_default_universe(conn)
        r = _invoke("universe", "delete", "modeling_default", "--confirm",
                    "--json")
        assert r.exit_code != 0
        assert "DEFAULT" in r.output or "default" in r.output


class TestDbHealthHeadline:
    def test_db_health_reports_universe(self, conn):
        from gefion.universe.definitions import seed_default_universe
        seed_default_universe(conn)
        r = _invoke("db-health", "--json")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert "universe" in payload
        assert payload["universe"]["name"] == "modeling_default"
        assert "members" in payload["universe"]

    def test_db_health_after_refresh_reports_timestamp(self, conn):
        """The staleness branch only executes once a refresh has happened —
        prod hit a NameError here that the unrefreshed test never reached.
        Must report last_refresh, not fall back to the missing-universe
        warning."""
        from gefion.universe.definitions import seed_default_universe
        from gefion.universe.membership import refresh_universe
        seed_default_universe(conn)
        refresh_universe(conn, "modeling_default")
        r = _invoke("db-health", "--json")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["universe"] is not None, payload.get("warnings")
        assert payload["universe"]["last_refresh"] is not None
        assert not any("no default modeling universe" in w
                       for w in payload.get("warnings", []))
