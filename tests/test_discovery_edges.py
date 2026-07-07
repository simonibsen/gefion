"""Edge-evaluation tests for regime discovery (006, T013 — US1 tier 1).

TDD: written FIRST. The v1 signal source builds per-observation records from
active features causally (trailing-median alignment, no future data); the
tier-1 path answers the gradient question per candidate via the 005
continuous-interaction test, restricted to the outer holdout window.
"""
import numpy as np
import pytest

from gefion.experiments.holdout import HoldoutManager
from gefion.regimes.discovery import edges, grammar, segregation, signals
from discovery_synth import make_universe, plant_regime_edge


def _market(u):
    return segregation.MarketData(features=u.features,
                                  forward_returns=u.forward_returns,
                                  dataset_version="synth-test")


# --- per-observation records from signal_source='features' -------------------

def test_signal_source_validates_signals():
    u = make_universe(seed=1, n_days=200, n_features=2)
    with pytest.raises(LookupError):
        signals.FeatureSignalSource(_market(u), ["nope"])


def test_records_are_causal_and_aligned():
    u = make_universe(seed=2, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"], align_window=60)
    recs = src.records("noise_0")
    # warmup: no record before the trailing window is full
    assert len(recs) == 300 - 59
    fwd = dict(u.forward_returns)
    for r in recs[:20]:
        assert r["baseline_score"] == 0.0
        assert abs(r["experimental_score"]) == pytest.approx(abs(fwd[r["date"]]))


def _truncate(u, n):
    """Same universe, future removed — the honest way to test causality."""
    import dataclasses
    return dataclasses.replace(
        u,
        dates=u.dates[:n],
        prices=u.prices[:n],
        features={k: v[:n] for k, v in u.features.items()},
        forward_returns=u.forward_returns[:n],
    )


def test_records_prefix_invariant():
    """Alignment at date t must not change when future data is appended —
    the causal guarantee, tested by truncation."""
    u_full = make_universe(seed=4, n_days=300, n_features=1)
    u_short = _truncate(u_full, 200)
    full = signals.FeatureSignalSource(_market(u_full), ["noise_0"]).records("noise_0")
    short = signals.FeatureSignalSource(_market(u_short), ["noise_0"]).records("noise_0")
    by_date = {r["date"]: r for r in full}
    for r in short:
        assert by_date[r["date"]]["experimental_score"] == pytest.approx(
            r["experimental_score"])


def test_records_window_restriction():
    u = make_universe(seed=5, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    start, end = u.dates[250], u.dates[280]
    recs = src.records("noise_0", start=start, end=end)
    assert recs and all(start <= r["date"] <= end for r in recs)


# --- tier-1: continuous-interaction per candidate -----------------------------

def test_tier1_interaction_finds_planted_gradient():
    u = plant_regime_edge(make_universe(seed=6, n_days=400, n_features=2), "noise_0")
    src = signals.FeatureSignalSource(_market(u), ["noise_0", "noise_1"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="planted_cond")
    assert test["pvalue"] is not None and test["pvalue"] < 0.01
    null = edges.tier1_interaction_test(
        src, signal="noise_1", conditioning_feature="planted_cond")
    assert null["pvalue"] is None or null["pvalue"] > 0.01


def test_tier1_refuses_small_samples():
    u = make_universe(seed=7, n_days=300, n_features=1)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="noise_0",
        start=u.dates[-2], end=u.dates[-1])
    assert test["pvalue"] is None and test["reason"] == "min_sample"
    assert test["n"] < 5


def test_tier1_respects_evaluation_window():
    """Restricting to the outer holdout must only use holdout observations."""
    u = plant_regime_edge(make_universe(seed=8, n_days=400, n_features=1), "noise_0")
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=13)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    test = edges.tier1_interaction_test(
        src, signal="noise_0", conditioning_feature="planted_cond",
        start=holdout.holdout_start_date, end=holdout.holdout_end_date)
    outer_days = sum(1 for d in u.dates if d >= holdout.holdout_start_date)
    assert test["n"] <= outer_days


# --- causal bucket labels from candidate ASTs ---------------------------------

def test_causal_labels_from_tercile_candidate():
    u = make_universe(seed=9, n_days=300, n_features=2)
    cand = grammar.enumerate_candidates(
        [{"feature": "noise_0", "form": "tercile"}], depth=1)[0]
    labels = edges.causal_labels(cand, _market(u), window=60)
    assert set(labels.values()) <= {"low", "mid", "high", "undefined"}
    defined = [lab for lab in labels.values() if lab != "undefined"]
    assert len(defined) == 300 - 59


def test_causal_labels_prefix_invariant():
    """Label at t must not change when future data is appended (FR-103)."""
    u_full = make_universe(seed=10, n_days=300, n_features=1)
    u_short = _truncate(u_full, 220)
    cand = grammar.enumerate_candidates(
        [{"feature": "noise_0", "form": "tercile"}], depth=1)[0]
    full = edges.causal_labels(cand, _market(u_full), window=60)
    short = edges.causal_labels(cand, _market(u_short), window=60)
    for d, lab in short.items():
        assert full[d] == lab


def test_causal_labels_boolean_composite():
    u = make_universe(seed=11, n_days=200, n_features=2)
    cands = grammar.enumerate_candidates(
        [{"feature": "noise_0", "cmp": ">", "value": 0.0},
         {"feature": "noise_1", "cmp": ">", "value": 0.0}], depth=2)
    composite = next(c for c in cands if c.depth == 2)
    labels = edges.causal_labels(composite, _market(u), window=60)
    assert set(labels.values()) <= {"true", "false"}


# --- tier 2: grammar candidates through the conditional gate (US2, T020) -----

def _planted_tier2(seed=21):
    """Planted edge with short episodes so the holdout holds several; the
    conditioning atom is boolean (planted_cond > 0 == in-regime). The
    cancellation design makes decoy buckets net flat — decoy rejection is
    only meaningful when the edge is genuinely conditional."""
    u = plant_regime_edge(
        make_universe(seed=seed, n_days=500, n_features=3), "noise_0",
        episode_len=10, cancel=True)
    return u


def test_tier2_bucket_pvalues_find_the_planted_bucket():
    from gefion.experiments.holdout import HoldoutManager
    u = _planted_tier2()
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=26)
    cand = grammar.enumerate_candidates(
        [{"feature": "planted_cond", "cmp": ">", "value": 0.0}], depth=1)[0]
    labels = edges.causal_labels(cand, _market(u), window=60)
    tests = edges.tier2_bucket_tests(
        src, signal="noise_0", labels_by_date=labels,
        start=holdout.holdout_start_date, end=holdout.holdout_end_date,
        min_effective_n=5)
    by_bucket = {t["bucket"]: t for t in tests}
    assert by_bucket["true"]["pvalue"] is not None
    assert by_bucket["true"]["pvalue"] < 0.01          # the edge lives here
    assert (by_bucket["false"]["pvalue"] is None        # refused, or
            or by_bucket["false"]["pvalue"] > 0.01)     # honestly unimpressive


