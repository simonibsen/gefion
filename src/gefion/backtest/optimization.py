"""
Walk-forward optimization for backtesting.

Provides rolling window optimization to detect overfitting
and validate strategy robustness.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional
import itertools

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore


# Supported optimization metrics
SUPPORTED_METRICS = [
    "sharpe_ratio",
    "total_return",
    "max_drawdown",
    "calmar_ratio",
    "sortino_ratio",
]


@dataclass
class WalkForwardConfig:
    """
    Configuration for walk-forward optimization.

    Attributes:
        train_days: Number of trading days in training window
        test_days: Number of trading days in test window
        step_days: Number of days to step forward between windows
        optimization_metric: Metric to optimize (sharpe_ratio, total_return, etc.)
    """

    train_days: int = 252  # 1 year
    test_days: int = 63  # 1 quarter
    step_days: int = 63  # 1 quarter
    optimization_metric: str = "sharpe_ratio"


@dataclass
class WalkForwardWindow:
    """
    A single walk-forward window.

    Contains train and test date ranges.
    """

    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class WalkForwardResult:
    """
    Results from walk-forward optimization.

    Contains per-window and aggregate metrics.
    """

    windows: List[WalkForwardWindow]
    best_params_per_window: List[Dict[str, Any]]
    in_sample_metrics: List[Dict[str, float]]
    out_of_sample_metrics: List[Dict[str, float]]
    aggregate_metrics: Dict[str, float]
    overfitting_score: float


def expand_param_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """
    Expand parameter grid into list of all combinations.

    Args:
        grid: Dict mapping param name to list of values

    Returns:
        List of dicts, each containing one combination
    """
    if not grid:
        return [{}]

    keys = list(grid.keys())
    values = list(grid.values())

    combinations = []
    for combo in itertools.product(*values):
        combinations.append(dict(zip(keys, combo)))

    return combinations


def calculate_overfitting_score(
    in_sample_returns: List[float],
    out_of_sample_returns: List[float],
) -> float:
    """
    Calculate overfitting score based on performance degradation.

    A score of 0 means no overfitting (out-of-sample matches in-sample).
    A score of 1 means complete overfitting (out-of-sample is 0 or negative
    while in-sample is positive).

    Args:
        in_sample_returns: Returns from training periods
        out_of_sample_returns: Returns from test periods

    Returns:
        Overfitting score between 0 and 1
    """
    if not in_sample_returns or not out_of_sample_returns:
        return 0.0

    avg_in_sample = sum(in_sample_returns) / len(in_sample_returns)
    avg_out_sample = sum(out_of_sample_returns) / len(out_of_sample_returns)

    if avg_in_sample <= 0:
        # No positive in-sample return to compare against
        return 0.0

    # Calculate degradation ratio
    degradation = (avg_in_sample - avg_out_sample) / avg_in_sample

    # Clamp between 0 and 1
    return max(0.0, min(1.0, degradation))


class WalkForwardOptimizer:
    """
    Walk-forward optimization engine.

    Performs rolling window optimization to:
    1. Train strategy parameters on in-sample data
    2. Validate on out-of-sample data
    3. Detect overfitting by comparing in/out-of-sample performance
    """

    def __init__(self, config: WalkForwardConfig):
        """
        Initialize optimizer with configuration.

        Args:
            config: Walk-forward configuration
        """
        self.config = config

    def generate_windows(
        self,
        start_date: date,
        end_date: date,
    ) -> List[WalkForwardWindow]:
        """
        Generate walk-forward windows.

        Args:
            start_date: Start date of data
            end_date: End date of data

        Returns:
            List of WalkForwardWindow objects
        """
        windows = []

        # Calculate minimum required days
        min_days = self.config.train_days + self.config.test_days
        total_days = (end_date - start_date).days

        if total_days < min_days:
            return []

        current_train_start = start_date

        while True:
            # Calculate dates for this window
            train_end = current_train_start + timedelta(
                days=self.config.train_days
            )
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=self.config.test_days - 1)

            # Check if test period fits
            if test_end > end_date:
                break

            windows.append(
                WalkForwardWindow(
                    train_start=current_train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

            # Step forward
            current_train_start = current_train_start + timedelta(
                days=self.config.step_days
            )

            # Safety check to prevent infinite loop
            if current_train_start > end_date:
                break

        return windows

    def run(
        self,
        price_data: "pd.DataFrame",
        strategy_factory: Callable[[Dict[str, Any]], Any],
        param_grid: Dict[str, List[Any]],
        symbols: List[str],
        initial_cash: float = 100000.0,
    ) -> WalkForwardResult:
        """
        Run walk-forward optimization.

        Args:
            price_data: DataFrame with dates as index, symbols as columns
            strategy_factory: Function that creates strategy from params
            param_grid: Parameter grid to search
            symbols: List of symbols to trade
            initial_cash: Initial portfolio cash

        Returns:
            WalkForwardResult with per-window and aggregate metrics
        """
        if pd is None:
            raise ImportError("pandas is required for walk-forward optimization")

        # Get date range from data
        start_date = price_data.index.min().date()
        end_date = price_data.index.max().date()

        # Generate windows
        windows = self.generate_windows(start_date, end_date)

        if not windows:
            return WalkForwardResult(
                windows=[],
                best_params_per_window=[],
                in_sample_metrics=[],
                out_of_sample_metrics=[],
                aggregate_metrics={},
                overfitting_score=0.0,
            )

        # Expand parameter grid
        param_combinations = expand_param_grid(param_grid)

        best_params_per_window = []
        in_sample_metrics = []
        out_of_sample_metrics = []

        for window in windows:
            # Optimize on training data
            best_params, train_metrics = self._optimize_window(
                price_data=price_data,
                window=window,
                strategy_factory=strategy_factory,
                param_combinations=param_combinations,
                symbols=symbols,
                initial_cash=initial_cash,
            )

            best_params_per_window.append(best_params)
            in_sample_metrics.append(train_metrics)

            # Evaluate on test data
            test_metrics = self._evaluate_window(
                price_data=price_data,
                window=window,
                strategy_factory=strategy_factory,
                params=best_params,
                symbols=symbols,
                initial_cash=initial_cash,
            )
            out_of_sample_metrics.append(test_metrics)

        # Calculate aggregate metrics
        aggregate_metrics = self._calculate_aggregate_metrics(
            out_of_sample_metrics
        )

        # Calculate overfitting score
        in_sample_returns = [m.get("total_return", 0) for m in in_sample_metrics]
        out_sample_returns = [
            m.get("total_return", 0) for m in out_of_sample_metrics
        ]
        overfitting_score = calculate_overfitting_score(
            in_sample_returns, out_sample_returns
        )

        return WalkForwardResult(
            windows=windows,
            best_params_per_window=best_params_per_window,
            in_sample_metrics=in_sample_metrics,
            out_of_sample_metrics=out_of_sample_metrics,
            aggregate_metrics=aggregate_metrics,
            overfitting_score=overfitting_score,
        )

    def _optimize_window(
        self,
        price_data: "pd.DataFrame",
        window: WalkForwardWindow,
        strategy_factory: Callable[[Dict[str, Any]], Any],
        param_combinations: List[Dict[str, Any]],
        symbols: List[str],
        initial_cash: float,
    ) -> tuple:
        """Optimize parameters on training window."""
        best_metric = float("-inf")
        best_params: Dict[str, Any] = {}
        best_metrics: Dict[str, float] = {}

        # Slice data to training period
        train_mask = (
            (price_data.index >= pd.Timestamp(window.train_start))
            & (price_data.index <= pd.Timestamp(window.train_end))
        )
        train_data = price_data[train_mask]

        for params in param_combinations:
            # Create strategy with these params
            strategy = strategy_factory(params)

            # Run backtest on training data
            metrics = self._run_backtest(
                price_data=train_data,
                strategy=strategy,
                symbols=symbols,
                initial_cash=initial_cash,
            )

            # Check if this is the best
            metric_value = metrics.get(self.config.optimization_metric, 0)
            if metric_value > best_metric:
                best_metric = metric_value
                best_params = params
                best_metrics = metrics

        return best_params, best_metrics

    def _evaluate_window(
        self,
        price_data: "pd.DataFrame",
        window: WalkForwardWindow,
        strategy_factory: Callable[[Dict[str, Any]], Any],
        params: Dict[str, Any],
        symbols: List[str],
        initial_cash: float,
    ) -> Dict[str, float]:
        """Evaluate parameters on test window."""
        # Slice data to test period
        test_mask = (
            (price_data.index >= pd.Timestamp(window.test_start))
            & (price_data.index <= pd.Timestamp(window.test_end))
        )
        test_data = price_data[test_mask]

        # Create strategy with best params
        strategy = strategy_factory(params)

        # Run backtest on test data
        return self._run_backtest(
            price_data=test_data,
            strategy=strategy,
            symbols=symbols,
            initial_cash=initial_cash,
        )

    def _run_backtest(
        self,
        price_data: "pd.DataFrame",
        strategy: Any,
        symbols: List[str],
        initial_cash: float,
    ) -> Dict[str, float]:
        """Run a simple backtest and return metrics."""
        from gefion.backtest.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=initial_cash)
        equity_curve = []

        for idx in range(len(price_data)):
            current_date = price_data.index[idx].date()
            prices = {
                symbol: price_data[symbol].iloc[idx]
                for symbol in symbols
                if symbol in price_data.columns
            }

            # Get signals from strategy
            signals = strategy.generate_signals(current_date, prices, portfolio)

            # Execute signals
            for signal in signals:
                symbol = signal.get("symbol")
                action = signal.get("action")
                shares = signal.get("shares", 0)
                price = prices.get(symbol, 0)

                if action == "buy" and shares > 0:
                    try:
                        portfolio.buy(symbol, shares, price, current_date)
                    except ValueError:
                        pass  # Insufficient cash
                elif action == "sell" and shares > 0:
                    try:
                        portfolio.sell(symbol, shares, price, current_date)
                    except ValueError:
                        pass  # No position

            # Record equity
            equity = portfolio.calculate_equity(prices)
            equity_curve.append(equity)

        # Calculate metrics
        return self._calculate_metrics(equity_curve, initial_cash)

    def _calculate_metrics(
        self,
        equity_curve: List[float],
        initial_cash: float,
    ) -> Dict[str, float]:
        """Calculate performance metrics from equity curve."""
        if not equity_curve or len(equity_curve) < 2:
            return {
                "sharpe_ratio": 0.0,
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "calmar_ratio": 0.0,
            }

        # Total return
        final_equity = equity_curve[-1]
        total_return = (final_equity - initial_cash) / initial_cash

        # Daily returns
        returns = []
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] > 0:
                ret = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
                returns.append(ret)

        # Sharpe ratio (annualized, assuming 252 trading days)
        if returns:
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_return = variance ** 0.5

            if std_return > 0:
                sharpe_ratio = (avg_return / std_return) * (252 ** 0.5)
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0

        # Max drawdown
        peak = equity_curve[0]
        max_drawdown = 0.0
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak if peak > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Calmar ratio (annualized return / max drawdown)
        if max_drawdown > 0:
            annualized_return = (1 + total_return) ** (252 / len(equity_curve)) - 1
            calmar_ratio = annualized_return / max_drawdown
        else:
            calmar_ratio = 0.0

        return {
            "sharpe_ratio": sharpe_ratio,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "calmar_ratio": calmar_ratio,
        }

    def _calculate_aggregate_metrics(
        self,
        out_of_sample_metrics: List[Dict[str, float]],
    ) -> Dict[str, float]:
        """Calculate aggregate metrics across all out-of-sample periods."""
        if not out_of_sample_metrics:
            return {}

        aggregate = {}
        for key in out_of_sample_metrics[0].keys():
            values = [m.get(key, 0) for m in out_of_sample_metrics]
            aggregate[key] = sum(values) / len(values)

        return aggregate
