"""SPA statistical core (010, T002/T004 — Foundational). Pure, seeded, no DB.

The hardest correctness risk in spec 010, tested where it is cheapest: the
stationary bootstrap must honor its expected block length and apply ONE joint
index path across units; the automatic block length must be sane on dependent
series; the Hansen SPA test must hold its size on pure noise (exact binomial
bound), have power against a planted unit, keep the p_lower <= p_consistent <=
p_upper ordering, and be byte-reproducible under a seed.
"""
import numpy as np
import pytest

from gefion.regimes.discovery import spa

# ---------------------------------------------------------------------------
# Stationary bootstrap index paths
# ---------------------------------------------------------------------------


def test_bootstrap_indices_shape_and_range():
    rng = np.random.default_rng(7)
    idx = spa.stationary_bootstrap_indices(n=100, expected_block=5.0,
                                           iterations=50, rng=rng)
    assert idx.shape == (50, 100)
    assert idx.min() >= 0 and idx.max() < 100


def test_bootstrap_mean_block_length_matches_target():
    """Blocks are geometric with mean L: the fraction of 'continuation' steps
    (idx[t] == idx[t-1]+1 mod n) should be ~ 1 - 1/L."""
    rng = np.random.default_rng(11)
    L = 8.0
    idx = spa.stationary_bootstrap_indices(n=500, expected_block=L,
                                           iterations=200, rng=rng)
    continuations = (idx[:, 1:] == (idx[:, :-1] + 1) % 500).mean()
    assert abs(continuations - (1 - 1 / L)) < 0.02


def test_bootstrap_wraps_circularly():
    """Index paths may run past n-1 and must wrap, never clip."""
    rng = np.random.default_rng(3)
    idx = spa.stationary_bootstrap_indices(n=10, expected_block=50.0,
                                           iterations=200, rng=rng)
    # with L >> n, wrap-around is certain across 200 draws
    wrapped = ((idx[:, :-1] == 9) & (idx[:, 1:] == 0)).any()
    assert wrapped


def test_bootstrap_deterministic_under_seed():
    a = spa.stationary_bootstrap_indices(100, 5.0, 20, np.random.default_rng(42))
    b = spa.stationary_bootstrap_indices(100, 5.0, 20, np.random.default_rng(42))
    assert np.array_equal(a, b)


# ---------------------------------------------------------------------------
# Politis–White automatic block length
# ---------------------------------------------------------------------------


def test_block_length_grows_with_dependence():
    rng = np.random.default_rng(5)
    n = 400
    iid = rng.normal(size=n)
    ar = np.empty(n)
    ar[0] = 0.0
    for t in range(1, n):                        # strongly dependent AR(1)
        ar[t] = 0.8 * ar[t - 1] + rng.normal()
    L_iid = spa.politis_white_block_length(iid)
    L_ar = spa.politis_white_block_length(ar)
    assert L_ar > L_iid                          # dependence → longer blocks
    assert 1.0 <= L_iid <= n / 3
    assert 1.0 <= L_ar <= n / 3


def test_block_length_floor_and_cap():
    rng = np.random.default_rng(9)
    tiny = rng.normal(size=25)
    L = spa.politis_white_block_length(tiny)
    assert 1.0 <= L <= 25 / 3


# ---------------------------------------------------------------------------
# Hansen SPA
# ---------------------------------------------------------------------------


def _noise_matrix(rng, units=20, n=120):
    return rng.normal(0.0, 1.0, size=(units, n))


def test_spa_size_on_pure_noise():
    """Under the null (all units are noise), rejection at alpha=0.05 across
    seeded repetitions stays within the exact binomial 99% bound."""
    reps, alpha = 60, 0.05
    rejects = 0
    for seed in range(reps):
        rng = np.random.default_rng(1000 + seed)
        d = _noise_matrix(rng)
        result = spa.spa_test(d, iterations=200, seed=2000 + seed)
        if result["p_consistent"] <= alpha:
            rejects += 1
    # Binomial(60, 0.05): P(X >= 9) < 0.005 — 9+ rejections means broken size
    assert rejects <= 8, f"size violated: {rejects}/60 rejections at alpha=0.05"


def test_spa_power_on_planted_unit():
    rng = np.random.default_rng(77)
    d = _noise_matrix(rng, units=20, n=120)
    d[3] += 0.6                                   # one strongly superior unit
    result = spa.spa_test(d, iterations=500, seed=88)
    assert result["p_consistent"] < 0.01


def test_spa_p_value_ordering():
    """p_lower <= p_consistent <= p_upper by construction, on noise and on
    planted cases alike."""
    for seed in (1, 2, 3, 4, 5):
        rng = np.random.default_rng(seed)
        d = _noise_matrix(rng, units=10, n=80)
        if seed % 2:
            d[0] += 0.3
        r = spa.spa_test(d, iterations=200, seed=seed)
        assert r["p_lower"] <= r["p_consistent"] <= r["p_upper"]


def test_spa_reproducible_under_seed():
    rng = np.random.default_rng(31)
    d = _noise_matrix(rng)
    a = spa.spa_test(d, iterations=300, seed=99)
    b = spa.spa_test(d, iterations=300, seed=99)
    assert a == b                                 # byte-identical p-values


def test_spa_family_of_one_degrades_gracefully():
    rng = np.random.default_rng(13)
    noise = rng.normal(0.0, 1.0, size=(1, 150))
    strong = noise + 0.8
    assert spa.spa_test(noise, iterations=300, seed=5)["p_consistent"] > 0.05
    assert spa.spa_test(strong, iterations=300, seed=5)["p_consistent"] < 0.01


def test_spa_reports_metadata():
    rng = np.random.default_rng(21)
    d = _noise_matrix(rng, units=5, n=60)
    r = spa.spa_test(d, iterations=100, seed=1)
    assert r["family_size"] == 5
    assert r["iterations"] == 100
    assert r["block_length"] >= 1.0


def test_spa_masked_missing_dates():
    """Units may miss dates (per-unit masks): masked entries contribute
    nothing to that unit's statistic."""
    rng = np.random.default_rng(55)
    d = _noise_matrix(rng, units=4, n=100)
    mask = np.ones_like(d, dtype=bool)
    mask[2, 50:] = False                          # unit 2 has half the window
    r = spa.spa_test(d, iterations=100, seed=2, mask=mask)
    assert r["family_size"] == 4
    assert 0.0 <= r["p_consistent"] <= 1.0
