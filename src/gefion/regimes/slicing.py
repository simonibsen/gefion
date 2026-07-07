"""Regime-sliced backtest metrics (spec 005, T021).

Attributes each dated equity-curve point and trade to its regime label and
computes per-regime metrics by reusing backtest.metrics on regime-filtered
returns. Read-only over backtest output; per-regime results reconcile to the
aggregate (FR-009).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from gefion.backtest.metrics import calculate_metrics, calculate_trade_metrics
from gefion.observability import create_span, set_attributes
from gefion.regimes.labels import effective_n, episodes as label_episodes, is_flicker

EquityCurve = List[Dict[str, Any]]


def daily_returns(equity_curve: EquityCurve) -> List[Tuple[Any, float]]:
    """Per-period returns (date, r) from a dated equity curve (skips the first point)."""
    out = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["equity"]
        cur = equity_curve[i]["equity"]
        if prev:
            out.append((equity_curve[i]["date"], cur / prev - 1))
    return out


def _synthetic_curve(returns: List[Tuple[Any, float]], initial: float) -> EquityCurve:
    """Compound a regime's returns from `initial` into a valid equity curve."""
    eq = initial
    curve = [{"date": None, "equity": initial}]
    for d, r in returns:
        eq *= (1 + r)
        curve.append({"date": d, "equity": eq})
    return curve


def slice_backtest_by_regime(
    equity_curve: EquityCurve,
    trades: List[Dict[str, Any]],
    labels_by_date: Dict[Any, str],
    initial_capital: float,
    min_effective_n: int = 20,
) -> Dict[str, Any]:
    """Compute per-regime metrics from a backtest's equity curve and trades.

    Returns {"buckets": {label: metrics}, "reconciliation_ok": bool}. Each bucket
    carries return/sharpe/drawdown/trade metrics plus raw_n, effective_n, mean_dwell,
    low_power and flicker flags. Buckets reconcile to the aggregate.
    """
    with create_span("regimes.slicing.slice") as span:
        rets = daily_returns(equity_curve)
        # ordered label series over the return dates (for episode/effective-N stats)
        label_series = [(d, labels_by_date.get(d, "undefined")) for d, _ in rets]

        # group returns by regime label
        by_label: Dict[str, List[Tuple[Any, float]]] = {}
        for (d, r) in rets:
            by_label.setdefault(labels_by_date.get(d, "undefined"), []).append((d, r))

        # group trades by regime label (attributed by trade date)
        trades_by_label: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            lab = labels_by_date.get(t.get("date"), "undefined")
            trades_by_label.setdefault(lab, []).append(t)

        buckets: Dict[str, Any] = {}
        for label, label_rets in by_label.items():
            if label == "undefined":
                continue  # excluded from findings; still counted in reconciliation below
            metrics = calculate_metrics(_synthetic_curve(label_rets, initial_capital),
                                        initial_capital)
            tmetrics = calculate_trade_metrics(trades_by_label.get(label, []))
            eff_n = effective_n(label_series, label)
            buckets[label] = {
                "total_return": metrics["total_return"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "max_drawdown": metrics["max_drawdown"],
                "win_rate": tmetrics["win_rate"],
                "profit_factor": tmetrics["profit_factor"],
                "trade_count": len(trades_by_label.get(label, [])),
                "raw_n": len(label_rets),
                "effective_n": eff_n,
                "mean_dwell": (lambda eps: float(sum(e[3] for e in eps) / len(eps)) if eps else 0.0)(
                    [e for e in label_episodes(label_series) if e[0] == label]),
                "low_power": eff_n < min_effective_n,
                "flicker": is_flicker(label_series),
            }

        # Reconciliation: product of every bucket's growth factor (incl. undefined) equals
        # the full curve's growth; trade counts across all buckets equal the total.
        full_growth = (equity_curve[-1]["equity"] / equity_curve[0]["equity"]
                       if equity_curve and equity_curve[0]["equity"] else 1.0)
        prod = 1.0
        for label_rets in by_label.values():
            for _, r in label_rets:
                prod *= (1 + r)
        total_bucket_trades = sum(len(v) for v in trades_by_label.values())
        reconciliation_ok = (abs(prod - full_growth) < 1e-6
                             and total_bucket_trades == len(trades))

        set_attributes(span, n_buckets=len(buckets), reconciliation_ok=reconciliation_ok)
        return {"buckets": buckets, "reconciliation_ok": reconciliation_ok}
