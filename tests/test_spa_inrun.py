"""In-run SPA gate (issue #87 follow-up to spec 010).

TDD: written FIRST. A completed discovery run with a non-empty family
automatically carries a SPA re-verdict — computed at run completion, in the
same process against the same world (verification passes by construction,
tagged in_run) — so the budget gate is self-sustaining: no operator has to
remember `discover spa`. Family-0 runs carry none (nothing to test). The
in-run verdict must agree exactly with a post-run re-verdict at the same
seed/iterations: reconstruction reproducing the run is the 010 invariant.
"""
import datetime as dt
import os
import sys

import numpy as np
import psycopg
import pytest

from gefion.db import schema

sys.path.insert(0, os.path.dirname(__file__))
from discovery_synth import make_universe  # noqa: E402


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
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'spainrun%'")
        cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'disc-spainrun%'")
    c.close()


def _run(conn, name, inner_screen=None, n_days=500, seed=11):
    from gefion.regimes.discovery import runner, segregation
    u = make_universe(seed=seed, n_days=n_days, n_features=3,
                      feature_prefix="noise")
    market = segregation.MarketData(features=u.features,
                                    forward_returns=u.forward_returns,
                                    dataset_version="synth-inrun")
    kwargs = {}
    if inner_screen is not None:
        kwargs["inner_screen"] = inner_screen
    config = runner.DiscoveryConfig(
        name=name, seed=seed,
        atoms=[{"feature": "noise_0", "form": "tercile"},
               {"feature": "noise_0", "cmp": ">", "value": 0.0}],
        signals=["noise_1"], depth=1, budget=10,
        tiers=("interaction", "grammar"), holdout_weeks=13,
        universe_filter="passthrough", **kwargs)
    return runner.run_discovery(conn, config, market)


def test_completed_run_carries_inrun_reverdict(conn):
    from gefion.regimes.discovery.ledger import latest_spa_reverdict
    summary = _run(conn, "spainrun-open", inner_screen=1.0)   # non-empty family
    assert summary["family_size"] > 0
    latest = latest_spa_reverdict(conn, summary["run_id"])
    assert latest is not None, "completed run must auto-carry a re-verdict"
    assert latest["verification"]["in_run"] is True
    assert latest["verification"]["all_match"] is True
    assert latest["family_size"] == summary["family_size"]
    assert latest["seed"] == 11                                # the run's seed
    assert 0.0 <= latest["p_consistent"] <= 1.0
    # summary reports it too
    assert summary["spa"]["p_consistent"] == latest["p_consistent"]


def test_family_zero_run_carries_none(conn):
    from gefion.regimes.discovery.ledger import latest_spa_reverdict
    summary = _run(conn, "spainrun-empty")     # default screen kills noise
    assert summary["family_size"] == 0
    assert latest_spa_reverdict(conn, summary["run_id"]) is None
    assert summary.get("spa") is None


def test_inrun_verification_is_exact_same_world(conn):
    """In-run units come from the run's own live market data — recomputed
    per-unit p-values must reproduce the just-stored ones with ZERO
    divergence (same process, same world, by construction)."""
    from gefion.regimes.discovery.ledger import latest_spa_reverdict
    summary = _run(conn, "spainrun-agree", inner_screen=1.0)
    latest = latest_spa_reverdict(conn, summary["run_id"])
    assert latest["verification"]["max_abs_divergence"] == 0.0
    assert latest["verification"]["units"] == summary["family_size"]
