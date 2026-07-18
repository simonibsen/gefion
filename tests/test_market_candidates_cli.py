"""CLI tests for the candidate gate + composite registration (014 T009/T019).

Uses CliRunner + --db-url for test-DB isolation (matches test_regime_cli).
"""
import json
import os

import pytest
from typer.testing import CliRunner

from gefion.cli import app

runner = CliRunner()


@pytest.fixture
def db_url():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled")
    import psycopg
    from gefion.db.schema import test_db_url, create_market_function_candidates_table
    url = test_db_url()
    with psycopg.connect(url, autocommit=True) as c:
        create_market_function_candidates_table(c)
        with c.cursor() as cur:
            cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'cli_mfc_%'")
            cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_cli_mfc_%'")
            cur.execute("DELETE FROM feature_functions WHERE name LIKE 'cli_mfc_%'")
    return url


def _seed_candidate(db_url, name="cli_mfc_one", ok=True):
    import psycopg
    from gefion.macro import candidates
    with psycopg.connect(db_url, autocommit=True) as c:
        cid = candidates.create_candidate(
            c, name=name, kind="cross_section",
            function_body="def compute(rows):\n    return float(len(rows))",
            origin="template", principle_id="p-cli", generator="test")
        candidates.record_dry_run(c, cid, {
            "ok": ok, "sample": [{"date": "2026-01-02", "value": 50.0}],
            "error": None if ok else "sandbox refusal",
            "seed": 42, "ran_at": "2026-07-18T00:00:00"})
    return cid


def test_candidate_list_shows_pending_queue(db_url):
    cid = _seed_candidate(db_url)
    r = runner.invoke(app, ["macro", "candidate", "list", "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    names = [c["name"] for c in payload["candidates"]]
    assert "cli_mfc_one" in names


def test_candidate_show_is_the_review_packet(db_url):
    cid = _seed_candidate(db_url)
    r = runner.invoke(app, ["macro", "candidate", "show", "--id", str(cid),
                            "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    c = json.loads(r.output)["candidate"]
    # one place: body, inputs, provenance, dry-run
    assert "def compute(rows" in c["function_body"]
    assert c["origin"] == "template" and c["principle_id"] == "p-cli"
    assert c["dry_run"]["ok"] is True


def test_candidate_approve_promotes(db_url):
    cid = _seed_candidate(db_url, name="cli_mfc_approve")
    r = runner.invoke(app, ["macro", "candidate", "approve", "--id", str(cid),
                            "--approver", "simon", "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    import psycopg
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM feature_functions "
                        "WHERE name = 'cli_mfc_approve' AND scope = 'market'")
            assert cur.fetchone()[0] == 1


def test_candidate_approve_refuses_failed_dry_run(db_url):
    cid = _seed_candidate(db_url, name="cli_mfc_bad", ok=False)
    r = runner.invoke(app, ["macro", "candidate", "approve", "--id", str(cid),
                            "--approver", "simon", "--db-url", db_url])
    assert r.exit_code != 0
    assert "dry-run" in r.output


def test_candidate_reject_requires_reason(db_url):
    cid = _seed_candidate(db_url, name="cli_mfc_rej")
    r = runner.invoke(app, ["macro", "candidate", "reject", "--id", str(cid),
                            "--db-url", db_url])
    assert r.exit_code != 0     # --reason is required
    r = runner.invoke(app, ["macro", "candidate", "reject", "--id", str(cid),
                            "--reason", "dupe", "--db-url", db_url, "--json"])
    assert r.exit_code == 0


def test_derive_refusal_names_the_gate(db_url):
    _seed_candidate(db_url, name="cli_mfc_locked")
    r = runner.invoke(app, ["macro", "derive", "--series", "cli_mfc_locked",
                            "--db-url", db_url])
    assert r.exit_code != 0
    assert "candidate" in r.output


def test_macro_propose_command_exists():
    r = runner.invoke(app, ["macro", "propose", "--help"])
    assert r.exit_code == 0
    assert "--principle" in r.output
    assert "--kind" in r.output


# --- T019 (US2): composite registration CLI ----------------------------------------

def test_register_composite_command_exists():
    r = runner.invoke(app, ["macro", "register-composite", "--help"])
    assert r.exit_code == 0
    assert "--series" in r.output
    assert "--body-file" in r.output


def test_register_composite_refuses_unknown_series(db_url, tmp_path):
    body = tmp_path / "comp.py"
    body.write_text("def compute(row):\n    return row['no_such_series']\n")
    r = runner.invoke(app, ["macro", "register-composite", "--name", "cli_mfc_comp",
                            "--series", "no_such_series", "--body-file", str(body),
                            "--db-url", db_url])
    assert r.exit_code != 0
    assert "no_such_series" in r.output
