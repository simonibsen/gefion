"""Fold-accrual automation (#105) with the vintage guard.

TDD: written FIRST. Due-ness anchors at the run's recorded holdout_end
(fold windows fully elapsed, no grade row yet). TRUST-BEARING requires
more: the window must end after the RUN EXECUTED — for a vintage
(--max-date) run the operator had already seen the post-vintage span, so
those folds are procedure evidence: evaluate_fold records them with the
descriptive flag (visible, never counted in the grade, never grid-locking),
and `discover accrue-folds` auto-grades only the trust-bearing ones.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
TODAY = None  # resolved from data below; windows are controlled via run rows


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
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'fa-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name IN ('fa_sig','fa_cond'))")
    cur.execute("DELETE FROM feature_definitions WHERE name IN ('fa_sig','fa_cond')")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'FAX%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'FAX%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'FAX%'")


def _mk_run(cur, name, holdout_end, created_at, horizon=5):
    cur.execute(
        """INSERT INTO regime_discovery_runs
               (name, seed, status, search_space, segregation,
                dataset_version, created_at)
           VALUES (%s, 1, 'complete', %s, %s, 'fa-synth', %s)
           RETURNING id""",
        (name,
         json.dumps({"horizon_days": horizon, "min_effective_n": 1,
                     "label_window": 20, "align_window": 20,
                     "dataset": "fa-synth"}),
         json.dumps({"holdout_end": str(holdout_end),
                     "holdout_start": str(holdout_end - dt.timedelta(days=90)),
                     "inner_end": str(holdout_end - dt.timedelta(days=91))}),
         created_at))
    return cur.fetchone()[0]


def _mk_admitted(cur, run_id, fold_days=180):
    cur.execute(
        """INSERT INTO regime_candidates
               (run_id, candidate_hash, expression, tier, provenance,
                results, counted_in_family, verdict)
           VALUES (%s, %s, %s, 'interaction', %s, %s, TRUE, 'admitted')
           RETURNING id""",
        (run_id, f"fa:{run_id}",
         json.dumps({"leaf": "comparison", "feature": "fa_cond",
                     "cmp": ">", "value": 0.0}),
         json.dumps({"atom_features": ["fa_cond"], "depth": 1,
                     "grading": {"scheme": "walk_forward",
                                 "fold_length_days": fold_days}}),
         json.dumps({"tests": [{"signal": "fa_sig", "bucket": None,
                                "survived": True, "pvalue": 0.001,
                                "effective_n": 30}]})))
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world():
    """Two admitted edges: NORMAL (holdout ended ~200d ago, executed then —
    fold 1 elapsed and trust-bearing) and VINTAGE (holdout ended ~700d ago,
    executed only 10d ago — folds 1-3 elapsed but pre-execution). Market
    data (2 stocks) spans the whole window so re-tests are computable."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    today = D.today()
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) VALUES "
                    "('FAX1','Stock'),('FAX2','Stock') RETURNING id")
        ids = [r[0] for r in cur.fetchall()]
        fids = {}
        for feat in ("fa_sig", "fa_cond"):
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES (%s,'indicator','stocks') "
                        "RETURNING id", (feat,))
            fids[feat] = cur.fetchone()[0]
        start = today - dt.timedelta(days=760)
        d = start
        i = 0
        while d <= today:
            if d.weekday() < 5:
                for j, sid in enumerate(ids):
                    close = 100.0 + 0.01 * i + j
                    cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open,
                        high, low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)
                        ON CONFLICT DO NOTHING""",
                                (sid, d, close, close, close, close))
                    for feat, base in (("fa_sig", 50.0), ("fa_cond", 0.0)):
                        cur.execute("""INSERT INTO computed_features (data_id,
                            date, feature_id, value) VALUES (%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING""",
                                    (sid, d, fids[feat],
                                     base + ((i % 7) - 3) * 0.5))
                i += 1
            d += dt.timedelta(days=1)
        normal_run = _mk_run(cur, "fa-normal", today - dt.timedelta(days=200),
                             today - dt.timedelta(days=200))
        vintage_run = _mk_run(cur, "fa-vintage", today - dt.timedelta(days=700),
                              today - dt.timedelta(days=10))
        normal_cand = _mk_admitted(cur, normal_run)
        vintage_cand = _mk_admitted(cur, vintage_run)
    yield {"conn": c, "normal": normal_cand, "vintage": vintage_cand,
           "today": today}
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def test_due_folds_split_by_execution_date(world):
    """due_folds lists fully-elapsed ungraded folds; a fold is trust-bearing
    only when its window ends after the run executed (vintage guard)."""
    from gefion.regimes.discovery.grading import due_folds
    due = due_folds(world["conn"])
    mine = {(f["candidate_id"], f["fold"]): f for f in due
            if f["candidate_id"] in (world["normal"], world["vintage"])}
    assert (world["normal"], 1) in mine
    assert mine[(world["normal"], 1)]["trust_bearing"] is True
    # normal fold 2 (window ends ~today+160) is NOT elapsed -> not due
    assert (world["normal"], 2) not in mine
    # vintage: folds 1..3 elapsed (700d / 180d), all pre-execution
    for fold in (1, 2, 3):
        assert (world["vintage"], fold) in mine
        assert mine[(world["vintage"], fold)]["trust_bearing"] is False


def test_accrue_grades_trust_bearing_only(world):
    """The door grades trust-bearing folds (a real grade row lands,
    descriptive=false) and only REPORTS vintage-span folds (no rows)."""
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, ["regime", "discover", "accrue-folds",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with world["conn"].cursor() as cur:
        cur.execute("SELECT fold, descriptive FROM regime_trust_grades "
                    "WHERE candidate_id = %s", (world["normal"],))
        rows = cur.fetchall()
        assert (1, False) in [(f, d) for f, d in rows], \
            "trust-bearing fold 1 must be graded as a forward result"
        cur.execute("SELECT count(*) FROM regime_trust_grades "
                    "WHERE candidate_id = %s", (world["vintage"],))
        assert cur.fetchone()[0] == 0, \
            "vintage-span folds are reported, never auto-graded"
    assert "vintage" in r.output.lower() or "descriptive" in r.output.lower()


def test_accrue_is_idempotent(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    runner = CliRunner()
    assert runner.invoke(app, ["regime", "discover", "accrue-folds",
                               "--db-url", schema.test_db_url()]).exit_code == 0
    with world["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_trust_grades "
                    "WHERE candidate_id = %s AND fold = 1", (world["normal"],))
        before = cur.fetchone()[0]
    r = runner.invoke(app, ["regime", "discover", "accrue-folds",
                            "--db-url", schema.test_db_url()])
    assert r.exit_code == 0
    with world["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM regime_trust_grades "
                    "WHERE candidate_id = %s AND fold = 1", (world["normal"],))
        assert cur.fetchone()[0] == before


def test_manual_vintage_fold_records_descriptive(world):
    """The guard lives in evaluate_fold itself: a manual grade-fold on a
    pre-execution window records with the DESCRIPTIVE flag — visible,
    never counted, never grid-locking (register still allowed after)."""
    from gefion.regimes.discovery.grading import get_scheme
    from gefion.regimes.discovery.signals import load_market_data
    scheme = get_scheme("walk_forward")
    market = load_market_data(world["conn"], ["fa_sig", "fa_cond"],
                              horizon_days=5, dataset_version="fa-synth")
    outcome = scheme.evaluate_fold(world["conn"], market,
                                   world["vintage"], fold=1)
    assert outcome.get("descriptive") is True
    with world["conn"].cursor() as cur:
        cur.execute("SELECT descriptive FROM regime_trust_grades "
                    "WHERE candidate_id = %s AND fold = 1", (world["vintage"],))
        assert cur.fetchone()[0] is True
    # grid must still be movable: descriptive rows are not forward evidence
    scheme.register(world["conn"], world["vintage"], fold_length_days=180)
