"""
Cross-sectional feature computation.

Cross-sectional features compare stocks to their peers at the same point in time,
as opposed to time-series features which compare a stock to its own history.

Examples:
- return_vs_market: Stock return minus market average return
- market_rank: Percentile ranking based on returns
"""
from __future__ import annotations

from typing import Any, Dict, List


def compute_return_vs_market(price_data: List[Dict[str, Any]], date: str) -> List[Dict[str, Any]]:
    """
    Compute stock returns relative to market average.

    Args:
        price_data: List of price records with symbol, date, close
        date: Target date for computing market-relative returns

    Returns:
        List of dicts with symbol, return_vs_market

    Example:
        price_data = [
            {"symbol": "AAPL", "date": "2024-01-01", "close": 100.0},
            {"symbol": "AAPL", "date": "2024-01-02", "close": 105.0},  # +5%
            {"symbol": "MSFT", "date": "2024-01-01", "close": 200.0},
            {"symbol": "MSFT", "date": "2024-01-02", "close": 202.0},  # +1%
        ]

        results = compute_return_vs_market(price_data, date="2024-01-02")
        # Market avg = (5 + 1) / 2 = 3%
        # AAPL: 5 - 3 = 2% above market
        # MSFT: 1 - 3 = -2% below market
    """
    # Group data by symbol
    by_symbol: Dict[str, Dict[str, float]] = {}
    for row in price_data:
        symbol = row["symbol"]
        row_date = row["date"]
        close = float(row["close"])

        if symbol not in by_symbol:
            by_symbol[symbol] = {}
        by_symbol[symbol][row_date] = close

    # Calculate returns for each stock on target date
    stock_returns = []
    for symbol, prices in by_symbol.items():
        if date not in prices:
            continue

        # Find previous date
        dates = sorted([d for d in prices.keys() if d < date])
        if not dates:
            continue

        prev_date = dates[-1]
        prev_close = prices[prev_date]
        curr_close = prices[date]

        # Calculate return as percentage
        ret = ((curr_close - prev_close) / prev_close) * 100

        stock_returns.append({"symbol": symbol, "return": ret})

    if not stock_returns:
        return []

    # Calculate market average return
    market_avg = sum(r["return"] for r in stock_returns) / len(stock_returns)

    # Calculate market-relative returns
    results = []
    for stock in stock_returns:
        return_vs_market = stock["return"] - market_avg
        results.append({"symbol": stock["symbol"], "return_vs_market": return_vs_market})

    return results


def compute_market_rankings(returns_data: List[Dict[str, Any]], date: str) -> List[Dict[str, Any]]:
    """
    Compute market rankings based on returns.

    Args:
        returns_data: List of dicts with symbol, date, return
        date: Target date for rankings

    Returns:
        List of dicts with symbol, rank, percentile

    Example:
        returns_data = [
            {"symbol": "AAPL", "date": "2024-01-02", "return": 0.05},  # Rank 1
            {"symbol": "MSFT", "date": "2024-01-02", "return": 0.01},  # Rank 2
            {"symbol": "GOOGL", "date": "2024-01-02", "return": -0.02}, # Rank 3
        ]

        results = compute_market_rankings(returns_data, date="2024-01-02")
        # [
        #     {"symbol": "AAPL", "rank": 1, "percentile": 1.0},
        #     {"symbol": "MSFT", "rank": 2, "percentile": 0.5},
        #     {"symbol": "GOOGL", "rank": 3, "percentile": 0.0},
        # ]
    """
    # Filter to target date
    filtered = [r for r in returns_data if r["date"] == date]
    if not filtered:
        return []

    # Sort by return (descending)
    sorted_data = sorted(filtered, key=lambda x: x["return"], reverse=True)

    # Assign ranks and percentiles
    n = len(sorted_data)
    results = []

    for i, row in enumerate(sorted_data):
        rank = i + 1

        # Calculate percentile (0 = worst, 1 = best)
        if n == 1:
            percentile = 1.0
        else:
            percentile = (n - rank) / (n - 1)

        results.append({"symbol": row["symbol"], "rank": rank, "percentile": percentile})

    return results


def compute_percentiles(data: List[Dict[str, Any]], value_key: str = "value") -> List[Dict[str, Any]]:
    """
    Compute percentile rankings for a list of values.

    Args:
        data: List of dicts with symbol and value_key
        value_key: Key to use for values (default: "value")

    Returns:
        List of dicts with symbol, percentile (0-1 range, 1 = highest)

    Example:
        data = [
            {"symbol": "A", "value": 10.0},
            {"symbol": "B", "value": 20.0},
            {"symbol": "C", "value": 30.0},
        ]

        results = compute_percentiles(data)
        # [
        #     {"symbol": "A", "percentile": 0.0},  # Lowest
        #     {"symbol": "B", "percentile": 0.5},  # Middle
        #     {"symbol": "C", "percentile": 1.0},  # Highest
        # ]
    """
    if not data:
        return []

    # Sort by value (descending)
    sorted_data = sorted(data, key=lambda x: x[value_key], reverse=True)

    n = len(sorted_data)
    results = []

    for i, row in enumerate(sorted_data):
        rank = i + 1

        # Calculate percentile (0 = worst, 1 = best)
        if n == 1:
            percentile = 1.0
        else:
            percentile = (n - rank) / (n - 1)

        result = {"symbol": row["symbol"], "percentile": percentile}
        # Copy other fields
        for key, value in row.items():
            if key != "symbol":
                result[key] = value

        results.append(result)

    return results
