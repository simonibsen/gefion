"""Feature-management CLI (issue #89).

TDD: written FIRST. enable/disable doors for functions and definitions
(no more JSON-edit + reimport), validate/fix for orphaned definitions
(referencing missing or disabled functions; fix is dry-run by default per
the deletion-policy mold), and function status visible in feat-def-list.
"""
import os

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


def _cleanup(cur):
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'fmgmt_%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'fmgmt_%'")


@pytest.fixture
def conn():
    c = _conn()
    # canonical creators for every table touched — CI collection order differs
    schema.create_feature_definitions_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute(
            """INSERT INTO feature_functions (name, version, language,
               function_body) VALUES ('fmgmt_fn', 'v1', 'python', 'def f(): pass')""")
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name)
               VALUES ('fmgmt_ok', 'fmgmt_fn'),
                      ('fmgmt_orphan', 'fmgmt_missing_fn')""")
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _cli(args):
    from gefion.cli import app
    return CliRunner().invoke(app, args + ["--db-url", schema.test_db_url()])


def test_fx_disable_and_enable(conn):
    r = _cli(["feat-fx-disable", "fmgmt_fn"])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("SELECT enabled FROM feature_functions WHERE name='fmgmt_fn'")
        assert cur.fetchone()[0] is False
    r = _cli(["feat-fx-enable", "fmgmt_fn"])
    assert r.exit_code == 0
    with conn.cursor() as cur:
        cur.execute("SELECT enabled FROM feature_functions WHERE name='fmgmt_fn'")
        assert cur.fetchone()[0] is True


def test_fx_toggle_unknown_refuses(conn):
    r = _cli(["feat-fx-disable", "fmgmt_nope"])
    assert r.exit_code == 1
    assert "fmgmt_nope" in r.output


def test_def_disable_and_enable(conn):
    r = _cli(["feat-def-disable", "fmgmt_ok"])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("SELECT active FROM feature_definitions WHERE name='fmgmt_ok'")
        assert cur.fetchone()[0] is False
    r = _cli(["feat-def-enable", "fmgmt_ok"])
    assert r.exit_code == 0
    with conn.cursor() as cur:
        cur.execute("SELECT active FROM feature_definitions WHERE name='fmgmt_ok'")
        assert cur.fetchone()[0] is True


def test_validate_reports_orphans(conn):
    r = _cli(["feat-def-validate", "--json"])
    assert r.exit_code == 0, r.output
    import json
    payload = json.loads(r.output)
    orphan_names = [o["name"] for o in payload["orphans"]]
    assert "fmgmt_orphan" in orphan_names            # missing function
    assert "fmgmt_ok" not in orphan_names
    # a disabled function orphans its definitions too
    _cli(["feat-fx-disable", "fmgmt_fn"])
    payload = json.loads(_cli(["feat-def-validate", "--json"]).output)
    assert "fmgmt_ok" in [o["name"] for o in payload["orphans"]]


def test_fix_dry_run_by_default_then_confirm(conn):
    r = _cli(["feat-def-fix"])
    assert r.exit_code == 0, r.output
    assert "dry-run" in r.output.lower()
    with conn.cursor() as cur:                       # nothing changed
        cur.execute("SELECT active FROM feature_definitions WHERE name='fmgmt_orphan'")
        assert cur.fetchone()[0] is True
    r = _cli(["feat-def-fix", "--confirm"])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:                       # orphan deactivated, ok kept
        cur.execute("SELECT active FROM feature_definitions WHERE name='fmgmt_orphan'")
        assert cur.fetchone()[0] is False
        cur.execute("SELECT active FROM feature_definitions WHERE name='fmgmt_ok'")
        assert cur.fetchone()[0] is True


def test_def_list_shows_function_status(conn):
    import json
    payload = json.loads(_cli(["feat-def-list", "--json"]).output)
    by_name = {f["name"]: f for f in payload["features"]}
    assert by_name["fmgmt_ok"]["function_status"] == "active"
    assert by_name["fmgmt_orphan"]["function_status"] == "missing"
    _cli(["feat-fx-disable", "fmgmt_fn"])
    payload = json.loads(_cli(["feat-def-list", "--json"]).output)
    by_name = {f["name"]: f for f in payload["features"]}
    assert by_name["fmgmt_ok"]["function_status"] == "disabled"


def test_mcp_tools_exist():
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    for tool in ("feature_function_toggle", "feature_definition_toggle",
                 "feature_definitions_validate"):
        assert f'name="{tool}"' in server
