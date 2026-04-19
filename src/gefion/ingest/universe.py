from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
import queue
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple, TypeVar

import psycopg
from psycopg import errors
from psycopg import sql

# Try to import OpenTelemetry context propagation (optional)
try:
    from opentelemetry import context as otel_context
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

T = TypeVar('T')

from gefion.alphavantage.catalog import parse_daily_adjusted, parse_listing_status
from gefion.alphavantage.client import AlphaVantageClient
from gefion.db import schema
from gefion.observability import create_span, set_attributes
from gefion.db.ingest import (
    decide_outputsize,
    insert_stock_ohlcv,
    upsert_stock,
    latest_price_date,
    filter_symbols_needing_update,
    filter_new_rows,
)
from gefion.utils.progress import ProgressReporter

from datetime import date, timedelta


def propagate_context(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that captures and propagates OpenTelemetry context to worker threads.

    This ensures trace spans created in thread pool workers are properly nested
    under their parent spans.
    """
    if not OTEL_AVAILABLE:
        return func

    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        # Capture current context
        ctx = otel_context.get_current()

        # Create a function that attaches context before executing
        def run_with_context():
            token = otel_context.attach(ctx)
            try:
                return func(*args, **kwargs)
            finally:
                otel_context.detach(token)

        return run_with_context()

    return wrapper


def _today_date() -> date:
    return date.today()


def _expected_market_date(include_today: bool = False) -> date:
    """Return the most recent likely market day with complete data (weekend-aware).

    Args:
        include_today: If True, considers today's data available (useful after market close).
                      If False (default), only expects data through yesterday.

    Market close is 4pm ET (9pm UTC). By default, we conservatively use yesterday
    to avoid false positives before market data is published.
    """
    from datetime import datetime
    import pytz

    today = date.today()

    # If include_today is explicitly requested, use today
    if include_today:
        # Weekend adjustment for today
        if today.weekday() == 5:  # Saturday -> Friday
            return today - timedelta(days=1)
        if today.weekday() == 6:  # Sunday -> Friday
            return today - timedelta(days=2)
        return today

    # Auto-detect based on current time in ET
    # After 4pm ET (market close): today's data is available
    # After midnight ET but before 9:30am: yesterday's data is available (market closed hours ago)
    try:
        et_tz = pytz.timezone('America/New_York')
        now_et = datetime.now(et_tz)
        today_et = now_et.date()
        market_close_hour = 16  # 4pm ET

        if now_et.hour >= market_close_hour and today_et.weekday() < 5:
            # After market close on a weekday — today's data is available
            return today_et
        elif now_et.hour < 10 and today_et.weekday() < 5:
            # After midnight, before market open — yesterday's data is available
            yesterday_et = today_et - timedelta(days=1)
            # Weekend adjustment
            if yesterday_et.weekday() == 5:
                return yesterday_et - timedelta(days=1)
            if yesterday_et.weekday() == 6:
                return yesterday_et - timedelta(days=2)
            return yesterday_et
    except Exception:
        # If timezone check fails, fall through to conservative default
        pass

    # Default: use yesterday as most recent complete trading day
    yesterday = today - timedelta(days=1)
    # Weekend adjustment for yesterday
    if yesterday.weekday() == 5:  # Saturday -> Friday
        return yesterday - timedelta(days=1)
    if yesterday.weekday() == 6:  # Sunday -> Friday
        return yesterday - timedelta(days=2)
    return yesterday


def _parse_listing_csv(csv_text: str) -> List[Mapping[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [row for row in reader]


def fetch_listings(client: AlphaVantageClient) -> List[Mapping[str, object]]:
    payload = client.fetch_listing_status()
    if isinstance(payload, str):
        data = _parse_listing_csv(payload)
        return parse_listing_status({"data": data})
    if isinstance(payload, Mapping) and "text" in payload:
        data = _parse_listing_csv(str(payload["text"]))
        return parse_listing_status({"data": data})
    if "bestMatches" in payload:
        # Fallback; treat as empty
        return []
    return parse_listing_status(payload)


def filter_listings(
    listings: Iterable[Mapping[str, object]],
    exchange: Optional[str] = None,
    status: str = "Active",
) -> List[Mapping[str, object]]:
    exchange_norm = exchange.lower() if exchange else None
    status_norm = status.lower() if status else None
    out: List[Mapping[str, object]] = []
    for entry in listings:
        entry_status = str(entry.get("status", "")).lower()
        entry_exchange = str(entry.get("exchange", "")).lower()
        if status_norm and entry_status != status_norm:
            continue
        if exchange_norm and entry_exchange != exchange_norm:
            continue
        out.append(entry)
    return out


def load_listings_from_file(path: Path) -> List[Mapping[str, object]]:
    """Load listings from CSV or JSON file."""
    text = path.read_text()
    if path.suffix.lower() == ".csv":
        data = _parse_listing_csv(text)
        return parse_listing_status({"data": data})
    # Assume JSON with {"data": [...]} shape
    import json

    payload = json.loads(text)
    return parse_listing_status(payload)


def ingest_prices_for_symbols(
    db_url: str,
    client: AlphaVantageClient,
    symbols: Sequence[str],
    max_workers: int = 4,
    writer_workers: int = 1,
    timeframe: str = "auto",
    update_existing: bool = False,
    progress: Optional[ProgressReporter] = None,
    status: Optional[str] = "Active",
    target_date: Optional[date] = None,
    since_date: Optional[date] = None,
) -> int:
    """
    Fetch and ingest prices for symbols in parallel.

    Args:
        db_url: Database connection URL
        client: AlphaVantage API client
        symbols: List of symbols to ingest
        max_workers: Number of parallel API fetch workers
        writer_workers: Number of parallel database writer workers
        timeframe: Timeframe for fetching data ('auto', 'full', or 'compact')
        update_existing: Whether to update existing price data
        progress: Optional progress reporter
        status: Stock status to set (defaults to 'Active'). Set to None to not update status.
        target_date: Maximum date to allow (prevents inserting future/partial data).
                    Defaults to None (no limit).

    Returns:
        Total number of rows inserted
    """
    inserted_total = 0

    fetch_count = 0

    # Ensure schema exists once before parallel work
    with create_span("ingest_prices.schema_init"):
        with psycopg.connect(db_url) as conn:
            schema.create_stocks_table(conn)
            schema.migrate_stock_tables_to_data_id(conn)
            schema.create_stock_ohlcv_table(conn)
            # Note: Bulk filtering moved to CLI layer for better performance
            # (filters once for all symbols instead of once per 50-symbol chunk)
    # Bounded queue prevents memory exhaustion when fetchers outpace writers
    work_queue: queue.Queue[Tuple[str, int, list, str]] = queue.Queue(maxsize=200)
    writer_done = object()
    latest_cache: dict[str, int] = {}

    def fetch_worker(sym: str) -> None:
        nonlocal fetch_count
        try:
            with psycopg.connect(db_url) as conn:
                data_id = upsert_stock(conn, sym, status=status)

                if timeframe == "auto":
                    outputsize = decide_outputsize(conn, data_id, timeframe)
                elif timeframe == "full":
                    outputsize = "full"
                else:
                    outputsize = "compact"

            payload = client.fetch_daily_adjusted(sym, outputsize=outputsize)

            # Check for API error responses (rate limits, invalid symbols, etc.)
            if any(k in payload for k in ("Note", "Error Message", "Information")):
                error_msg = payload.get("Note") or payload.get("Error Message") or payload.get("Information")
                if progress:
                    progress.step_done(sym, error=True, meta={"inserted": 0, "reason": error_msg})
                return

            rows = parse_daily_adjusted(symbol=sym, payload=payload)
            if not rows:
                if progress:
                    progress.step_done(sym, error=True, meta={"inserted": 0, "reason": "empty payload"})
                return

            # Filter to only new rows unless we are explicitly refreshing/upserting
            if not update_existing:
                with psycopg.connect(db_url) as filter_conn:
                    rows = filter_new_rows(filter_conn, data_id, rows, target_date=target_date, since_date=since_date)

            if not rows:
                if progress:
                    progress.step_done(sym, error=False, meta={"inserted": 0, "reason": "no new data", "outputsize": "skip"})
                return

            api_latest = rows[0]["date"]
            cache_key = sym
            if cache_key in latest_cache and latest_cache[cache_key] >= api_latest:
                if progress:
                    progress.step_done(sym, error=False, meta={"inserted": 0, "reason": "no change", "outputsize": "skip"})
                return
            latest_cache[cache_key] = api_latest
            work_queue.put((sym, data_id, rows, outputsize))
            nonlocal fetch_count
            fetch_count += 1
            if progress:
                progress.update_stats(queue_depth=work_queue.qsize(), fetch_completed=fetch_count)
        except Exception as exc:
            if progress:
                progress.step_done(sym, error=True, meta={"inserted": 0, "reason": str(exc)})

    def writer_worker() -> int:
        nonlocal inserted_total
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            while True:
                item = work_queue.get()
                if item is writer_done:
                    break
                sym, data_id, rows, outputsize = item
                try:
                    retries = 0
                    backoff = 0.1
                    with create_span(
                        "ingest_prices.write_symbol",
                        symbol=sym,
                        outputsize=outputsize,
                        update_existing=update_existing,
                        row_count=len(rows),
                    ) as write_span:
                        while True:
                            try:
                                # data_id already obtained by fetch_worker, no need to upsert again
                                inserted = _batch_insert_prices(conn, data_id, rows, update_existing)
                                break
                            except errors.DeadlockDetected:
                                time.sleep(0.1 + random.random() * 0.2)
                                retries += 1
                            except errors.InsufficientResources:
                                time.sleep(backoff)
                                retries += 1
                                backoff = min(backoff * 2, 2.0)
                            if retries >= 5:
                                raise
                        set_attributes(write_span, inserted=inserted, retries=retries)
                    inserted_total += inserted
                    if progress:
                        progress.step_done(sym, error=False, meta={"inserted": inserted, "outputsize": outputsize})
                        progress.update_stats(queue_depth=work_queue.qsize(), fetch_completed=fetch_count)
                except Exception as exc:
                    if progress:
                        progress.step_done(sym, error=True, meta={"inserted": 0, "reason": str(exc)})
        return inserted_total

    def _batch_insert_prices(conn: psycopg.Connection, data_id: int, rows: list, update_existing: bool) -> int:
        if not rows:
            return 0
        total = 0
        chunk_size = 200

        def safe_num(val):
            if val is None:
                return None
            try:
                if abs(float(val)) >= 1e12:
                    return None
                return val
            except Exception:
                return None

        for i in range(0, len(rows), chunk_size):
            batch = rows[i : i + chunk_size]
            values_sql = []
            params = []
            for r in batch:
                open_v = safe_num(r.get("open"))
                high_v = safe_num(r.get("high"))
                low_v = safe_num(r.get("low"))
                close_v = safe_num(r.get("close"))
                adj_v = safe_num(r.get("adjusted_close"))
                if all(v is None for v in [open_v, high_v, low_v, close_v, adj_v]):
                    continue
                date_val = r.get("date")
                parsed_date = None
                if isinstance(date_val, (datetime,)):
                    parsed_date = date_val.date()
                elif isinstance(date_val, str):
                    try:
                        parsed_date = datetime.fromisoformat(date_val).date()
                    except Exception:
                        continue
                else:
                    parsed_date = date_val  # assume date or None
                if parsed_date is None:
                    continue
                values_sql.append("(%s, %s, %s, %s, %s, %s, %s, %s, %s)")
                params.extend(
                    [
                        data_id,
                        parsed_date,
                        open_v,
                        high_v,
                        low_v,
                        close_v,
                        adj_v,
                        safe_num(r.get("volume")),
                        r.get("source", "alphavantage"),
                    ]
                )
            if not values_sql:
                continue
            conflict = (
                "ON CONFLICT (data_id, date) DO UPDATE SET "
                "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                "close = EXCLUDED.close, adjusted_close = EXCLUDED.adjusted_close, "
                "volume = EXCLUDED.volume, source = EXCLUDED.source"
                if update_existing
                else "ON CONFLICT (data_id, date) DO NOTHING"
            )
            sql_stmt = (
                "INSERT INTO stock_ohlcv "
                "(data_id, date, open, high, low, close, adjusted_close, volume, source) VALUES "
                + ",".join(values_sql)
                + " "
                + conflict
            )
            with conn.cursor() as cur:
                cur.execute(sql_stmt, params)
                # Use rowcount to get ACTUAL inserts (excludes ON CONFLICT skipped rows)
                total += cur.rowcount
        conn.commit()
        return total

    # Start writer pool (small to avoid deadlocks)
    writer_threads = max(1, writer_workers)
    writer_ctx = otel_context.get_current() if OTEL_AVAILABLE else None
    with ThreadPoolExecutor(max_workers=writer_threads) as writer_pool:
        if OTEL_AVAILABLE:
            def make_writer_with_context():
                def writer_with_context():
                    token = otel_context.attach(writer_ctx)
                    try:
                        return writer_worker()
                    finally:
                        otel_context.detach(token)
                return writer_with_context
            writer_futures = [writer_pool.submit(make_writer_with_context()) for _ in range(writer_threads)]
        else:
            writer_futures = [writer_pool.submit(writer_worker) for _ in range(writer_threads)]

        try:
            # Fetch in parallel
            fetch_workers = max_workers
            with ThreadPoolExecutor(max_workers=fetch_workers) as fetch_pool:
                # Capture context and create wrapped worker for each symbol
                if OTEL_AVAILABLE:
                    ctx = otel_context.get_current()
                    def make_context_worker(symbol: str):
                        def worker_with_context():
                            token = otel_context.attach(ctx)
                            try:
                                return fetch_worker(symbol)
                            finally:
                                otel_context.detach(token)
                        return worker_with_context
                    futures = {fetch_pool.submit(make_context_worker(sym)): sym for sym in symbols}
                else:
                    futures = {fetch_pool.submit(fetch_worker, sym): sym for sym in symbols}

                for fut in as_completed(futures):
                    # Drain exceptions
                    fut.result()
        finally:
            # CRITICAL: Signal writers to finish even if fetch phase fails
            # Without this, writer threads block forever on queue.get() during shutdown
            for _ in range(writer_threads):
                work_queue.put(writer_done)

        # Wait for writers to complete
        for fut in writer_futures:
            fut.result()

    return inserted_total
