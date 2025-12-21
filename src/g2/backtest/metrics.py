"""
Performance metrics for backtesting.

Calculate returns, risk metrics, and performance statistics.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import math


def calculate_metrics(
    equity_curve: List[Dict[str, Any]], initial_capital: float
) -> Dict[str, Any]:
    """
    Calculate backtest performance metrics.

    Args:
        equity_curve: List of {date, equity} points
        initial_capital: Starting capital

    Returns:
        Dict with metrics:
            - total_return: Total return (fraction)
            - max_drawdown: Maximum drawdown (fraction, negative)
            - sharpe_ratio: Sharpe ratio (annualized, assuming daily data)
            - num_trades: Number of equity curve points
    """
    if not equity_curve:
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "num_trades": 0,
        }

    # Total return
    final_equity = equity_curve[-1]["equity"]
    total_return = (final_equity - initial_capital) / initial_capital

    # Max drawdown
    max_drawdown = _calculate_max_drawdown(equity_curve)

    # Sharpe ratio
    sharpe_ratio = _calculate_sharpe_ratio(equity_curve)

    return {
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "num_trades": len(equity_curve),
    }


def _calculate_max_drawdown(equity_curve: List[Dict[str, Any]]) -> float:
    """
    Calculate maximum drawdown.

    Args:
        equity_curve: List of {date, equity} points

    Returns:
        Max drawdown as negative fraction (e.g., -0.15 for 15% drawdown)
    """
    if not equity_curve:
        return 0.0

    max_equity = equity_curve[0]["equity"]
    max_dd = 0.0

    for point in equity_curve:
        equity = point["equity"]

        # Update running maximum
        if equity > max_equity:
            max_equity = equity

        # Calculate drawdown from peak
        if max_equity > 0:
            dd = (equity - max_equity) / max_equity
            if dd < max_dd:
                max_dd = dd

    return max_dd


def _calculate_sharpe_ratio(equity_curve: List[Dict[str, Any]]) -> float:
    """
    Calculate Sharpe ratio (annualized, assuming daily data).

    Args:
        equity_curve: List of {date, equity} points

    Returns:
        Annualized Sharpe ratio
    """
    if len(equity_curve) < 2:
        return 0.0

    # Calculate daily returns
    returns = []
    for i in range(1, len(equity_curve)):
        prev_equity = equity_curve[i - 1]["equity"]
        curr_equity = equity_curve[i]["equity"]

        if prev_equity > 0:
            daily_return = (curr_equity - prev_equity) / prev_equity
            returns.append(daily_return)

    if not returns:
        return 0.0

    # Mean and std of daily returns
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_return = math.sqrt(variance)

    if std_return == 0:
        return 0.0

    # Annualize (assuming 252 trading days)
    sharpe = (mean_return / std_return) * math.sqrt(252)

    return sharpe
