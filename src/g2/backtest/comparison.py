"""
Strategy comparison framework.

Compare multiple trading strategies side-by-side on the same data.
Uses the strategy dispatcher for dynamic strategy loading.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Tuple, Type

from g2.backtest.engine import BacktestEngine
from g2.backtest.metrics import calculate_metrics_extended
from g2.strategies.dispatcher import BUILTIN_STRATEGIES, _load_from_module


def _get_available_strategies() -> Dict[str, Type]:
    """
    Build map of strategy names to classes from dispatcher.

    Lazily loads strategy classes from BUILTIN_STRATEGIES.
    """
    strategies = {}
    for name, info in BUILTIN_STRATEGIES.items():
        strategy_class = _load_from_module(info["module_path"], info["class_name"])
        if strategy_class is not None:
            strategies[name] = strategy_class
    return strategies


# Map of strategy names to strategy classes (lazy-loaded from dispatcher)
AVAILABLE_STRATEGIES: Dict[str, Type] = _get_available_strategies()


def compare_strategies(
    strategies: List[str],
    price_data: List[Dict[str, Any]],
    initial_capital: float = 100000.0,
    strategy_params: Dict[str, Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Compare multiple strategies on the same price data.

    Args:
        strategies: List of strategy names to compare
        price_data: List of OHLCV price records
        initial_capital: Starting capital for each backtest
        strategy_params: Optional dict of strategy-specific parameters

    Returns:
        Dict mapping strategy name -> metrics dict

    Raises:
        ValueError: If an unknown strategy name is provided
    """
    if strategy_params is None:
        strategy_params = {}

    # Validate strategy names
    for name in strategies:
        if name not in AVAILABLE_STRATEGIES:
            raise ValueError(
                f"Unknown strategy: '{name}'. "
                f"Available: {list(AVAILABLE_STRATEGIES.keys())}"
            )

    # Determine date range from price data
    if not price_data:
        return {name: _empty_metrics() for name in strategies}

    dates = sorted(set(p["date"] for p in price_data))
    start_date = min(dates)
    end_date = max(dates)

    results = {}

    for strategy_name in strategies:
        # Create strategy instance with optional params
        params = strategy_params.get(strategy_name, {})
        strategy_class = AVAILABLE_STRATEGIES[strategy_name]
        strategy_instance = strategy_class(**params)

        # Create strategy function for BacktestEngine
        # Note: BacktestEngine provides:
        #   - prices as Dict[str, List[Dict]] (symbol -> list)
        #   - portfolio as Portfolio object (has .positions dict and .cash)
        # Strategies have different expectations:
        #   - MomentumStrategy: expects dict format prices, Portfolio object
        #   - Others: expect flat list prices, dict for portfolio
        def make_strategy_fn(strat, cash, strat_name):
            def strategy_fn(current_date, portfolio, prices):
                # MomentumStrategy expects dict format, others expect flat list
                if strat_name == "momentum":
                    price_data = prices  # Keep dict format
                    port_data = portfolio  # Keep Portfolio object
                else:
                    price_data = _dict_to_flat_prices(prices)
                    port_data = portfolio.positions  # Convert to dict
                return strat.generate_signals(current_date, port_data, price_data, cash)
            return strategy_fn

        strategy_fn = make_strategy_fn(strategy_instance, initial_capital, strategy_name)

        # Run backtest
        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy_fn,
            initial_cash=initial_capital,
            start_date=start_date,
            end_date=end_date,
        )

        backtest_results = engine.run()

        # Calculate extended metrics
        equity_curve = backtest_results.get("equity_curve", [])
        trades = backtest_results.get("trades", [])

        # Convert trades to PnL format for trade metrics
        trades_with_pnl = _extract_trade_pnl(trades, price_data)

        metrics = calculate_metrics_extended(
            equity_curve=equity_curve,
            trades=trades_with_pnl,
            initial_capital=initial_capital,
        )

        results[strategy_name] = metrics

    return results


