"""The standing negative control for regime discovery (006, T025 — US4).

This is the feature's own acceptance proof, run in CI (FR-112):

- SC-101: the FULL pipeline (pre-register → enumerate → freeze → inner screen
  → outer evaluation → one flat FDR family) admits ZERO regimes on pure-noise
  data across 20 fixed seeds.
- SC-102: with a conditional edge planted in exactly one regime (cancellation
  design — flat overall, concentrated inside), the loop recovers that regime
  and rejects the decoys in >= 95% of 20 seeded runs.

Honesty notes, so nobody mistakes this for more than it is:

- The seed sets below were fixed A PRIORI (noise 100–119, recovery 200–219).
  Runs are deterministic, so these tests are stable regression proofs of the
  guardrail MACHINERY, not a probabilistic guarantee.
- The measured false-admission rate at v1 defaults (inner_screen=0.05,
  fdr_rate=0.01) over a wider 100-seed noise scan was 1/100 (seed 142 — a
  genuine double coincidence of inner and outer evidence). That ~1% is the
  configured FDR working as specified; no nonzero admission rate can be zero
  everywhere. The FR-108 bootstrap fast-follow tightens this before search
  budgets are raised.

Budget: the whole file runs in seconds (tiny universes, depth K=1).
"""
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import ledger, runner, segregation
from tests.discovery_synth import make_universe, plant_regime_edge

NOISE_SEEDS = range(100, 120)      # fixed a priori — do not tune
RECOVERY_SEEDS = range(200, 220)   # fixed a priori — do not tune


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    c = _conn()
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'negctl-%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-negctl-%'")
    c.close()


def _market(u):
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-negctl")


def test_sc101_zero_survivors_in_pure_noise(conn):
    """The full loop, both tiers, 20 seeds of structureless data: nothing
    survives, and the ledgers stay honest (family recorded, losers counted)."""
    for seed in NOISE_SEEDS:
        u = make_universe(seed=seed, n_days=500, n_features=5)
        cfg = runner.DiscoveryConfig(
            name=f"negctl-noise-{seed}", seed=seed,
            atoms=[{"feature": f"noise_{i}", "cmp": ">", "value": 0.0}
                   for i in (1, 2, 3)],
            signals=["noise_0"], depth=1, budget=20,
            tiers=("interaction", "grammar"),
            holdout_weeks=26, min_effective_n=5, universe_filter="passthrough")
        summary = runner.run_discovery(conn, cfg, _market(u))
        assert summary["n_admitted"] == 0, (
            f"seed {seed}: discovery admitted {summary['n_admitted']} regime(s) "
            "from pure noise — the guardrails are broken")
        # accounting stays honest even when nothing survives
        run = ledger.get_run(conn, summary["run_id"])
        assert run["status"] == "complete"
        assert run["family_size"] == summary["family_size"]
        cands = ledger.list_candidates(conn, summary["run_id"])
        assert len(cands) == 6  # 3 interaction + 3 grammar candidates, all persisted
        assert all(c["verdict"] in ("rejected", "refused_low_power") for c in cands)


def test_sc102_planted_regime_recovered_decoys_rejected(conn):
    """One conditional edge planted (cancellation design), two decoys: the
    loop must find exactly the planted regime in >= 95% of seeded runs, and
    must NEVER admit a decoy without also finding the planted regime."""
    exact = 0
    for seed in RECOVERY_SEEDS:
        u = plant_regime_edge(
            make_universe(seed=seed, n_days=500, n_features=4), "noise_0",
            episode_len=10, cancel=True)
        cfg = runner.DiscoveryConfig(
            name=f"negctl-rec-{seed}", seed=seed,
            atoms=[{"feature": "planted_cond", "cmp": ">", "value": 0.0},
                   {"feature": "noise_1", "cmp": ">", "value": 0.0},
                   {"feature": "noise_2", "cmp": ">", "value": 0.0}],
            signals=["noise_0"], depth=1, budget=20, tiers=("grammar",),
            holdout_weeks=26, min_effective_n=5, universe_filter="passthrough")
        summary = runner.run_discovery(conn, cfg, _market(u))
        admitted = ledger.list_candidates(conn, summary["run_id"], verdict="admitted")
        features = sorted(f for c in admitted for f in c["provenance"]["atom_features"])
        if features == ["planted_cond"]:
            exact += 1
        else:
            # a decoy admitted alongside (or instead of) the planted regime
            assert "planted_cond" in features, (
                f"seed {seed}: planted regime missed entirely; admitted={features}")
    assert exact >= 0.95 * len(RECOVERY_SEEDS), (
        f"exact recovery in only {exact}/{len(RECOVERY_SEEDS)} runs (SC-102 needs >=95%)")


def test_negative_control_is_deterministic(conn):
    """Same seed, same inputs — identical family and verdicts (FR-111
    spot-check; the full byte-reproducibility test is US5)."""
    def one(name):
        u = make_universe(seed=107, n_days=500, n_features=5)
        cfg = runner.DiscoveryConfig(
            name=name, seed=107,
            atoms=[{"feature": "noise_1", "cmp": ">", "value": 0.0},
                   {"feature": "noise_2", "cmp": ">", "value": 0.0}],
            signals=["noise_0"], depth=2, budget=20,
            tiers=("interaction", "grammar"),
            holdout_weeks=26, min_effective_n=5, universe_filter="passthrough")
        summary = runner.run_discovery(conn, cfg, _market(u))
        cands = ledger.list_candidates(conn, summary["run_id"])
        return summary["family_size"], [
            (c["candidate_hash"], c["verdict"], c["results"]) for c in cands]

    assert one("negctl-repro-a") == one("negctl-repro-b")
