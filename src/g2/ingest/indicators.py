from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import time
import psycopg
from psycopg import errors
from datetime import date
from datetime import datetime

from g2.alphavantage.client import AlphaVantageClient
from g2.alphavantage.catalog import parse_daily_adjusted
from g2.alphavantage import indicators as indicator_parsers
from g2.db import schema
from g2.db.ingest import (
    upsert_stock,
    decide_outputsize,
    latest_indicator_date,
    ensure_indicator_feature_definitions,
    insert_computed_features,
    insert_stock_ohlcv,
)
from g2.utils.progress import ProgressReporter
from g2.indicators.local import compute_indicators as compute_local
from g2.features.dispatcher import compute_features


def compute_indicators_via_dispatcher(
    conn: psycopg.Connection,
    data_id: int,
    incremental: bool = True,
    update_existing: bool = False,
    feature_batch_size: int = 2000,
) -> int:
    """
    Compute indicators for a stock using the generic dispatcher.

    This is the new dispatcher-based approach.  Feature definitions
    must already exist in the database.

    Args:
        conn: Database connection
        data_id: Stock data_id
        incremental: Only compute new dates
        update_existing: Update existing rows on conflict

    Returns:
        Number of rows inserted

    Example:
        >>> with psycopg.connect(db_url) as conn:
        ...     # Ensure feature definitions exist
        ...     ensure_indicator_feature_definitions(conn, ['rsi', 'macd'])
        ...     # Compute using dispatcher
        ...     inserted = compute_indicators_via_dispatcher(conn, data_id=123)
    """
    result = compute_features(
        conn,
        data_id=data_id,
        function_names=['indicator'],
        incremental=incremental,
        update_existing=update_existing,
        feature_batch_size=feature_batch_size,
    )

    return result.get('summary', {}).get('total_inserted', 0)


INDICATOR_FUNCTIONS = {
    "rsi": (
        "RSI",
        indicator_parsers.parse_rsi,
        {"interval": "daily", "time_period": "14", "series_type": "close"},
    ),
    "macd": (
        "MACD",
        indicator_parsers.parse_macd,
        {
            "interval": "daily",
            "series_type": "close",
            "fastperiod": "12",
            "slowperiod": "26",
            "signalperiod": "9",
        },
    ),
    "sma20": ("SMA", lambda p: indicator_parsers.parse_sma(p, 20), {"interval": "daily", "time_period": "20", "series_type": "close"}),
    "bbands": (
        "BBANDS",
        indicator_parsers.parse_bbands,
        {"interval": "daily", "time_period": "20", "series_type": "close", "nbdevup": "2", "nbdevdn": "2", "matype": "0"},
    ),
    "adx": ("ADX", indicator_parsers.parse_adx, {"interval": "daily", "time_period": "14"}),
    "stoch": (
        "STOCH",
        indicator_parsers.parse_stoch,
        {"interval": "daily", "fastkperiod": "14", "slowkperiod": "3", "slowdperiod": "3", "slowkmatype": "1", "slowdmatype": "1"},
    ),
    "sma50": ("SMA", lambda p: indicator_parsers.parse_sma(p, 50), {"interval": "daily", "time_period": "50", "series_type": "close"}),
    "sma200": ("SMA", lambda p: indicator_parsers.parse_sma(p, 200), {"interval": "daily", "time_period": "200", "series_type": "close"}),
    "ema12": ("EMA", lambda p: indicator_parsers.parse_ema(p, 12), {"interval": "daily", "time_period": "12", "series_type": "close"}),
    "ema26": ("EMA", lambda p: indicator_parsers.parse_ema(p, 26), {"interval": "daily", "time_period": "26", "series_type": "close"}),
    "psar": ("SAR", indicator_parsers.parse_psar, {"interval": "daily", "acceleration": "0.02", "maximum": "0.2"}),
}


def fetch_indicator_payload(
    client: AlphaVantageClient,
    function: str,
    symbol: str,
    outputsize: str = "compact",
    extra_params: Optional[Dict[str, str]] = None,
) -> Mapping[str, object]:
    # Many indicator endpoints accept interval/outputsize/time_period; we keep it simple with default args
    params = {"symbol": symbol, "outputsize": outputsize}
    if extra_params:
        params.update(extra_params)
    return client.get(function, **params)


def merge_indicator_rows(rows_list: Iterable[List[Dict[str, object]]]) -> List[Dict[str, object]]:
    merged: Dict[str, Dict[str, object]] = {}
    for rows in rows_list:
        for row in rows:
            date = row["date"]
            merged.setdefault(date, {"date": date}).update({k: v for k, v in row.items() if k != "date"})
    return list(merged.values())


def _max_date(rows: List[Dict[str, object]]) -> Optional[date]:
    def to_date(val) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        try:
            return datetime.fromisoformat(str(val)).date()
        except Exception:
            return None

    dates = [to_date(r.get("date")) for r in rows]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


