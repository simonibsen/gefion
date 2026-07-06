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


def _market_feature_series(cur, feature: str) -> Dict[Any, float]:
    """Market-level daily median of a computed feature; robust to cross-sectional
    outliers (penny-stock vol, bad split returns). Raises LookupError if unknown."""
    cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (feature,))
    found = cur.fetchone()
    if not found:
        raise LookupError(f"feature {feature!r} is not defined")
    cur.execute(
        "SELECT date, percentile_cont(0.5) WITHIN GROUP (ORDER BY value) "
        "FROM computed_features WHERE feature_id = %s "
        "GROUP BY date ORDER BY date",
        (found[0],),
    )
    return {d: float(v) for d, v in cur.fetchall() if v is not None}


def load_market_interaction_data(conn, signal: str, conditioning: str, horizon_days: int):
    """Load aligned market-level (signal, conditioning, forward-return) arrays by date.

    Forward return is the market-mean of each stock's close-to-close return `horizon_days`
    trading rows ahead. Raises LookupError if any input has no data.
    """
    with conn.cursor() as cur:
        sig = _market_feature_series(cur, signal)
        cond = _market_feature_series(cur, conditioning)
        cur.execute(
            """
            SELECT date, AVG(fwd) FROM (
                SELECT date, close,
                       LEAD(close, %s) OVER (PARTITION BY data_id ORDER BY date) / NULLIF(close, 0) - 1 AS fwd
                FROM stock_ohlcv
            ) t
            WHERE fwd IS NOT NULL GROUP BY date ORDER BY date
            """,
            (horizon_days,),
        )
        rets = {d: float(v) for d, v in cur.fetchall() if v is not None}
    if not rets:
        raise LookupError("no forward returns available (need OHLCV price data)")
    common = sorted(set(sig) & set(cond) & set(rets))
    if len(common) < 5:
        raise LookupError(
            f"only {len(common)} aligned dates across signal/conditioning/returns (need >=5)")
    return (np.array([sig[d] for d in common]),
            np.array([cond[d] for d in common]),
            np.array([rets[d] for d in common]))


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
