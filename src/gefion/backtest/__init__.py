"""
Backtesting engine for strategy validation.

This package provides a simple, point-in-time correct backtesting engine
for validating trading strategies.

Features:
- Point-in-time correct execution (no look-ahead bias)
- Transaction costs (commission, spread, market impact)
- Slippage modeling (fixed, volume-based, volatility-based)
- Risk management (stop loss, take profit, position limits)
- Position sizing (fixed, percent, Kelly, volatility target)
- Walk-forward optimization (overfitting detection)
"""
from gefion.backtest.costs import (
    TransactionCosts,
    ZERO_COSTS,
    RETAIL_COSTS,
    INSTITUTIONAL_COSTS,
)
from gefion.backtest.engine import BacktestEngine
from gefion.backtest.metrics import calculate_metrics
from gefion.backtest.optimization import (
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
    WalkForwardWindow,
)
from gefion.backtest.portfolio import Portfolio
from gefion.backtest.risk import (
    RiskAction,
    RiskLimits,
    RiskManager,
    CONSERVATIVE_RISK,
    AGGRESSIVE_RISK,
)
from gefion.backtest.sizing import PositionSizer, SizingMethod
from gefion.backtest.slippage import (
    SlippageModel,
    OrderType,
    ZERO_SLIPPAGE,
    REALISTIC_SLIPPAGE,
)

__all__ = [
    # Engine
    "BacktestEngine",
    "Portfolio",
    "calculate_metrics",
    # Costs
    "TransactionCosts",
    "ZERO_COSTS",
    "RETAIL_COSTS",
    "INSTITUTIONAL_COSTS",
    # Slippage
    "SlippageModel",
    "OrderType",
    "ZERO_SLIPPAGE",
    "REALISTIC_SLIPPAGE",
    # Risk
    "RiskAction",
    "RiskLimits",
    "RiskManager",
    "CONSERVATIVE_RISK",
    "AGGRESSIVE_RISK",
    # Sizing
    "PositionSizer",
    "SizingMethod",
    # Optimization
    "WalkForwardConfig",
    "WalkForwardOptimizer",
    "WalkForwardResult",
    "WalkForwardWindow",
]