def test_tier2_low_power_bucket_is_refused_fail_closed():
    from gefion.experiments.holdout import HoldoutManager
    u = _planted_tier2(seed=22)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=26)
    cand = grammar.enumerate_candidates(
        [{"feature": "planted_cond", "cmp": ">", "value": 0.0}], depth=1)[0]
    labels = edges.causal_labels(cand, _market(u), window=60)
    tests = edges.tier2_bucket_tests(
        src, signal="noise_0", labels_by_date=labels,
        start=holdout.holdout_start_date, end=holdout.holdout_end_date,
        min_effective_n=50)  # unreachable floor in a 26-week holdout
    assert tests, "buckets must still be reported"
    assert all(t["pvalue"] is None and t["low_power"] for t in tests)


def test_tier2_every_bucket_test_is_reported():
    """Family assembly needs every (signal x candidate x bucket) evaluation —
    nothing dropped, refusals included (FR-104)."""
    from gefion.experiments.holdout import HoldoutManager
    u = make_universe(seed=23, n_days=500, n_features=2)
    src = signals.FeatureSignalSource(_market(u), ["noise_0"])
    holdout = HoldoutManager(max_date=u.dates[-1], holdout_weeks=26)
    cand = grammar.enumerate_candidates(
        [{"feature": "noise_1", "form": "tercile"}], depth=1)[0]
    labels = edges.causal_labels(cand, _market(u), window=60)
    tests = edges.tier2_bucket_tests(
        src, signal="noise_0", labels_by_date=labels,
        start=holdout.holdout_start_date, end=holdout.holdout_end_date,
        min_effective_n=3)
    observed_buckets = {lab for d, lab in labels.items()
                        if lab != "undefined" and d >= holdout.holdout_start_date}
    assert {t["bucket"] for t in tests} == observed_buckets


