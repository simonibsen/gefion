#!/usr/bin/env python
"""
Momentum Strategy Backtest Example

This example demonstrates how to:
1. Load historical price data from the database
2. Configure and run a momentum trading strategy
3. Analyze backtest results
4. Compare performance to buy-and-hold benchmark

Usage:
    python examples/momentum_backtest.py --symbols AAPL,MSFT,GOOGL --start 2024-01-01 --end 2024-12-31
    python examples/momentum_backtest.py --help
"""
import argparse
from datetime import date
from typing import List, Optional

from gefion.backtest.data_loader import load_price_data_for_backtest
from gefion.backtest.engine import BacktestEngine
from gefion.strategies.momentum import MomentumStrategy
from gefion.config import load_settings


def parse_date(date_str: str) -> date:
    """Parse YYYY-MM-DD format date string."""
    year, month, day = map(int, date_str.split("-"))
    return date(year, month, day)


def run_momentum_backtest(
    symbols: Optional[List[str]] = None,
    exchange: Optional[str] = None,
    start_date: date = None,
    end_date: date = None,
    initial_cash: float = 100000.0,
    lookback_days: int = 20,
    top_n: int = 5,
    rebalance_days: int = 7,
    limit: Optional[int] = None,
) -> dict:
    """
    Run a momentum strategy backtest.

    Args:
        symbols: List of stock symbols to test
        exchange: Exchange name (alternative to symbols)
        start_date: Backtest start date
        end_date: Backtest end date
        initial_cash: Starting portfolio value
        lookback_days: Momentum calculation period
        top_n: Number of top momentum stocks to hold
        rebalance_days: Days between rebalancing
        limit: Maximum number of symbols (for exchange filtering)

    Returns:
        Dict with backtest results (trades, equity_curve, metrics)
    """
    # Load settings
    settings = load_settings()

    # Load historical price data
    print("Loading price data from database...")
    price_data = load_price_data_for_backtest(
        db_url=settings.database_url,
        symbols=symbols,
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    if not price_data:
        raise ValueError(
            "No price data found for specified parameters.\n"
            "Try: g2 data-update --exchange NASDAQ --limit 50 --timeframe full"
        )

    symbols_found = set(row["symbol"] for row in price_data)
    print(f"Loaded {len(price_data)} price records for {len(symbols_found)} symbols")
    print(f"Symbols: {', '.join(sorted(symbols_found))}")
    print()

    # Initialize strategy
    print("Initializing momentum strategy...")
    strategy = MomentumStrategy(
        lookback_days=lookback_days,
        top_n=top_n,
        rebalance_days=rebalance_days,
    )

    # Create wrapper function for backtest engine
    def strategy_fn(current_date, portfolio, prices):
        return strategy.generate_signals(
            current_date=current_date,
            portfolio=portfolio,
            price_data=prices,
            initial_cash=initial_cash,
        )

    # Run backtest
    print("Running backtest...")
    engine = BacktestEngine(
        price_data=price_data,
        strategy=strategy_fn,
        initial_cash=initial_cash,
        start_date=start_date,
        end_date=end_date,
    )

    results = engine.run()
    print("Backtest complete!")
    print()

    return results


def print_results(results: dict, initial_cash: float):
    """Print backtest results in a readable format."""
    metrics = results["metrics"]
    trades = results["trades"]
    equity_curve = results["equity_curve"]

    print("=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print()

    print("Performance Metrics:")
    print(f"  Initial Capital:  ${initial_cash:,.2f}")
    print(f"  Final Value:      ${equity_curve[-1]['equity']:,.2f}" if equity_curve else f"  Final Value:      ${initial_cash:,.2f}")
    print(f"  Total Return:     {metrics['total_return']:.2%}")
    print(f"  Sharpe Ratio:     {metrics['sharpe_ratio']:.3f}")
    print(f"  Max Drawdown:     {metrics['max_drawdown']:.2%}")
    print()

    print("Trading Activity:")
    print(f"  Total Trades:     {len(trades)}")
    if trades:
        buys = [t for t in trades if t["action"] == "buy"]
        sells = [t for t in trades if t["action"] == "sell"]
        print(f"  Buy Orders:       {len(buys)}")
        print(f"  Sell Orders:      {len(sells)}")

        symbols_traded = set(t["symbol"] for t in trades)
        print(f"  Symbols Traded:   {len(symbols_traded)}")
        print(f"                    {', '.join(sorted(symbols_traded))}")
    print()

    # Print first few trades as examples
    if trades:
        print("Sample Trades (first 5):")
        for i, trade in enumerate(trades[:5]):
            print(
                f"  {i+1}. {trade['date']} {trade['action']:4s} {trade['shares']:5.0f} {trade['symbol']:6s} "
                f"@ ${trade['price']:8.2f} = ${trade['amount']:10.2f}"
            )
        if len(trades) > 5:
            print(f"  ... and {len(trades) - 5} more trades")
        print()

    # Interpretation
    print("Interpretation:")
    if metrics["total_return"] > 0:
        print(f"  ✓ Strategy was profitable with {metrics['total_return']:.2%} return")
    else:
        print(f"  ✗ Strategy lost {-metrics['total_return']:.2%}")

    if metrics["sharpe_ratio"] > 1.0:
        print(f"  ✓ Good risk-adjusted returns (Sharpe: {metrics['sharpe_ratio']:.3f})")
    elif metrics["sharpe_ratio"] > 0:
        print(f"  ≈ Modest risk-adjusted returns (Sharpe: {metrics['sharpe_ratio']:.3f})")
    else:
        print(f"  ✗ Poor risk-adjusted returns (Sharpe: {metrics['sharpe_ratio']:.3f})")

    if metrics["max_drawdown"] > -0.20:
        print(f"  ✓ Low drawdown risk ({-metrics['max_drawdown']:.2%} max decline)")
    else:
        print(f"  ⚠ Significant drawdown risk ({-metrics['max_drawdown']:.2%} max decline)")

    print()
    print("=" * 60)


def main():
    """Main entry point for example script."""
    parser = argparse.ArgumentParser(
        description="Run a momentum strategy backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backtest specific symbols
  python examples/momentum_backtest.py --symbols AAPL,MSFT,GOOGL --start 2024-01-01 --end 2024-12-31

  # Backtest NASDAQ stocks
  python examples/momentum_backtest.py --exchange NASDAQ --limit 20 --start 2024-01-01 --end 2024-12-31

  # Custom strategy parameters
  python examples/momentum_backtest.py --symbols AAPL,MSFT --start 2024-01-01 --end 2024-12-31 \\
    --lookback-days 30 --top-n 1 --rebalance-days 14

  # Large initial capital
  python examples/momentum_backtest.py --symbols AAPL --start 2024-01-01 --end 2024-12-31 \\
    --initial-cash 1000000
        """,
    )

    # Data selection
    data_group = parser.add_mutually_exclusive_group(required=True)
    data_group.add_argument(
        "--symbols",
        type=str,
        help="Comma-separated list of symbols (e.g., AAPL,MSFT,GOOGL)",
    )
    data_group.add_argument(
        "--exchange", type=str, help="Exchange name (e.g., NASDAQ, NYSE)"
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of symbols to test (for exchange filtering)",
    )

    # Backtest period
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )

    # Portfolio parameters
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100000.0,
        help="Initial portfolio value (default: 100000)",
    )

    # Strategy parameters
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=20,
        help="Momentum lookback period in days (default: 20)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of top momentum stocks to hold (default: 5)",
    )
    parser.add_argument(
        "--rebalance-days",
        type=int,
        default=7,
        help="Days between rebalancing (default: 7)",
    )

    args = parser.parse_args()

    # Parse dates
    start_date = parse_date(args.start)
    end_date = parse_date(args.end)

    # Parse symbols
    symbols = args.symbols.split(",") if args.symbols else None

    # Run backtest
    try:
        results = run_momentum_backtest(
            symbols=symbols,
            exchange=args.exchange,
            start_date=start_date,
            end_date=end_date,
            initial_cash=args.initial_cash,
            lookback_days=args.lookback_days,
            top_n=args.top_n,
            rebalance_days=args.rebalance_days,
            limit=args.limit,
        )

        # Print results
        print_results(results, args.initial_cash)

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
