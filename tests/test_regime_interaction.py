"""Tests for the continuous-interaction test (005 T026).

Verifies OLS + Newey-West HAC recovers coefficients, detects a planted
signal×conditioning gradient, and stays silent when the edge is flat.
"""
import numpy as np

from gefion.regimes.interaction import continuous_interaction, ols_hac


def test_ols_hac_recovers_coefficients_noiseless():
    np.random.seed(0)
    n = 200
    x = np.random.randn(n)
    X = np.column_stack([np.ones(n), x])
    y = 2.0 + 3.0 * x
    beta, se, t, p = ols_hac(X, y, lags=2)
    assert abs(beta[0] - 2.0) < 1e-6
    assert abs(beta[1] - 3.0) < 1e-6


def test_interaction_detects_planted_gradient():
    np.random.seed(1)
    n = 800
    s = np.random.randn(n)
    c = np.random.randn(n)
    ret = 0.5 * s + 0.6 * s * c + 0.1 * np.random.randn(n)
    out = continuous_interaction(s, c, ret)
    assert out["interaction_pvalue"] < 0.01
    assert out["interaction_coef"] > 0.3
    assert out["n"] == n


def test_interaction_silent_when_flat():
    np.random.seed(3)
    n = 800
    s = np.random.randn(n)
    c = np.random.randn(n)
    ret = 0.5 * s + 0.1 * np.random.randn(n)  # no interaction term
    out = continuous_interaction(s, c, ret)
    assert out["interaction_pvalue"] > 0.05


def test_interaction_drops_nan_rows():
    np.random.seed(5)
    n = 30
    s = np.random.randn(n)
    c = np.random.randn(n)
    ret = 0.3 * s + 0.05 * np.random.randn(n)
    s[3] = np.nan   # two rows become invalid
    ret[7] = np.nan
    out = continuous_interaction(s, c, ret)
    assert out["n"] == n - 2