def rank_strategies(
    comparison: Dict[str, Dict[str, float]],
    metric: str = "sharpe_ratio",
    ascending: bool = False,
) -> List[Tuple[str, float]]:
    """
    Rank strategies by a specified metric.

    Args:
        comparison: Dict mapping strategy name -> metrics dict
        metric: Metric name to rank by
        ascending: If True, sort ascending (lower is better)

    Returns:
        List of (strategy_name, metric_value) tuples, sorted by metric
    """
    ranked = []

    for name, metrics in comparison.items():
        value = metrics.get(metric, 0.0)
        ranked.append((name, value))

    # Sort by metric value
    ranked.sort(key=lambda x: x[1], reverse=not ascending)

    return ranked


def format_comparison_table(
    comparison: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Format comparison results for table display.

    Args:
        comparison: Dict mapping strategy name -> metrics dict

    Returns:
        List of row dicts with formatted values
    """
    rows = []

    for name, metrics in comparison.items():
        row = {
            "strategy": name,
            "return_pct": f"{metrics.get('total_return', 0) * 100:.1f}%",
            "sharpe": f"{metrics.get('sharpe_ratio', 0):.2f}",
            "sortino": f"{metrics.get('sortino_ratio', 0):.2f}",
            "calmar": f"{metrics.get('calmar_ratio', 0):.2f}",
            "max_dd": f"{metrics.get('max_drawdown', 0) * 100:.1f}%",
            "win_rate": f"{metrics.get('win_rate', 0) * 100:.0f}%",
            "profit_factor": f"{metrics.get('profit_factor', 0):.2f}",
            "trades": str(metrics.get("total_trades", 0)),
        }
        rows.append(row)

    return rows


def _dict_to_flat_prices(
    prices_dict: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Convert prices from dict format to flat list format.

    BacktestEngine provides: {symbol: [{date, close, ...}, ...], ...}
    Strategies expect: [{symbol, date, close, ...}, ...]

    Args:
        prices_dict: Dict mapping symbol -> list of price records

    Returns:
        Flat list of price records with 'symbol' field added
    """
    flat = []
    for symbol, records in prices_dict.items():
        for record in records:
            flat_record = {**record}
            if "symbol" not in flat_record:
                flat_record["symbol"] = symbol
            flat.append(flat_record)
    return flat


def _empty_metrics() -> Dict[str, Any]:
    """Return empty metrics dict."""
    return {
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_win_loss_ratio": 0.0,
        "total_trades": 0,
        "num_trades": 0,
    }


def _extract_trade_pnl(
    trades: List[Dict[str, Any]],
    price_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Extract PnL for each trade from trade log.

    Args:
        trades: List of trade records from backtest
        price_data: Price data used for the backtest

    Returns:
        List of trades with 'pnl' field
    """
    trades_with_pnl = []

    # Group trades by symbol to match buys with sells
    symbol_trades: Dict[str, List[Dict[str, Any]]] = {}

    for trade in trades:
        symbol = trade.get("symbol", "")
        if symbol not in symbol_trades:
            symbol_trades[symbol] = []
        symbol_trades[symbol].append(trade)

    # Match buys with sells to calculate PnL
    for symbol, symbol_trade_list in symbol_trades.items():
        open_position = None

        for trade in symbol_trade_list:
            action = trade.get("action", "")

            if action == "buy":
                # Open new position or add to existing
                if open_position is None:
                    open_position = {
                        "shares": trade.get("shares", 0),
                        "price": trade.get("price", 0),
                    }
                else:
                    # Average in
                    total_shares = open_position["shares"] + trade.get("shares", 0)
                    if total_shares > 0:
                        total_cost = (
                            open_position["shares"] * open_position["price"]
                            + trade.get("shares", 0) * trade.get("price", 0)
                        )
                        open_position["shares"] = total_shares
                        open_position["price"] = total_cost / total_shares

            elif action == "sell" and open_position is not None:
                # Close position and calculate PnL
                sell_shares = trade.get("shares", 0)
                sell_price = trade.get("price", 0)
                buy_price = open_position["price"]

                pnl = (sell_price - buy_price) * sell_shares

                trades_with_pnl.append({
                    "symbol": symbol,
                    "pnl": pnl,
                })

                # Update or close position
                open_position["shares"] -= sell_shares
                if open_position["shares"] <= 0:
                    open_position = None

    return trades_with_pnl
