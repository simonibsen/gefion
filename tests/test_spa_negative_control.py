"""The standing SPA negative control (010, T020/T021 — US5, FR-1011).

Like 006's discovery negative control, this is a regression guarantee on the
*machinery*: seed sets fixed a priori, everything deterministic. Any failure
here is a real size or power defect, not flakiness.

Two layers:

1. **Statistical control (pure, no DB)** — M=40 seeded pure-noise families
   built in the production unit form (research R2a: demeaned interaction
   moment, sign-aligned the way `spa._unit_series` aligns with the stored
   coefficient — the alignment folds sample means positive, so it is the
   form that must be size-checked, not raw noise). Rejections at α=0.05 must
   stay within the exact binomial 99% bound; a planted edge must reject.
   Measured while designing: raw noise units 0/60 (conservative), aligned
   units 3/60 (nominal) — alignment costs the slack, not the guarantee.

2. **Full-pipeline control (DB)** — one seeded noise world through the real
   runner and the real re-verdict: with the default inner screen the family
   is empty and the re-verdict REFUSES honestly; with a pre-registered
   inner_screen=1.0 the family is non-empty and SPA does not reject.
"""
import datetime as dt
import os
import sys

import numpy as np
import psycopg
import pytest
from scipy import stats

from gefion.db import schema

sys.path.insert(0, os.path.dirname(__file__))
from discovery_synth import make_universe  # noqa: E402

D = dt.date

# Fixed a priori — do not tune after observing results.
NOISE_SEEDS = tuple(range(9100, 9140))          # M = 40
ALPHA = 0.05
BOOTSTRAP_B = 200


def _production_form_family(u, planted_beta=0.0):
    """A family of interaction units exactly as reconstruction builds them
    (R2a): z = (s - s̄)(c - c̄) · r per ordered feature pair, sign-aligned
    with the fitted direction (the stored-coef alignment in _unit_series)."""
    r = np.array([v for _, v in u.forward_returns])
    feats = [np.array([v for _, v in u.features[n]]) for n in u.feature_names()]
    if planted_beta:
        s0, c1 = feats[0], feats[1]
        r = r + planted_beta * s0 * c1
    units = []
    for i, s in enumerate(feats):
        for j, c in enumerate(feats):
            if i == j:
                continue
            z = (s - s.mean()) * (c - c.mean()) * r
            units.append(z * (1.0 if z.mean() >= 0 else -1.0))
    return np.array(units)


def test_sc1004_noise_families_reject_at_no_more_than_nominal():
    from gefion.regimes.discovery.spa import spa_test
    rejections = 0
    for seed in NOISE_SEEDS:
        u = make_universe(seed=seed, n_days=400, n_features=4)
        res = spa_test(_production_form_family(u), iterations=BOOTSTRAP_B,
                       seed=seed)
        rejections += res["p_consistent"] < ALPHA
    # exact binomial 99% bound for size <= ALPHA over M draws
    bound = int(stats.binom.isf(0.01, len(NOISE_SEEDS), ALPHA))
    assert rejections <= bound, (
        f"{rejections}/{len(NOISE_SEEDS)} noise families rejected at "
        f"α={ALPHA} — exceeds the exact binomial 99% bound ({bound}); "
        "the SPA machinery has a size defect")


def test_planted_edge_family_rejects():
    from gefion.regimes.discovery.spa import spa_test
    u = make_universe(seed=9200, n_days=400, n_features=4)
    res = spa_test(_production_form_family(u, planted_beta=0.005),
                   iterations=1000, seed=9200)
    assert res["p_consistent"] < 0.01, (
        f"planted edge not detected (p={res['p_consistent']}) — power defect")


# --- full-pipeline control (DB) -------------------------------------------------------

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
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'spanegctl%'")
    cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-spanegctl%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE 'spanegctl_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'spanegctl_%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SPN%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SPN%'")


