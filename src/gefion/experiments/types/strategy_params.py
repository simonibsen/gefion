"""
Strategy parameter optimization experiments.

Evaluates different parameter combinations for trading strategies
using the backtest engine.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class StrategyParamExperiment:
    """
    Optimize parameters for a trading strategy.

    Runs backtests with different parameter combinations and returns
    performance metrics for each trial.

    Example search space:
    {
        "lookback_days": {"type": "int", "low": 5, "high": 30},
        "entry_threshold": {"type": "float", "low": 0.01, "high": 0.10},
        "exit_threshold": {"type": "float", "low": 0.005, "high": 0.05}
    }
    """

    def __init__(
        self,
        strategy_name: str,
        search_space: Dict[str, Any],
        symbols: List[str],
        start_date: str,
        end_date: str,
        objective: str = "sharpe_ratio",
        db_url: Optional[str] = None,
        initial_cash: float = 100000.0,
    ):
        """
        Initialize strategy parameter experiment.

        Args:
            strategy_name: Name of strategy (momentum, mean_reversion, etc.)
            search_space: Parameter search space definition
            symbols: List of symbols to backtest
            start_date: Backtest start date (YYYY-MM-DD)
            end_date: Backtest end date (YYYY-MM-DD)
            objective: Metric to optimize (sharpe_ratio, total_return, etc.)
            db_url: Database URL for loading price data
            initial_cash: Initial portfolio cash
        """
        self.strategy_name = strategy_name
        self.search_space = search_space
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.objective = objective
        self.db_url = db_url or os.environ.get(
            "DATABASE_URL",
            "postgresql://gefion:gefionpass@localhost:6432/gefion"
        )
        self.initial_cash = initial_cash

    def evaluate(self, params: Dict[str, Any]) -> Dict[str, float]:
        """
        Run backtest with given params, return metrics.

        Args:
            params: Parameter values to test (e.g., {"lookback_days": 10})

        Returns:
            Dict of metrics including sharpe_ratio, total_return, max_drawdown
        """
        from gefion.backtest.engine import BacktestEngine
        from gefion.backtest.data_loader import load_price_data_for_backtest
        from gefion.strategies.momentum import MomentumStrategy
        from gefion.strategies.mean_reversion import MeanReversionStrategy
        from gefion.strategies.ma_crossover import MovingAverageCrossoverStrategy
        from gefion.strategies.breakout import BreakoutStrategy

        # Parse dates
        start = datetime.strptime(self.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(self.end_date, "%Y-%m-%d").date()

        # Load price data
        price_data = load_price_data_for_backtest(
            db_url=self.db_url,
            symbols=self.symbols,
            start_date=start,
            end_date=end,
        )

        if not price_data:
            # Return default metrics if no data
            return {
                "sharpe_ratio": 0.0,
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
            }

        # Create strategy with params
        strategy = self._create_strategy(params)

        # Create wrapper function for strategy
        def strategy_fn(current_date, portfolio, prices):
            return strategy.generate_signals(
                current_date=current_date,
                portfolio=portfolio,
                price_data=prices,
                initial_cash=self.initial_cash,
            )

        # Run backtest
        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy_fn,
            initial_cash=self.initial_cash,
            start_date=start,
            end_date=end,
        )

        results = engine.run()
        metrics = results["metrics"]

        return {
            "sharpe_ratio": metrics.get("sharpe_ratio", 0.0),
            "total_return": metrics.get("total_return", 0.0),
            "max_drawdown": metrics.get("max_drawdown", 0.0),
            "win_rate": metrics.get("win_rate", 0.0),
            "profit_factor": metrics.get("profit_factor", 0.0),
            "total_trades": len(results.get("trades", [])),
        }

    def _create_strategy(self, params: Dict[str, Any]):
        """Create strategy instance with given parameters."""
        from gefion.strategies.momentum import MomentumStrategy
        from gefion.strategies.mean_reversion import MeanReversionStrategy
        from gefion.strategies.ma_crossover import MovingAverageCrossoverStrategy
        from gefion.strategies.breakout import BreakoutStrategy

        if self.strategy_name == "momentum":
            return MomentumStrategy(
                lookback_days=params.get("lookback_days", 20),
                top_n=params.get("top_n", 10),
                rebalance_days=params.get("rebalance_days", 5),
            )
        elif self.strategy_name == "mean_reversion":
            return MeanReversionStrategy(
                rsi_oversold=params.get("rsi_oversold", 30.0),
                rsi_overbought=params.get("rsi_overbought", 70.0),
                rsi_period=params.get("rsi_period", 14),
                position_size=params.get("position_size", 0.2),
                max_positions=params.get("max_positions", 5),
            )
        elif self.strategy_name == "ma_crossover":
            return MovingAverageCrossoverStrategy(
                fast_period=params.get("fast_period", 50),
                slow_period=params.get("slow_period", 200),
                position_size=params.get("position_size", 0.2),
                max_positions=params.get("max_positions", 5),
            )
        elif self.strategy_name == "breakout":
            return BreakoutStrategy(
                lookback_days=params.get("lookback_days", 20),
                volume_threshold=params.get("volume_threshold", 1.5),
                position_size=params.get("position_size", 0.2),
                max_positions=params.get("max_positions", 5),
            )
        else:
            # Default to momentum if unknown strategy
            return MomentumStrategy(
                lookback_days=params.get("lookback_days", 20),
                top_n=params.get("top_n", 10),
                rebalance_days=params.get("rebalance_days", 5),
            )

    def get_objective_score(self, metrics: Dict[str, float]) -> float:
        """
        Extract the objective metric from results.

        Args:
            metrics: Dict of backtest metrics

        Returns:
            The objective metric value
        """
        return metrics.get(self.objective, 0.0)
