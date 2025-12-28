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


def calculate_sortino_ratio(equity_curve: List[Dict[str, Any]]) -> float:
    """
    Calculate Sortino ratio (annualized, using downside deviation only).

    Unlike Sharpe ratio, Sortino only penalizes downside volatility.

    Args:
        equity_curve: List of {date, equity} points

    Returns:
        Annualized Sortino ratio (0 if no downside returns or insufficient data)
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

    # Calculate mean return
    mean_return = sum(returns) / len(returns)

    # Calculate downside deviation (only negative returns)
    downside_returns = [r for r in returns if r < 0]

    if not downside_returns:
        # No downside returns - undefined, return 0
        return 0.0

    # Downside deviation
    downside_variance = sum(r**2 for r in downside_returns) / len(downside_returns)
    downside_deviation = math.sqrt(downside_variance)

    if downside_deviation == 0:
        return 0.0

    # Annualize (assuming 252 trading days)
    sortino = (mean_return / downside_deviation) * math.sqrt(252)

    return sortino


def calculate_calmar_ratio(
    equity_curve: List[Dict[str, Any]], days: int = 252
) -> float:
    """
    Calculate Calmar ratio (annualized return / max drawdown).

    Args:
        equity_curve: List of {date, equity} points
        days: Number of days in the period (for annualization)

    Returns:
        Calmar ratio (0 if no drawdown or insufficient data)
    """
    if len(equity_curve) < 2:
        return 0.0

    # Calculate total return
    initial_equity = equity_curve[0]["equity"]
    final_equity = equity_curve[-1]["equity"]

    if initial_equity <= 0:
        return 0.0

    total_return = (final_equity - initial_equity) / initial_equity

    # Annualize the return
    annualized_return = total_return * (365 / days) if days > 0 else total_return

    # Calculate max drawdown
    max_drawdown = _calculate_max_drawdown(equity_curve)

    # Calmar = annualized return / abs(max drawdown)
    if max_drawdown >= 0:
        # No drawdown, undefined
        return 0.0

    calmar = annualized_return / abs(max_drawdown)

    return calmar


def calculate_trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Calculate trade-based performance metrics.

    Args:
        trades: List of trade dicts with 'pnl' field (profit/loss per trade)

    Returns:
        Dict with metrics:
            - win_rate: Percentage of winning trades (0-1)
            - profit_factor: Gross profit / gross loss
            - avg_win_loss_ratio: Average win / average loss
            - total_trades: Number of trades
    """
    if not trades:
        return {
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_win_loss_ratio": 0.0,
            "total_trades": 0,
        }

    # Separate wins and losses
    wins = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
    losses = [t["pnl"] for t in trades if t.get("pnl", 0) < 0]

    total_trades = len(trades)

    # Win rate
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

    # Profit factor (gross profit / gross loss)
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    # Average win/loss ratio
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    avg_win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win_loss_ratio": avg_win_loss_ratio,
        "total_trades": total_trades,
    }


def calculate_metrics_extended(
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    initial_capital: float,
) -> Dict[str, Any]:
    """
    Calculate all performance metrics including extended metrics.

    Args:
        equity_curve: List of {date, equity} points
        trades: List of trade dicts with 'pnl' field
        initial_capital: Starting capital

    Returns:
        Dict with all metrics (original + extended)
    """
    # Get original metrics
    base_metrics = calculate_metrics(equity_curve, initial_capital)

    # Calculate extended metrics
    sortino = calculate_sortino_ratio(equity_curve)

    # Days in period for Calmar calculation
    days = len(equity_curve) if equity_curve else 0
    calmar = calculate_calmar_ratio(equity_curve, days=days)

    # Trade metrics
    trade_metrics = calculate_trade_metrics(trades)

    # Combine all metrics
    return {
        **base_metrics,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        **trade_metrics,
    }
