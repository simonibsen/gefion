"""Unit tests for the synthetic-data generators (006, T002).

The generators are test infrastructure for the discovery suite; they must be
seeded-deterministic and the planted edge must actually be conditional.
"""
import numpy as np

from tests.discovery_synth import business_days, make_universe, plant_regime_edge


def test_business_days_are_weekdays_and_deterministic():
    days = business_days(50)
    assert len(days) == 50
    assert all(d.weekday() < 5 for d in days)
    assert days == business_days(50)
    assert days == sorted(days)


def test_make_universe_deterministic_across_calls():
    a = make_universe(seed=7, n_days=100, n_features=3)
    b = make_universe(seed=7, n_days=100, n_features=3)
    assert a.dates == b.dates
    assert a.features == b.features
    assert a.forward_returns == b.forward_returns


def test_make_universe_seeds_differ():
    a = make_universe(seed=1, n_days=100, n_features=2)
    b = make_universe(seed=2, n_days=100, n_features=2)
    assert a.features != b.features


def test_make_universe_shapes():
    u = make_universe(seed=3, n_days=120, n_features=4)
    assert len(u.dates) == 120
    assert u.feature_names() == ["noise_0", "noise_1", "noise_2", "noise_3"]
    for series in u.features.values():
        assert len(series) == 120
    assert len(u.forward_returns) == 120
    # forward return at t is the price move t -> t+1
    prices = dict(u.prices)
    fwd = dict(u.forward_returns)
    d0, d1 = u.dates[0], u.dates[1]
    assert abs(fwd[d0] - (prices[d1] / prices[d0] - 1.0)) < 1e-12


def test_noise_universe_has_no_signal_alignment():
    """In pure noise, sign(signal) x forward return has ~zero mean."""
    u = make_universe(seed=11, n_days=400, n_features=1)
    sig = np.array([v for _, v in u.features["noise_0"]])
    fwd = np.array([v for _, v in u.forward_returns])
    aligned = np.sign(sig) * fwd
    assert abs(aligned.mean()) < 5 * aligned.std() / np.sqrt(len(aligned))


def test_plant_regime_edge_is_conditional():
    """The planted edge exists inside the regime and NOT outside it."""
    u = plant_regime_edge(make_universe(seed=5, n_days=400, n_features=2), "noise_0")
    assert u.planted is not None
    in_dates = set(u.planted["in_regime_dates"])
    assert 0.3 < len(in_dates) / len(u.dates) < 0.7

    sig = dict(u.features["noise_0"])
    fwd = dict(u.forward_returns)
    inside = np.array([np.sign(sig[d]) * fwd[d] for d in u.dates if d in in_dates])
    outside = np.array([np.sign(sig[d]) * fwd[d] for d in u.dates if d not in in_dates])
    assert inside.mean() > 0.01           # effect dominates the 1% daily noise
    assert abs(outside.mean()) < 0.005    # no edge leaks outside the regime


def test_plant_regime_edge_deterministic():
    a = plant_regime_edge(make_universe(seed=9, n_days=200, n_features=2), "noise_1")
    b = plant_regime_edge(make_universe(seed=9, n_days=200, n_features=2), "noise_1")
    assert a.forward_returns == b.forward_returns
    assert a.features == b.features


def test_plant_regime_edge_conditioning_has_long_episodes():
    """Conditioning must dwell long enough for episode-based effective-N."""
    u = plant_regime_edge(make_universe(seed=13, n_days=400, n_features=1), "noise_0")
    cond = [v for _, v in u.features["planted_cond"]]
    flips = sum(1 for a, b in zip(cond, cond[1:]) if (a > 0) != (b > 0))
    assert flips <= 12  # slow square wave, not flicker
