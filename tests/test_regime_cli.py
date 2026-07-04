"""CLI tests for the `regime` command group (005 T013).

Uses CliRunner + --db-url for test-DB isolation (matches cross-sectional-compute).
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
    from gefion.db.schema import test_db_url
    import psycopg
    url = test_db_url()
    with psycopg.connect(url, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM regime_labels")
            cur.execute("DELETE FROM regime_definitions")
    return url


def _write_expr(tmp_path):
    p = tmp_path / "expr.json"
    p.write_text(json.dumps({
        "leaf": "comparison", "feature": "realized_vol_20",
        "cmp": "quantile", "value": "tercile", "scope": "market",
    }))
    return str(p)


def _bucketing(tmp_path):
    p = tmp_path / "buckets.json"
    p.write_text(json.dumps({"labels": ["calm", "normal", "stressed"], "method": "tercile"}))
    return str(p)


def test_regime_help_lists_subcommands():
    result = runner.invoke(app, ["regime", "--help"])
    assert result.exit_code == 0
    for sub in ("define", "list", "show", "compute", "labels", "archive", "export",
                "import", "interaction"):
        assert sub in result.output


def test_define_then_list_and_show(db_url, tmp_path):
    r = runner.invoke(app, [
        "regime", "define", "--name", "vol-regime", "--scope", "market",
        "--expression", _write_expr(tmp_path), "--bucketing", _bucketing(tmp_path),
        "--db-url", db_url, "--json",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["regime", "list", "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    assert "vol-regime" in r.output

    r = runner.invoke(app, ["regime", "show", "vol-regime", "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["scope"] == "market"


def test_define_rejects_invalid_scope(db_url, tmp_path):
    r = runner.invoke(app, [
        "regime", "define", "--name", "bad", "--scope", "galaxy",
        "--expression", _write_expr(tmp_path), "--bucketing", _bucketing(tmp_path),
        "--db-url", db_url,
    ])
    assert r.exit_code != 0


def test_archive_marks_archived(db_url, tmp_path):
    runner.invoke(app, [
        "regime", "define", "--name", "vol-regime", "--scope", "market",
        "--expression", _write_expr(tmp_path), "--bucketing", _bucketing(tmp_path),
        "--db-url", db_url,
    ])
    r = runner.invoke(app, ["regime", "archive", "vol-regime", "--db-url", db_url])
    assert r.exit_code == 0
    r = runner.invoke(app, ["regime", "show", "vol-regime", "--db-url", db_url, "--json"])
    assert json.loads(r.output)["status"] == "archived"


def test_export_then_import(db_url, tmp_path):
    runner.invoke(app, [
        "regime", "define", "--name", "vol-regime", "--scope", "market",
        "--expression", _write_expr(tmp_path), "--bucketing", _bucketing(tmp_path),
        "--db-url", db_url,
    ])
    outdir = tmp_path / "export"
    r = runner.invoke(app, ["regime", "export", str(outdir), "--db-url", db_url])
    assert r.exit_code == 0
    assert (outdir / "vol-regime.json").exists()

    # wipe and re-import
    import psycopg
    with psycopg.connect(db_url, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM regime_definitions")
    r = runner.invoke(app, ["regime", "import", str(outdir), "--db-url", db_url, "--json"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["regime", "show", "vol-regime", "--db-url", db_url, "--json"])
    assert r.exit_code == 0


def test_backtest_run_has_by_regime_option():
    """US2 T022: backtest run exposes --by-regime (additive slicing)."""
    r = runner.invoke(app, ["backtest", "run", "--help"])
    assert r.exit_code == 0
    assert "--by-regime" in r.output


def test_interaction_errors_gracefully_without_data(db_url):
    """US5 T028: interaction test errors clearly when the signal feature is absent."""
    r = runner.invoke(app, [
        "regime", "interaction", "--signal", "no_such_feature", "--by", "also_missing",
        "--db-url", db_url,
    ])
    assert r.exit_code != 0