@pytest.fixture(scope="module")
def noise_world():
    """A seeded pure-noise world: GBM prices, iid noise features — nothing to
    find, by construction."""
    c = _conn()
    with c.cursor() as cur:
        _cleanup(cur)
        rng = np.random.default_rng(31)
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('SPN1', 'A', 'Common Stock'), ('SPN2', 'B', 'Common Stock'),
               ('SPN3', 'C', 'Common Stock') RETURNING id""")
        stock_ids = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO feature_definitions (name, function_name) "
                    "VALUES ('spanegctl_sig', 'indicator') RETURNING id")
        sig_id = cur.fetchone()[0]
        cur.execute("INSERT INTO feature_definitions (name, function_name) "
                    "VALUES ('spanegctl_cond', 'indicator') RETURNING id")
        cond_id = cur.fetchone()[0]
        base = D(2024, 1, 1)
        level = [100.0, 101.0, 102.0]
        for i in range(420):
            d = base + dt.timedelta(days=i)
            sig = float(rng.normal())
            cond = float(rng.normal())
            for j, sid in enumerate(stock_ids):
                level[j] *= 1.0 + float(rng.normal(0, 0.01))
                cur.execute(
                    """INSERT INTO stock_ohlcv (data_id, date, open, high, low,
                       close, volume) VALUES (%s, %s, %s, %s, %s, %s, 1000)
                       ON CONFLICT DO NOTHING""",
                    (sid, d, level[j], level[j], level[j], level[j]))
                for fid, v in ((sig_id, sig + 0.05 * j), (cond_id, cond + 0.05 * j)):
                    cur.execute(
                        """INSERT INTO computed_features (data_id, date,
                           feature_id, value) VALUES (%s, %s, %s, %s)
                           ON CONFLICT DO NOTHING""", (sid, d, fid, v))
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _run_discovery(conn, name, **overrides):
    from gefion.regimes.discovery import runner, signals
    market = signals.load_market_data(conn, ["spanegctl_sig", "spanegctl_cond"])
    config = runner.DiscoveryConfig(
        name=name, seed=7,
        atoms=[{"feature": "spanegctl_cond", "form": "tercile"},
               {"feature": "spanegctl_cond", "cmp": ">", "value": 0.0}],
        signals=["spanegctl_sig"], depth=1, budget=10,
        tiers=("interaction", "grammar"), holdout_weeks=13,
        universe_filter="passthrough", **overrides)
    return runner.run_discovery(conn, config, market)


def test_full_pipeline_noise_empty_family_refuses(noise_world):
    """Default inner screen on noise: everything screened out, family 0 —
    the re-verdict refuses honestly instead of testing nothing."""
    from gefion.regimes.discovery import spa
    summary = _run_discovery(noise_world, "spanegctl-default")
    assert summary["n_admitted"] == 0
    assert summary["family_size"] == 0
    with pytest.raises(spa.SpaRefusal) as exc:
        spa.reverdict(noise_world, summary["run_id"], iterations=BOOTSTRAP_B,
                      seed=7)
    assert "nothing to test" in str(exc.value).lower()


def test_full_pipeline_noise_family_does_not_reject(noise_world):
    """Pre-registered inner_screen=1.0 lets noise candidates spend outer
    tests: a real non-empty family through the real runner, reconstructed
    and verified by the real re-verdict — and SPA does not reject."""
    from gefion.regimes.discovery import spa
    summary = _run_discovery(noise_world, "spanegctl-open", inner_screen=1.0)
    assert summary["n_admitted"] == 0
    assert summary["family_size"] > 0
    res = spa.reverdict(noise_world, summary["run_id"], iterations=BOOTSTRAP_B,
                        seed=7)
    assert res["verification"]["all_match"] is True
    assert res["p_consistent"] >= ALPHA, (
        f"SPA rejected a pure-noise full-pipeline family "
        f"(p={res['p_consistent']}) — size defect at the pipeline level")