# --- load_market_data: the real-run data path (DB) ---------------------------

def _conn():
    import os
    import psycopg
    from gefion.db import schema as dbschema
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(dbschema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def seeded_conn():
    import datetime
    c = _conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'EDGT%'")
        cur.execute("DELETE FROM feature_definitions WHERE name = 'edgetest_feat'")
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('EDGT1', 'A', 'Common Stock'), ('EDGT2', 'B', 'ETF')
               RETURNING id"""
        )
        ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name) "
            "VALUES ('edgetest_feat', 'indicator') RETURNING id"
        )
        feat_id = cur.fetchone()[0]
        base = datetime.date(2024, 1, 1)
        for i in range(5):
            d = base + datetime.timedelta(days=i)
            for j, sid in enumerate(ids):
                close = 100.0 + i + 10 * j
                cur.execute(
                    """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, 1000)
                       ON CONFLICT DO NOTHING""",
                    (sid, d, close, close, close, close),
                )
                cur.execute(
                    """INSERT INTO computed_features (data_id, date, feature_id, value)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sid, d, feat_id, float(j)),  # EDGT1 -> 0.0, EDGT2 -> 1.0
                )
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id = "
                    "(SELECT id FROM feature_definitions WHERE name = 'edgetest_feat')")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'EDGT%')")
        cur.execute("DELETE FROM feature_definitions WHERE name = 'edgetest_feat'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'EDGT%'")
    c.close()


def test_load_market_data_respects_symbol_universe(seeded_conn):
    market_all = signals.load_market_data(seeded_conn, ["edgetest_feat"], symbols=None)
    market_one = signals.load_market_data(
        seeded_conn, ["edgetest_feat"], symbols=["EDGT1"])
    # both stocks: median of {0.0, 1.0} = 0.5; EDGT1 only: 0.0
    assert market_all.features["edgetest_feat"][0][1] == pytest.approx(0.5)
    assert market_one.features["edgetest_feat"][0][1] == pytest.approx(0.0)
    assert market_one.forward_returns  # LEAD-based forward returns exist


def test_load_market_data_unknown_feature_raises(seeded_conn):
    with pytest.raises(LookupError):
        signals.load_market_data(seeded_conn, ["definitely_not_a_feature"])


def test_load_market_data_optional_features_skipped(seeded_conn):
    market = signals.load_market_data(
        seeded_conn, ["edgetest_feat", "vix_level"], optional_features=["vix_level"])
    assert "vix_level" not in market.features
    assert "edgetest_feat" in market.features


def test_load_market_data_max_date_truncates_the_vintage(seeded_conn):
    """Issue #68: vintage re-discovery loads the world as of a past date —
    features AND forward returns must stop there."""
    import datetime
    cutoff = datetime.date(2024, 1, 3)
    market = signals.load_market_data(
        seeded_conn, ["edgetest_feat"], max_date=cutoff)
    assert max(d for d, _ in market.features["edgetest_feat"]) <= cutoff
    assert max(d for d, _ in market.forward_returns) <= cutoff
    full = signals.load_market_data(seeded_conn, ["edgetest_feat"])
    assert max(d for d, _ in full.features["edgetest_feat"]) > cutoff