def ingest_indicators_for_symbols(
    db_url: str,
    client: AlphaVantageClient,
    symbols: Sequence[str],
    indicators: Sequence[str],
    timeframe: str = "auto",
    update_existing: bool = False,
    compute_locally: bool = True,
    fetch_workers: int = 4,
    writer_workers: int = 2,
    refresh: bool = False,
    batch_size: int = 200,
    progress: Optional[ProgressReporter] = None,
    use_prepared_statements: bool = True,
) -> int:
    """
    Ingest indicators for symbols.

    Args:
        use_prepared_statements: Enable prepared statement optimization (10-30% speedup)
    """
    inserted_total = 0
    # Bounded queue prevents memory exhaustion when fetchers outpace writers
    work_q: queue.Queue[Dict[str, object]] = queue.Queue(maxsize=200)
    writer_done = object()
    fetch_completed = 0
    cache: Dict[tuple, Mapping[str, object]] = {}
    latest_cache: Dict[str, date] = {}

    # Initialize connection pool with prepared statement support for better performance
    from g2.db import pool as db_pool
    if use_prepared_statements:
        # Calculate appropriate pool size based on worker counts
        min_size = max(2, writer_workers)
        max_size = fetch_workers + writer_workers + 2  # Extra for main thread operations
        db_pool.init_pool(db_url, min_size=min_size, max_size=max_size, prepare_statements=True)

    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            schema.create_stocks_table(conn)
            schema.migrate_stock_tables_to_data_id(conn)
            schema.drop_legacy_stock_indicators(conn)
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)
            feature_map = ensure_indicator_feature_definitions(conn, indicators)

        def fetch_symbol(sym: str) -> None:
            nonlocal fetch_completed
            try:
                with psycopg.connect(db_url) as conn:
                    conn.autocommit = True
                    schema.create_stocks_table(conn)
                    schema.migrate_stock_tables_to_data_id(conn)
                    schema.create_stock_ohlcv_table(conn)
                    data_id = upsert_stock(conn, sym)
                    outputsize = decide_outputsize(conn, data_id, timeframe)
                    last_ind = latest_indicator_date(conn, data_id)

                    # Skip if indicators are already up-to-date (unless explicitly refreshing)
                    if not refresh and not update_existing and last_ind is not None:
                        from datetime import date
                        days_old = (date.today() - last_ind).days
                        # If indicator data is from today or yesterday, skip
                        if days_old <= 1:
                            if progress:
                                progress.step_done(sym, error=False, meta={"inserted": 0, "reason": "up-to-date", "latest": str(last_ind)})
                            return

                if compute_locally:
                    with psycopg.connect(db_url) as conn:
                        cur = conn.cursor()
                        if last_ind is None:
                            cur.execute(
                                "SELECT date, open, high, low, close, adjusted_close, volume FROM stock_ohlcv WHERE data_id = %s "
                                "ORDER BY date",
                                (data_id,),
                            )
                        else:
                            cur.execute(
                                "SELECT date, open, high, low, close, adjusted_close, volume FROM stock_ohlcv WHERE data_id = %s "
                                "AND date >= %s "
                                "ORDER BY date",
                                (data_id, last_ind),
                            )
                        price_rows = [
                            {
                                "date": row[0],
                                "open": row[1],
                                "high": row[2],
                                "low": row[3],
                                "close": row[4],
                                "adjusted_close": row[5],
                                "volume": row[6],
                            }
                            for row in cur.fetchall()
                        ]
                    # Fallback: if no local prices, try to fetch from API and store
                    if not price_rows:
                        if client is None:
                            if progress:
                                progress.step_done(sym, error=True, meta={"inserted": 0, "reason": "no price data"})
                            return
                        try:
                            payload = client.fetch_daily_adjusted(sym, outputsize=outputsize)
                            fetched_rows = parse_daily_adjusted(symbol=sym, payload=payload)
                            if fetched_rows:
                                with psycopg.connect(db_url) as conn:
                                    conn.autocommit = True
                                    schema.create_stock_ohlcv_table(conn)
                                    data_id = upsert_stock(conn, sym)
                                    insert_stock_ohlcv(conn, data_id, fetched_rows, update_existing=True)
                                price_rows = sorted(
                                    [
                                        {
                                            "date": r["date"],
                                            "open": r.get("open"),
                                            "high": r.get("high"),
                                            "low": r.get("low"),
                                            "close": r.get("close"),
                                            "adjusted_close": r.get("adjusted_close"),
                                            "volume": r.get("volume"),
                                        }
                                        for r in fetched_rows
                                    ],
                                    key=lambda r: r["date"],
                                )
                        except Exception as exc:
                            if progress:
                                progress.step_done(sym, error=True, meta={"inserted": 0, "reason": str(exc)})
                            return
                    merged_rows, failed = compute_local(price_rows, indicators, return_failures=True)
                    if not merged_rows:
                        reason = "no price data"
                        if failed:
                            # failed is list of (feature_name, error_msg) tuples
                            failed_names = [f[0] for f in failed]
                            reason = f"features failed: {', '.join(failed_names)}"
                        if progress:
                            progress.step_done(sym, error=True, meta={"inserted": 0, "reason": reason, "failed_features": failed})
                        return
                    # Report any partial failures
                    if failed and progress:
                        meta = {"failed_features": failed}
                    else:
                        meta = {}
                    max_dt = _max_date(merged_rows)
                    if (not refresh) and last_ind and max_dt and max_dt <= last_ind:
                        if progress:
                            progress.step_done(sym, error=False, meta={"inserted": 0, "reason": "no change", "outputsize": "skip"})
                        return
                else:
                    rows_per_indicator: List[List[Dict[str, object]]] = []
                    for ind in indicators:
                        fn, parser, params = INDICATOR_FUNCTIONS[ind]
                        cache_key = (sym, fn, outputsize)
                        if cache_key in cache:
                            payload = cache[cache_key]
                        else:
                            attempts = 0
                            payload = {}
                            while attempts < 3:
                                payload = fetch_indicator_payload(client, fn, symbol=sym, outputsize=outputsize, extra_params=params)
                                if any(k in payload for k in ("Note", "Error Message", "Information")):
                                    attempts += 1
                                    if attempts >= 3:
                                        reason = payload.get("Note") or payload.get("Error Message") or payload.get("Information")
                                        if progress:
                                            progress.step_done(sym, error=True, meta={"inserted": 0, "reason": reason or "api error"})
                                        return
                                    time.sleep(2 + attempts)
                                    continue
                                break
                            cache[cache_key] = payload
                        parsed = parser(payload)
                        rows_per_indicator.append(parsed)
                    merged_rows = merge_indicator_rows(rows_per_indicator)
                    max_dt = _max_date(merged_rows)
                    if (not refresh) and last_ind and max_dt and max_dt <= last_ind:
                        if progress:
                            progress.step_done(sym, error=False, meta={"inserted": 0, "reason": "no change", "outputsize": "skip"})
                        return

                if not merged_rows:
                    if progress:
                        progress.step_done(sym, error=True, meta={"inserted": 0, "reason": "empty indicators"})
                    return
                work_q.put({"symbol": sym, "rows": merged_rows, "data_id": data_id, "meta": meta})
                fetch_completed += 1
                if progress:
                    progress.update_stats(queue_depth=work_q.qsize(), fetch_completed=fetch_completed)
            except Exception as exc:
                if progress:
                    progress.step_done(sym, error=True, meta={"inserted": 0, "reason": str(exc)})

        def writer_worker() -> None:
            nonlocal inserted_total
            with psycopg.connect(db_url) as conn:
                conn.autocommit = True
                while True:
                    item = work_q.get()
                    if item is writer_done:
                        break
                    sym = item["symbol"]
                    rows = item["rows"]
                    data_id = item["data_id"]
                    item_meta = item.get("meta", {})
                    try:
                        retries = 0
                        backoff = 0.1
                        write_start = time.monotonic()
                        while True:
                            try:
                                inserted = 0
                                if feature_map:
                                    inserted += insert_computed_features(
                                        conn,
                                        data_id=data_id,
                                        rows=rows,
                                        feature_map=feature_map,
                                        update_existing=update_existing,
                                        skip_before=None,
                                        batch_size=batch_size,
                                    )
                                break
                            except errors.DeadlockDetected:
                                time.sleep(0.1 + (0.1 * retries))
                                retries += 1
                            except errors.InsufficientResources:
                                time.sleep(backoff)
                                retries += 1
                                backoff = min(backoff * 2, 2.0)
                            if retries >= 5:
                                raise
                        write_duration = time.monotonic() - write_start
                        if progress:
                            progress.record_write_latency(write_duration)
                        inserted_total += inserted
                        if progress:
                            success_meta = {"inserted": inserted}
                            success_meta.update(item_meta)
                            progress.step_done(sym, error=False, meta=success_meta)
                            progress.update_stats(queue_depth=work_q.qsize(), fetch_completed=fetch_completed)
                    except Exception as exc:
                        conn.rollback()
                        if progress:
                            progress.step_done(sym, error=True, meta={"inserted": 0, "reason": str(exc)})

        writers = []
        for _ in range(max(1, writer_workers)):
            t = threading.Thread(target=writer_worker, daemon=True)
            t.start()
            writers.append(t)

        # Fetch in parallel
        fetch_workers = max(1, fetch_workers)
        with ThreadPoolExecutor(max_workers=fetch_workers) as pool:
            futures = {pool.submit(fetch_symbol, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                fut.result()

            work_q.put(writer_done)
            for _ in writers:
                work_q.put(writer_done)
            for t in writers:
                t.join()
            return inserted_total
    finally:
        if use_prepared_statements:
            db_pool.close_pool()
