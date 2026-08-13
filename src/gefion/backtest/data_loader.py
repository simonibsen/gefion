"""
Data loading utilities for backtesting.

Loads historical price data from the database for use in backtesting.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg import sql

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)


# Day-over-day close ratio beyond which a move is not a market move (#262).
#
# The backtest prices on raw `close`, which is NOT split-adjusted, so a 1:20
# reverse split is indistinguishable from a 20x rally -- and a short held
# through one loses 1900%. Every account blowup in the epic #179 A/B was this:
# six runs, six blowups, each on a reverse-split date in a shorted name
# (ADIL 26.3x, MNTS 37.4x, FFAI 61.0x, SMX 18.3x).
#
# We cannot adjust correctly instead: `stock_ohlcv.split_coefficient` is 100%
# NULL (0 of 1,962,612 rows in 2023 -- no split is recorded anywhere) and
# `adjusted_close` is corrupt for exactly these serial reverse-splitters
# (SMX: close $0.129 vs adjusted_close $3.45e9; 2.2% of rows exceed 100x
# their close). Nor may we INFER the ratio from the jump: a genuine 20x move
# would then be silently rewritten as a split, which is the plausible-value
# substitution the failure-semantics rule exists to prevent.
#
# So the guard blocks rather than guesses. 4.0 sits well above real single-day
# equity moves and well below the smallest reverse split we observed.
IMPLAUSIBLE_MOVE_RATIO = 4.0


def resolve_backtest_universe(
    db_url: str,
    symbols: Optional[List[str]],
    universe: Optional[str],
) -> Dict[str, Any]:
    """Provenance stamp for a backtest's population (spec 015, issue #153).

    Explicit symbol lists bypass the universe gate (documented bypass, same
    as the dataset path) — they are their own universe, stamped "explicit".
    Otherwise the stamp names the resolved universe and its fingerprint,
    exactly the ``ml_datasets.universe`` pattern.
    """
    from gefion.universe import resolve_universe

    if symbols:
        return {"universe_name": "explicit", "universe_fingerprint": None}
    with psycopg.connect(db_url) as conn:
        return resolve_universe(conn, universe).provenance()


def filter_implausible_price_moves(
    price_data: List[Dict[str, Any]],
    max_ratio: float = IMPLAUSIBLE_MOVE_RATIO,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Drop symbols whose close series contains a move we cannot explain (#262).

    A day-over-day close ratio at or beyond ``max_ratio`` (or its reciprocal)
    is a data event, not a market event -- almost always an unrecorded reverse
    split. Since no split data exists to adjust with, the symbol is removed
    from the backtest entirely and named in a warning.

    Dropping the whole symbol rather than the offending bar is deliberate: a
    position marked at a stale price is worse than one never opened, and the
    prices on both sides of an unadjusted split are on different scales, so
    neither side is usable once a split sits between them.

    A non-positive or missing close is treated the same way. The ratio is then
    undefined, and undefined is not "no move" -- it blocks, exactly as an
    unparseable config does.

    Args:
        price_data: Rows of {symbol, date, close, ...}, any order.
        max_ratio: Blocking threshold, must be > 1.

    Returns:
        (kept_rows, dropped) where ``dropped`` maps symbol -> the first
        offending {date, ratio, prev_close, close}. Callers that report should
        surface ``dropped``; it is never silent.

    Raises:
        ValueError: if ``max_ratio`` <= 1, which would drop every symbol or
            none and leave the backtest with a guard that means nothing.
    """
    if max_ratio <= 1:
        raise ValueError(
            f"max_ratio must be > 1 to be meaningful, got {max_ratio!r}")

    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in price_data:
        by_symbol[row["symbol"]].append(row)

    dropped: Dict[str, Dict[str, Any]] = {}
    for symbol, rows in by_symbol.items():
        # Callers order by (date, symbol), so per-symbol order is not
        # guaranteed by list position -- sort explicitly or a jump gets
        # measured against the wrong bar.
        rows.sort(key=lambda r: r["date"])
        prev = None
        for row in rows:
            close = row["close"]
            if close is None or close <= 0:
                dropped[symbol] = {"date": row["date"], "ratio": None,
                                   "prev_close": prev, "close": close}
                break
            if prev is not None:
                ratio = close / prev
                if ratio >= max_ratio or ratio <= 1.0 / max_ratio:
                    dropped[symbol] = {"date": row["date"], "ratio": ratio,
                                       "prev_close": prev, "close": close}
                    break
            prev = close

    if dropped:
        for symbol, info in sorted(dropped.items()):
            ratio = info["ratio"]
            logger.warning(
                "backtest price integrity (#262): dropping %s — close moved "
                "%s -> %s on %s (%s), which is not a market move and no split "
                "data exists to adjust it; the symbol is excluded from this "
                "backtest",
                symbol, info["prev_close"], info["close"], info["date"],
                f"{ratio:.1f}x" if ratio is not None else "unusable close")

    kept = [row for row in price_data if row["symbol"] not in dropped]
    return kept, dropped


