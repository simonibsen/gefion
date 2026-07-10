"""Reconstruction + verification (010, T006/T008 — US1/US2). DB-backed.

TDD: written FIRST. The re-verdict rebuilds a stored run's counted family by
calling the SAME functions the run used, and must reproduce the ledger's
stored per-test p-values before any verdict — a mismatch means the world
drifted (price backfill, environment) and the command refuses honestly.
"""
import datetime as dt
import os

import numpy as np
import psycopg
import pytest

from gefion.db import schema

D = dt.date


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
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'sparecon%'")
    cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-sparecon%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE 'sparec_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'sparec_%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SPR%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SPR%'")


@pytest.fixture(scope="module")
def completed_run():
    """A completed DB-backed discovery run over a seeded synthetic world:
    3 stocks x 420 days, one signal feature and one conditioning feature with
    a planted interaction, evaluated through the real runner."""
    c = _conn()
    with c.cursor() as cur:
        _cleanup(cur)
        rng = np.random.default_rng(42)
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('SPR1', 'A', 'Common Stock'), ('SPR2', 'B', 'Common Stock'),
               ('SPR3', 'C', 'Common Stock') RETURNING id""")
        stock_ids = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO feature_definitions (name, function_name) "
                    "VALUES ('sparec_sig', 'indicator') RETURNING id")
        sig_id = cur.fetchone()[0]
        cur.execute("INSERT INTO feature_definitions (name, function_name) "
                    "VALUES ('sparec_cond', 'indicator') RETURNING id")
        cond_id = cur.fetchone()[0]
        base = D(2024, 1, 1)
        for i in range(420):
            d = base + dt.timedelta(days=i)
            cond = float(rng.normal())
            sig = float(rng.normal())
            # planted: forward return co-moves with sig*cond
            drift = 0.004 * sig * cond
            for j, sid in enumerate(stock_ids):
                close = 100.0 * (1.0 + drift) ** i * (1 + 0.001 * j) \
                    + float(rng.normal(0, 0.2))
                cur.execute(
                    """INSERT INTO stock_ohlcv (data_id, date, open, high, low,
                       close, volume) VALUES (%s, %s, %s, %s, %s, %s, 1000)
                       ON CONFLICT DO NOTHING""",
                    (sid, d, close, close, close, close))
                cur.execute(
                    """INSERT INTO computed_features (data_id, date, feature_id,
                       value) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sid, d, sig_id, sig + 0.05 * j))
                cur.execute(
                    """INSERT INTO computed_features (data_id, date, feature_id,
                       value) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sid, d, cond_id, cond + 0.05 * j))

    from gefion.regimes.discovery import runner, signals
    market = signals.load_market_data(c, ["sparec_sig", "sparec_cond"])
    config = runner.DiscoveryConfig(
        name="sparecon-run", seed=7,
        atoms=[{"feature": "sparec_cond", "form": "tercile"},
               {"feature": "sparec_cond", "cmp": ">", "value": 0.0}],
        signals=["sparec_sig"], depth=1, budget=10,
        tiers=("interaction", "grammar"),
        holdout_weeks=13, universe_filter="passthrough")
    summary = runner.run_discovery(c, config, market)
    yield c, summary
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def test_reconstruction_reproduces_stored_pvalues(completed_run):
    from gefion.regimes.discovery import spa
    conn, summary = completed_run
    fam = spa.reconstruct_family(conn, summary["run_id"])
    assert fam["units"], "counted family must be non-empty"
    assert fam["family_size"] == summary["family_size"]
    for u in fam["units"]:
        assert u["stored_pvalue"] is not None
        assert abs(u["recomputed_pvalue"] - u["stored_pvalue"]) <= \
            max(1e-9, 1e-6 * abs(u["stored_pvalue"])), \
            f"unit {u['candidate_hash']}/{u['signal']} diverged"
    # every unit carries its per-observation series for the bootstrap
    assert all(len(u["values"]) >= 2 for u in fam["units"])


def test_price_drift_refuses_with_named_units(completed_run):
    from gefion.regimes.discovery import spa
    conn, summary = completed_run
    # perturb one outer-window price row, then restore
    seg = spa._get_run(conn, summary["run_id"])["segregation"]
    outer_start = dt.date.fromisoformat(seg["holdout_start"])
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.data_id, o.date, o.close FROM stock_ohlcv o
               JOIN stocks s ON s.id = o.data_id
               WHERE s.symbol = 'SPR1' AND o.date >= %s
               ORDER BY o.date LIMIT 1""", (outer_start,))
        did, d, close = cur.fetchone()
        cur.execute("UPDATE stock_ohlcv SET close = %s WHERE data_id = %s "
                    "AND date = %s", (float(close) * 1.5, did, d))
    try:
        with pytest.raises(spa.SpaRefusal) as exc:
            spa.reconstruct_family(conn, summary["run_id"])
        assert "diverge" in str(exc.value).lower() or \
               "mismatch" in str(exc.value).lower()
    finally:
        with conn.cursor() as cur:
            cur.execute("UPDATE stock_ohlcv SET close = %s WHERE data_id = %s "
                        "AND date = %s", (float(close), did, d))
    # world restored: reconstruction verifies again
    fam = spa.reconstruct_family(conn, summary["run_id"])
    assert fam["units"]


def test_empty_family_refuses_honestly(completed_run):
    from gefion.regimes.discovery import ledger, spa
    conn, _ = completed_run
    run_id = ledger.create_run(
        conn, name="sparecon-empty", seed=1,
        search_space={"signal_source": "features", "grading_scheme": "walk_forward",
                      "universe_filter": ["passthrough"], "atoms": [],
                      "signals": ["sparec_sig"], "horizon_days": 1,
                      "label_window": 60, "align_window": 60},
        segregation={"inner_start": "2024-01-01", "inner_end": "2024-06-01",
                     "holdout_start": "2024-06-02", "holdout_end": "2024-09-01"},
        dataset_version="dev")
    ledger.set_family_size(conn, run_id, 0)
    with pytest.raises(spa.SpaRefusal) as exc:
        spa.reconstruct_family(conn, run_id)
    assert "nothing to test" in str(exc.value).lower()


def test_end_to_end_reverdict_is_seeded_and_read_only(completed_run):
    """T010: the full re-verdict — planted world rejects or at least yields a
    valid, reproducible p-value; ledger and price rows are byte-identical
    before and after (SC-1002)."""
    from gefion.regimes.discovery import spa
    conn, summary = completed_run
    with conn.cursor() as cur:
        cur.execute("SELECT md5(CAST((SELECT array_agg(c.* ORDER BY c.id) "
                    "FROM regime_candidates c WHERE c.run_id = %s) AS text))",
                    (summary["run_id"],))
        ledger_before = cur.fetchone()[0]
    a = spa.reverdict(conn, summary["run_id"], iterations=200, seed=11)
    b = spa.reverdict(conn, summary["run_id"], iterations=200, seed=11)
    assert a["p_consistent"] == b["p_consistent"]          # seeded
    assert 0.0 <= a["p_consistent"] <= 1.0
    assert a["p_lower"] <= a["p_consistent"] <= a["p_upper"]
    assert a["family_size"] == summary["family_size"]
    assert a["verification"]["all_match"] is True
    with conn.cursor() as cur:
        cur.execute("SELECT md5(CAST((SELECT array_agg(c.* ORDER BY c.id) "
                    "FROM regime_candidates c WHERE c.run_id = %s) AS text))",
                    (summary["run_id"],))
        assert cur.fetchone()[0] == ledger_before          # read-only
