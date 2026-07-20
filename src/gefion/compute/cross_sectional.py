"""
Cross-sectional feature computation.

Cross-sectional features compare stocks to their peers at the same point in time,
as opposed to time-series features which compare a stock to its own history.

Examples:
- return_vs_market: Stock return minus market average return
- market_rank: Percentile ranking based on returns
- sector_rank: Percentile ranking within sector peers
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import psycopg

from gefion.observability import create_span, set_attributes


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


def compute_rankings_by_group(
    data: List[Dict[str, Any]],
    comparison_group: str = "market",
    value_key: str = "value"
) -> List[Dict[str, Any]]:
    """
    Compute rankings for stocks within a comparison group.

    Args:
        data: List of dicts with symbol, data_id, value, and optionally sector/industry
        comparison_group: One of:
            - 'market': rank against all stocks
            - 'sector:X': rank against stocks in sector X
            - 'industry:X': rank against stocks in industry X
        value_key: Key to use for ranking values (default: "value")

    Returns:
        List of dicts with symbol, data_id, value, rank, percentile, comparison_group

    Example:
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECHNOLOGY"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECHNOLOGY"},
            {"symbol": "JPM", "data_id": 3, "value": 60.0, "sector": "FINANCE"},
        ]

        # Market ranking (all stocks)
        results = compute_rankings_by_group(data, comparison_group="market")
        # Returns all 3 stocks ranked 1-3

        # Sector ranking (only TECHNOLOGY)
        results = compute_rankings_by_group(data, comparison_group="sector:TECHNOLOGY")
        # Returns only AAPL (rank 1) and MSFT (rank 2)
    """
    if not data:
        return []

    # Filter data based on comparison group
    if comparison_group == "market":
        filtered = data
    elif comparison_group.startswith("sector:"):
        target_sector = comparison_group.split(":", 1)[1]
        filtered = [d for d in data if d.get("sector") == target_sector]
    elif comparison_group.startswith("industry:"):
        target_industry = comparison_group.split(":", 1)[1]
        filtered = [d for d in data if d.get("industry") == target_industry]
    else:
        # Unknown comparison group type
        filtered = data

    if not filtered:
        return []

    # Sort by value (descending - highest value = rank 1)
    sorted_data = sorted(filtered, key=lambda x: x.get(value_key, 0), reverse=True)

    n = len(sorted_data)
    results = []

    # Track previous value for handling ties
    prev_value = None
    prev_rank = 0

    for i, row in enumerate(sorted_data):
        current_value = row.get(value_key, 0)

        # Handle ties: same value = same rank
        if current_value == prev_value:
            rank = prev_rank
        else:
            rank = i + 1
            prev_rank = rank
            prev_value = current_value

        # Calculate percentile (0 = worst, 1 = best)
        if n == 1:
            percentile = 1.0
        else:
            # Use position in sorted list for percentile
            percentile = (n - (i + 1)) / (n - 1)

        results.append({
            "symbol": row["symbol"],
            "data_id": row["data_id"],
            "value": row.get(value_key),
            "rank": rank,
            "percentile": percentile,
            "comparison_group": comparison_group,
        })

    return results


def compute_all_rankings(
    data: List[Dict[str, Any]],
    value_key: str = "value",
    include_market: bool = True,
    include_sectors: bool = True,
    include_industries: bool = False
) -> List[Dict[str, Any]]:
    """
    Compute rankings for all comparison groups (market + sectors + industries).

    Args:
        data: List of dicts with symbol, data_id, value, sector, industry
        value_key: Key to use for ranking values
        include_market: Include market-wide ranking
        include_sectors: Include per-sector rankings
        include_industries: Include per-industry rankings

    Returns:
        List of all ranking results across all comparison groups

    Example:
        data = [
            {"symbol": "AAPL", "data_id": 1, "value": 100.0, "sector": "TECH"},
            {"symbol": "MSFT", "data_id": 2, "value": 80.0, "sector": "TECH"},
            {"symbol": "JPM", "data_id": 3, "value": 60.0, "sector": "FINANCE"},
        ]

        results = compute_all_rankings(data)
        # Returns 6 results:
        # - 3 market rankings (all stocks)
        # - 2 sector:TECH rankings
        # - 1 sector:FINANCE ranking
    """
    all_results = []

    # Market ranking
    if include_market:
        market_results = compute_rankings_by_group(data, "market", value_key)
        all_results.extend(market_results)

    # Sector rankings
    if include_sectors:
        sectors = {d.get("sector") for d in data if d.get("sector")}
        for sector in sectors:
            sector_results = compute_rankings_by_group(
                data, f"sector:{sector}", value_key
            )
            all_results.extend(sector_results)

    # Industry rankings
    if include_industries:
        industries = {d.get("industry") for d in data if d.get("industry")}
        for industry in industries:
            industry_results = compute_rankings_by_group(
                data, f"industry:{industry}", value_key
            )
            all_results.extend(industry_results)

    return all_results


def fetch_feature_with_sectors(
    conn: psycopg.Connection,
    feature_name: str,
    target_date: Optional[date] = None,
    universe: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch feature values joined with stock sector/industry data.

    Args:
        conn: Database connection
        feature_name: Name of the feature to fetch (e.g., 'indicator_rsi_14')
        target_date: Date to fetch data for (defaults to latest available)
        universe: Modeling universe for the ranking population (spec 015);
            None = default universe, 'all' = unfiltered

    Returns:
        List of dicts with symbol, data_id, value, sector, industry

    Example:
        data = fetch_feature_with_sectors(conn, "indicator_rsi_14")
        # [
        #     {"symbol": "AAPL", "data_id": 1, "value": 65.2, "sector": "TECHNOLOGY", ...},
        #     {"symbol": "MSFT", "data_id": 2, "value": 58.1, "sector": "TECHNOLOGY", ...},
        # ]
    """
    with create_span("compute.cross_sectional.fetch_feature_with_sectors", feature_name=feature_name) as span:
        with conn.cursor() as cur:
            # Get feature_id
            cur.execute(
                "SELECT id FROM feature_definitions WHERE name = %s",
                (feature_name,)
            )
            row = cur.fetchone()
            if not row:
                set_attributes(span, result_count=0)
                return []
            feature_id = row[0]

            # Determine target date
            if target_date is None:
                cur.execute(
                    "SELECT MAX(date) FROM computed_features WHERE feature_id = %s",
                    (feature_id,)
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    set_attributes(span, result_count=0)
                    return []
                target_date = row[0]

            # Fetch feature values with stock sector/industry. The ranking
            # population routes through the universe gate (spec 015); the
            # legacy Inactive-status exclusion stays as belt-and-braces.
            from gefion.universe import (resolve_universe,
                                         universe_exclusion_clause)
            resolved = resolve_universe(conn, universe)
            uni_clause, uni_params = universe_exclusion_clause(
                resolved.universe_id, "cf.date", "cf.data_id")
            cur.execute(
                f"""
                SELECT
                    s.symbol,
                    s.id as data_id,
                    cf.value,
                    s.sector,
                    s.industry,
                    cf.date
                FROM computed_features cf
                JOIN stocks s ON cf.data_id = s.id
                WHERE cf.feature_id = %s
                  AND cf.date = %s
                  AND s.status IS DISTINCT FROM 'Inactive'
                  AND {uni_clause}
                ORDER BY s.symbol
                """,
                (feature_id, target_date, *uni_params)
            )

            results = []
            for row in cur.fetchall():
                results.append({
                    "symbol": row[0],
                    "data_id": row[1],
                    "value": float(row[2]) if row[2] is not None else None,
                    "sector": row[3],
                    "industry": row[4],
                    "date": row[5],
                })

            set_attributes(span, result_count=len(results))
            return results


def store_cross_sectional_rankings(
    conn: psycopg.Connection,
    rankings: List[Dict[str, Any]],
    feature_name: str,
    target_date: date,
) -> int:
    """
    Store cross-sectional rankings to the database.

    Args:
        conn: Database connection
        rankings: List of ranking results from compute_rankings_by_group
        feature_name: Name of the feature being ranked
        target_date: Date of the rankings

    Returns:
        Number of rows inserted
    """
    with create_span("compute.cross_sectional.store_cross_sectional_rankings", feature_name=feature_name, target_date=str(target_date)) as span:
        if not rankings:
            set_attributes(span, row_count=0)
            return 0

        with conn.cursor() as cur:
            # Batch insert
            values = []
            for r in rankings:
                values.append((
                    r["data_id"],
                    target_date,
                    feature_name,
                    r["comparison_group"],
                    r["value"],
                    r["rank"],
                    r["percentile"],
                ))

            # Use ON CONFLICT to handle updates
            from psycopg import sql
            cur.executemany(
                """
                INSERT INTO cross_sectional_features
                    (data_id, date, feature_name, comparison_group, value, rank, percentile)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (data_id, date, feature_name, comparison_group)
                DO UPDATE SET value = EXCLUDED.value, rank = EXCLUDED.rank, percentile = EXCLUDED.percentile
                """,
                values
            )

        conn.commit()
        set_attributes(span, row_count=len(rankings))
        return len(rankings)


def compute_and_store_rankings(
    conn: psycopg.Connection,
    feature_name: str,
    target_date: Optional[date] = None,
    include_market: bool = True,
    include_sectors: bool = True,
    include_industries: bool = False,
) -> Dict[str, Any]:
    """
    Compute cross-sectional rankings and store to database.

    This is the main entry point for computing sector/market rankings.

    Args:
        conn: Database connection
        feature_name: Feature to rank (e.g., 'indicator_rsi_14')
        target_date: Date to compute rankings for
        include_market: Include market-wide rankings
        include_sectors: Include per-sector rankings
        include_industries: Include per-industry rankings

    Returns:
        Dict with status, counts, and any errors

    Example:
        result = compute_and_store_rankings(conn, "indicator_rsi_14")
        # {"success": True, "total_rankings": 15, "groups": ["market", "sector:TECHNOLOGY", ...]}
    """
    with create_span("compute.cross_sectional.compute_and_store_rankings", feature_name=feature_name) as span:
        # Fetch feature data with sectors
        data = fetch_feature_with_sectors(conn, feature_name, target_date)

        if not data:
            set_attributes(span, success=False, total_rankings=0)
            return {
                "success": False,
                "error": f"No data found for feature '{feature_name}'",
                "total_rankings": 0,
            }

        # Use actual date from data
        actual_date = data[0]["date"]

        # Compute all rankings
        rankings = compute_all_rankings(
            data,
            value_key="value",
            include_market=include_market,
            include_sectors=include_sectors,
            include_industries=include_industries,
        )

        # Store to database
        stored = store_cross_sectional_rankings(conn, rankings, feature_name, actual_date)

        # Get unique comparison groups
        groups = list({r["comparison_group"] for r in rankings})

        set_attributes(span, success=True, total_rankings=stored, stocks_count=len(data))
        return {
            "success": True,
            "feature_name": feature_name,
            "date": str(actual_date),
            "total_rankings": stored,
            "groups": sorted(groups),
            "stocks_count": len(data),
        }


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