def load_price_data_for_backtest(
    db_url: str,
    symbols: Optional[List[str]] = None,
    exchange: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: Optional[int] = None,
    universe: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Load historical price data from database for backtesting.

    Args:
        db_url: Database connection URL
        symbols: List of symbols to load (optional; wins verbatim)
        exchange: Exchange name to filter by (optional, alternative to symbols)
        start_date: Start date for price data (optional)
        end_date: End date for price data (optional)
        limit: Maximum number of symbols to load (optional, for testing)
        universe: Modeling universe for the population (spec 015);
            None = default universe, 'all' = unfiltered

    Returns:
        List of price records with symbol, date, close, open, high, low, volume
    """
    from gefion.universe import resolve_universe, universe_exclusion_clause

    with create_span("backtest.data_loader.load_price_data_for_backtest", symbol_count=len(symbols) if symbols else 0) as span:
        with psycopg.connect(db_url) as conn:
            resolved = resolve_universe(conn, universe)
            # Membership is date-aware: bind the gate to each bar's own date
            uni_clause, uni_params = universe_exclusion_clause(
                resolved.universe_id, "o.date", "o.data_id")

            # Build query
            symbol_where_clauses = []  # For filtering stocks
            ohlcv_where_clauses = []   # For filtering price data
            params = []

            # Filter by symbols
            # Note: exchange filtering is not enforced yet — stocks.exchange is
            # only populated by fundamentals-update and most rows are still
            # NULL, so filtering would silently drop symbols. When exchange is
            # specified, we fall back to just using the limit parameter.
            if symbols:
                symbol_where_clauses.append("s.symbol = ANY(%s)")
                params.append(symbols)

            # Apply symbol limit if specified
            if limit and not symbols:
                # Create subquery to get limited set of stock IDs
                # Note: exchange is not enforced here (see note above), so we
                # just use the limit to get top N active stocks with data
                symbol_id_subquery = f"""
                    (SELECT s.id FROM stocks s
                     JOIN stock_ohlcv o ON s.id = o.data_id
                     WHERE s.status = 'Active'
                     GROUP BY s.id
                     ORDER BY COUNT(*) DESC
                     LIMIT {limit})
                """
                # Replace symbol filters with IN clause
                symbol_where_clauses = [f"s.id IN {symbol_id_subquery}"]

            # Add active status filter if not using limit subquery
            elif not limit:
                symbol_where_clauses.append("s.status = 'Active'")

            # Filter by date range (applies to OHLCV data)
            if start_date:
                ohlcv_where_clauses.append("o.date >= %s")
                params.append(start_date)
            if end_date:
                ohlcv_where_clauses.append("o.date <= %s")
                params.append(end_date)

            # Universe gate (spec 015) — explicit symbols win verbatim
            # (documented bypass, same as the dataset path)
            if not symbols:
                ohlcv_where_clauses.append(uni_clause)
                params.extend(uni_params)

            # Combine all WHERE clauses
            all_where_clauses = symbol_where_clauses + ohlcv_where_clauses
            where_sql = " AND ".join(all_where_clauses) if all_where_clauses else "1=1"

            query = f"""
                SELECT
                    s.symbol,
                    o.date,
                    o.close,
                    o.open,
                    o.high,
                    o.low,
                    o.volume
                FROM stocks s
                JOIN stock_ohlcv o ON s.id = o.data_id
                WHERE {where_sql}
                ORDER BY o.date ASC, s.symbol ASC
            """

            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

                # Convert to list of dicts
                price_data = [
                    {
                        "symbol": row[0],
                        "date": row[1],
                        "close": float(row[2]) if row[2] is not None else None,
                        "open": float(row[3]) if row[3] is not None else None,
                        "high": float(row[4]) if row[4] is not None else None,
                        "low": float(row[5]) if row[5] is not None else None,
                        "volume": int(row[6]) if row[6] is not None else None,
                    }
                    for row in rows
                ]

                # Price integrity gate (#262). This function is the single
                # door price data enters a backtest through, so the guard
                # belongs here rather than at each call site.
                raw_row_count = len(price_data)
                price_data, dropped = filter_implausible_price_moves(price_data)

                set_attributes(span, row_count=len(price_data),
                               table="stock_ohlcv",
                               raw_row_count=raw_row_count,
                               dropped_symbol_count=len(dropped))
                return price_data


def get_available_symbols(
    db_url: str,
    exchange: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """
    Get list of available symbols in the database.

    Args:
        db_url: Database connection URL
        exchange: Filter by exchange (optional, not enforced yet — stocks.exchange is sparsely populated)
        limit: Maximum number of symbols to return (optional)

    Returns:
        List of symbol strings
    """
    with create_span("backtest.data_loader.get_available_symbols") as span:
        with psycopg.connect(db_url) as conn:
            # Note: exchange filtering not enforced yet (stocks.exchange is
            # sparsely populated). We just return active stocks with sufficient data
            from gefion.universe import resolve_universe, universe_exclusion_clause
            resolved = resolve_universe(conn, None)
            uni_clause, uni_params = universe_exclusion_clause(
                resolved.universe_id, "CURRENT_DATE", "s.id")
            where_clause = f"WHERE s.status = 'Active' AND {uni_clause}"
            params: List[Any] = list(uni_params)

            limit_clause = f"LIMIT {limit}" if limit else ""

            query = f"""
                SELECT s.symbol
                FROM stocks s
                JOIN stock_ohlcv o ON s.id = o.data_id
                {where_clause}
                GROUP BY s.symbol
                HAVING COUNT(*) > 50
                ORDER BY COUNT(*) DESC
                {limit_clause}
            """

            with conn.cursor() as cur:
                cur.execute(query, params)
                result = [row[0] for row in cur.fetchall()]
                set_attributes(span, result_count=len(result))
                return result
