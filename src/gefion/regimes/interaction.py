"""Continuous-interaction test for graded conditioning (spec 005, T027).

Answers "does a signal's edge vary with a conditioning variable?" via a single
linear interaction term (signal × conditioning) in an OLS regression with
Newey-West (HAC) standard errors — one coefficient, one p-value. Implemented in
numpy/scipy to avoid a heavy statsmodels dependency (Constitution VI, Simplicity).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from gefion.observability import create_span, set_attributes


def ols_hac(
    X: np.ndarray, y: np.ndarray, lags: int
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """OLS with Newey-West HAC covariance. Returns (beta, se, t, p_two_sided)."""
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta

    # Score contributions u_t = X_t * e_t  (n x k)
    u = X * resid[:, None]
    S = u.T @ u  # lag 0
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)  # Bartlett kernel
        gamma = u[lag:].T @ u[:-lag]
        S += w * (gamma + gamma.T)

    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.diag(cov))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, 0.0)
    dof = max(n - k, 1)
    p = 2.0 * (1.0 - stats.t.cdf(np.abs(t), df=dof))
    return beta, se, t, p


def _newey_west_lags(n: int) -> int:
    """Standard automatic lag choice: floor(4 * (n/100)^(2/9))."""
    return max(1, int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0))))


def continuous_interaction(
    signal, conditioning, returns, lags: Optional[int] = None
) -> Dict[str, Any]:
    """Test how a signal's edge varies with a conditioning variable.

    Fits returns ~ 1 + signal + conditioning + signal*conditioning with HAC
    errors and returns the interaction coefficient and its p-value.
    """
    with create_span("regimes.interaction.continuous") as span:
        s = np.asarray(signal, dtype=float)
        c = np.asarray(conditioning, dtype=float)
        y = np.asarray(returns, dtype=float)

        mask = ~(np.isnan(s) | np.isnan(c) | np.isnan(y))
        s, c, y = s[mask], c[mask], y[mask]
        n = int(len(y))
        if n < 5:
            raise ValueError(f"continuous_interaction needs >=5 aligned rows, got {n}")

        X = np.column_stack([np.ones(n), s, c, s * c])
        L = lags if lags is not None else _newey_west_lags(n)
        beta, se, t, p = ols_hac(X, y, L)

        set_attributes(span, n=n, interaction_pvalue=float(p[3]))
        return {
            "interaction_coef": float(beta[3]),
            "interaction_pvalue": float(p[3]),
            "interaction_se": float(se[3]),
            "n": n,
            "lags": int(L),
        }
