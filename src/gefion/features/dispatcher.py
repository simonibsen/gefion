"""
Generic feature computation dispatcher.

Routes feature computation based on function_name in feature_definitions,
fetches source data based on source_table/source_column metadata,
calls appropriate compute functions, and stores results.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Callable, Any, Tuple, Mapping
import queue
import threading
import time
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import warnings
import inspect
import psycopg
from psycopg import sql

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Try to import OpenTelemetry context propagation (optional)
try:
    from opentelemetry import context as otel_context
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

from gefion.db.ingest import insert_computed_features
from gefion.observability import create_span, set_attributes, add_event, get_current_span


# Function cache for resolved DB functions
_FUNCTION_CACHE: Dict[str, Callable] = {}
_FUNCTION_CACHE_SOURCE: Dict[str, str] = {}
_LOOKBACK_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def _function_lookback_declaration(conn: psycopg.Connection,
                                   function_name: str) -> Optional[Dict[str, Any]]:
    """The function's declared inputs["lookback"], or None (= full history).
    Cached per run — one query per function group, not per symbol."""
    if function_name in _LOOKBACK_CACHE:
        return _LOOKBACK_CACHE[function_name]
    declaration = None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT inputs FROM feature_functions
               WHERE name = %s AND enabled = TRUE AND status = 'active'
               ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
               LIMIT 1""",
            (function_name,))
        row = cur.fetchone()
    if row and row[0]:
        inputs = row[0]
        if isinstance(inputs, str):
            import json as _json
            inputs = _json.loads(inputs)
        if isinstance(inputs, dict):
            declaration = inputs.get("lookback")
    _LOOKBACK_CACHE[function_name] = declaration
    return declaration


def compute_features(
    conn: psycopg.Connection,
    data_id: int,
    function_names: Optional[List[str]] = None,
    feature_names: Optional[List[str]] = None,
    incremental: bool = True,
    full_refresh: bool = False,
    update_existing: bool = False,
    feature_batch_size: int = 2000,
    writer_workers: int = 0,
    profile: bool = False,
    sync_commit: bool = False,
    parallel_functions: bool = False,
    max_parallel_functions: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generic feature computation dispatcher.

    Reads active feature_definitions, fetches source data based on metadata,
    calls appropriate compute functions, stores results.

    Args:
        conn: Database connection
        data_id: ID of the data entity (stock, market, etc.)
        function_names: Optional filter for specific function types
        feature_names: Optional filter for specific feature names
        incremental: If True, only compute new dates (default)
        full_refresh: If True, recompute all dates (overrides incremental)
        update_existing: If True, update existing rows on conflict
        parallel_functions: If True, process function groups in parallel
        max_parallel_functions: Max parallel function groups (defaults to cpu_count - 2)

    Returns:
        Dict with results per function_name:
        {
            'indicator': {'inserted': 100, 'errors': []},
            'derivative': {'inserted': 50, 'errors': [...]},
            'summary': {'total_inserted': 150, 'total_errors': 0}
        }
    """
    with create_span(
        "compute_features",
        data_id=data_id,
        incremental=incremental,
        full_refresh=full_refresh,
        parallel_functions=parallel_functions,
        writer_workers=writer_workers,
        function_names=str(function_names) if function_names else "all",
        feature_names=str(feature_names) if feature_names else "all"
    ):
        return _compute_features_impl(
            conn, data_id, function_names, feature_names, incremental,
            full_refresh, update_existing, feature_batch_size, writer_workers,
            profile, sync_commit, parallel_functions, max_parallel_functions
        )


def _compute_features_impl(
    conn: psycopg.Connection,
    data_id: int,
    function_names: Optional[List[str]],
    feature_names: Optional[List[str]],
    incremental: bool,
    full_refresh: bool,
    update_existing: bool,
    feature_batch_size: int,
    writer_workers: int,
    profile: bool,
    sync_commit: bool,
    parallel_functions: bool,
    max_parallel_functions: Optional[int],
) -> Dict[str, Any]:
    """Internal implementation of compute_features."""
    # Ensure fresh resolution per run (cache still used within this call)
    _FUNCTION_CACHE.clear()
    _FUNCTION_CACHE_SOURCE.clear()
    _LOOKBACK_CACHE.clear()

    # Memory safety check (if psutil available)
    if PSUTIL_AVAILABLE:
        try:
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)

            # Warn if available memory is low
            if available_gb < 2.0:
                warnings.warn(
                    f"Low memory warning: Only {available_gb:.1f} GB available. "
                    f"Feature computation may fail or cause system slowdown. "
                    f"Consider reducing --max-workers, --writer-workers, or --batch-size."
                )

            # Error if critically low (< 500 MB)
            if available_gb < 0.5:
                raise MemoryError(
                    f"Critically low memory: Only {available_gb:.1f} GB available. "
                    f"Cannot safely proceed with feature computation. "
                    f"Free up memory and try again with lower settings."
                )
        except Exception as e:
            # Don't fail on memory check errors, just warn
            if not isinstance(e, MemoryError):
                warnings.warn(f"Memory check failed: {e}")

    results: Dict[str, Any] = {}
    total_inserted = 0
    total_errors: List[Dict[str, Any]] = []

    # Step 1: Read active feature definitions
    current_span = get_current_span()
    with create_span("compute_features.fetch_definitions"):
        feature_defs = _fetch_feature_definitions(
            conn,
            function_names=function_names,
            feature_names=feature_names
        )
    set_attributes(current_span, feature_count=len(feature_defs))

    if not feature_defs:
        return {
            'summary': {'total_inserted': 0, 'total_errors': 0}
        }

    latest_by_feature: Dict[int, Optional[date]] = {}
    if incremental and not full_refresh:
        feature_ids = [f[0] for f in feature_defs]
        with create_span("compute_features.latest_dates", feature_count=len(feature_ids)):
            latest_by_feature = _latest_dates_for_features(conn, data_id, feature_ids)

    # Step 2: Group by function_name
    grouped_by_function = _group_by_function_name(feature_defs)
    set_attributes(current_span, function_group_count=len(grouped_by_function))

    # Optional writer queue for pipelined writes
    write_queue: Optional[queue.Queue] = None
    writer_threads: List[threading.Thread] = []
    stop_token = object()
    writer_errors: List[Exception] = []
    writer_events: List[threading.Event] = []
    timings: Optional[Dict[str, float]] = None
    timings_lock: Optional[threading.Lock] = None
    if profile:
        timings = {"fetch": 0.0, "compute": 0.0, "write": 0.0, "queue_wait": 0.0, "writer": 0.0, "writer_wait": 0.0}
        timings_lock = threading.Lock()  # Protect timings dict from concurrent updates

    # Create cache for intermediate calculations shared across all features for this stock
    # This allows features to reuse expensive computations (e.g., moving averages)
    # Cache is stock-specific and persists across all function groups
    cache: Dict[str, Any] = {}
    cache_lock = threading.Lock() if parallel_functions else None

    def enqueue_or_write(rows, feature_map, skip_before=None):
        if write_queue is not None:
            q_start = time.monotonic()
            evt = threading.Event()
            writer_events.append(evt)
            write_queue.put({"rows": rows, "feature_map": feature_map,
                             "skip_before": skip_before,
                             "queue_ts": q_start, "event": evt})
            if timings is not None and timings_lock is not None:
                elapsed = time.monotonic() - q_start
                with timings_lock:
                    timings["queue_wait"] += elapsed
            return len(rows)
        return insert_computed_features(
            conn,
            data_id=data_id,
            rows=rows,
            feature_map=feature_map,
            update_existing=update_existing,
            skip_before=skip_before,
            batch_size=feature_batch_size,
        )

    if writer_workers and writer_workers > 0:
        # Use a larger queue size (200) to provide adequate buffering between
        # compute and write stages. Small queues (e.g., writer_workers * 2)
        # cause frequent blocking and reduce pipeline efficiency.
        write_queue = queue.Queue(maxsize=200)

        def writer_loop():
            # Acquire connection per-write instead of holding for thread lifetime
            # This reduces pool contention and allows connections to be reused
            # between writes when threads are idle waiting on the queue
            from gefion.db import pool as db_pool

            while True:
                # Wait for work item (no connection held during idle wait)
                item = write_queue.get()
                if item is stop_token:
                    write_queue.task_done()
                    break

                # Acquire connection only for the duration of the write
                try:
                    with db_pool.get_connection() as writer_conn:
                        writer_conn.autocommit = True
                        try:
                            start = time.monotonic()
                            insert_computed_features(
                                writer_conn,
                                data_id=data_id,
                                rows=item["rows"],
                                feature_map=item["feature_map"],
                                update_existing=update_existing,
                                skip_before=item.get("skip_before"),
                                batch_size=feature_batch_size,
                                sync_commit=sync_commit,
                            )
                            if timings is not None and timings_lock is not None:
                                elapsed = time.monotonic() - start
                                with timings_lock:
                                    timings["writer"] += elapsed
                            evt = item.get("event")
                            if evt:
                                evt.set()
                        except Exception as exc:
                            writer_errors.append(exc)
                            # Still set the event even on error to avoid deadlock
                            evt = item.get("event")
                            if evt:
                                evt.set()
                except Exception as exc:
                    # Connection acquisition failure
                    writer_errors.append(exc)
                    evt = item.get("event")
                    if evt:
                        evt.set()
                finally:
                    write_queue.task_done()

        # Capture context for propagation to writer threads
        writer_ctx = otel_context.get_current() if OTEL_AVAILABLE else None

        def make_writer_with_context():
            """Create a context-aware wrapper for writer_loop."""
            def writer_with_context():
                if OTEL_AVAILABLE and writer_ctx:
                    token = otel_context.attach(writer_ctx)
                    try:
                        return writer_loop()
                    finally:
                        otel_context.detach(token)
                else:
                    return writer_loop()
            return writer_with_context

        for _ in range(writer_workers):
            # Use context-aware wrapper if OpenTelemetry is available
            if OTEL_AVAILABLE and writer_ctx:
                t = threading.Thread(target=make_writer_with_context(), daemon=True)
            else:
                t = threading.Thread(target=writer_loop, daemon=True)
            t.start()
            writer_threads.append(t)

    # Step 3: Process each function_name group
    if parallel_functions and len(grouped_by_function) > 1:
        # Parallel execution using ThreadPoolExecutor
        num_cores = multiprocessing.cpu_count()
        max_parallel = max_parallel_functions or max(2, num_cores - 2)
        max_parallel = min(max_parallel, len(grouped_by_function))  # Don't spawn more workers than groups

        # Safety limit: cap at 4 workers to prevent resource exhaustion
        # This prevents thread explosion when combined with writer_workers
        if max_parallel > 4:
            warnings.warn(
                f"Limiting parallel_functions workers from {max_parallel} to 4 to prevent resource exhaustion. "
                f"With writer_workers={writer_workers}, total threads would be {max_parallel * (1 + writer_workers)}."
            )
            max_parallel = 4

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            # Capture context for propagation to executor threads
            if OTEL_AVAILABLE:
                executor_ctx = otel_context.get_current()
                def make_context_worker(data_id, func_name, features, incremental, update_existing,
                                       latest_by_feature, feature_batch_size, writer, timings,
                                       timings_lock, cache, cache_lock, sync_commit):
                    """Create context-aware wrapper for function group processing."""
                    def worker_with_context():
                        token = otel_context.attach(executor_ctx)
                        try:
                            return _process_function_group_with_connection(
                                data_id, func_name, features, incremental, update_existing,
                                latest_by_feature, feature_batch_size, writer, timings,
                                timings_lock, cache, cache_lock, sync_commit
                            )
                        finally:
                            otel_context.detach(token)
                    return worker_with_context

            # Submit all function groups for parallel execution
            future_to_func = {}
            for func_name, features in grouped_by_function.items():
                if OTEL_AVAILABLE:
                    # Submit context-aware wrapper
                    future = executor.submit(
                        make_context_worker(
                            data_id, func_name, features,
                            incremental=incremental and not full_refresh,
                            update_existing=update_existing,
                            latest_by_feature=latest_by_feature,
                            feature_batch_size=feature_batch_size,
                            writer=enqueue_or_write,
                            timings=timings if profile else None,
                            timings_lock=timings_lock if profile else None,
                            cache=cache,
                            cache_lock=cache_lock,
                            sync_commit=sync_commit,
                        )
                    )
                else:
                    # Submit directly without context propagation
                    future = executor.submit(
                        _process_function_group_with_connection,
                        data_id,
                        func_name,
                        features,
                        incremental=incremental and not full_refresh,
                        update_existing=update_existing,
                        latest_by_feature=latest_by_feature,
                        feature_batch_size=feature_batch_size,
                        writer=enqueue_or_write,
                        timings=timings if profile else None,
                        timings_lock=timings_lock if profile else None,
                        cache=cache,
                        cache_lock=cache_lock,
                        sync_commit=sync_commit,
                    )
                future_to_func[future] = func_name

            # Collect results as they complete
            for future in as_completed(future_to_func):
                func_name = future_to_func[future]
                try:
                    func_result = future.result()
                    results[func_name] = func_result
                    total_inserted += func_result.get('inserted', 0)
                    total_errors.extend(func_result.get('errors', []))
                except Exception as exc:
                    error = {
                        'function_name': func_name,
                        'error': str(exc),
                        'feature_count': len(grouped_by_function[func_name]),
                    }
                    results[func_name] = {'inserted': 0, 'errors': [error]}
                    total_errors.append(error)
    else:
        # Sequential execution (original behavior)
        for func_name, features in grouped_by_function.items():
            try:
                func_result = _process_function_group(
                    conn,
                    data_id,
                    func_name,
                    features,
                    incremental=incremental and not full_refresh,
                    update_existing=update_existing,
                    latest_by_feature=latest_by_feature,
                    feature_batch_size=feature_batch_size,
                    writer=enqueue_or_write,
                    timings=timings if profile else None,
                    timings_lock=timings_lock if profile else None,
                    cache=cache,  # Pass cache to share across all functions for this stock
                    cache_lock=cache_lock,  # Pass lock for thread-safe cache access
                )

                results[func_name] = func_result
                total_inserted += func_result.get('inserted', 0)
                total_errors.extend(func_result.get('errors', []))

            except Exception as exc:
                error = {
                    'function_name': func_name,
                    'error': str(exc),
                    'feature_count': len(features),
                }
                results[func_name] = {'inserted': 0, 'errors': [error]}
                total_errors.append(error)

    # === CHUNK PRE-CREATION (Prevents deadlocks and optimizes performance) ===
    # After all computes complete, pre-create chunks for the entire date range
    # This ensures writers hit the optimistic insert fast path (no chunk creation)
    # and eliminates the deadlock condition from concurrent chunk creation
    if write_queue is not None and not write_queue.empty():
        with create_span("chunk.precreate", queue_size=write_queue.qsize()):
            def precreate_chunks_for_queued_data():
                """Scan write queue and pre-create all needed chunks before writers drain it."""
                if write_queue.empty():
                    return

                # Scan queue to find min/max dates (non-destructive peek)
                items = []
                try:
                    while True:
                        item = write_queue.get_nowait()
                        if item is stop_token:
                            write_queue.put(item)
                            break
                        items.append(item)
                        # Must call task_done() for each get_nowait() to keep task counter balanced
                        # when we put() items back below
                        write_queue.task_done()
                except queue.Empty:
                    pass

                # Put items back in queue
                for item in items:
                    write_queue.put(item)

                if not items:
                    return

                # Calculate date range across ALL queued work
                min_date = None
                max_date = None
                for item in items:
                    rows = item.get("rows", [])
                    for row in rows:
                        dt = row.get("date")
                        if dt:
                            if min_date is None or dt < min_date:
                                min_date = dt
                            if max_date is None or dt > max_date:
                                max_date = dt

                if min_date and max_date:
                    from gefion.utils.timescale import ensure_chunks_for_date_range

                    buffer = timedelta(days=1)

                    # Use SEPARATE connection (not autocommit) to avoid lock contention
                    # Note: data_id is from parent scope (compute_features function)
                    try:
                        with psycopg.connect(conn.info.dsn) as chunk_conn:
                            chunk_conn.autocommit = False
                            try:
                                ensure_chunks_for_date_range(
                                    chunk_conn,
                                    "computed_features",
                                    min_date - buffer,
                                    max_date + buffer,
                                    chunk_interval_days=30
                                )
                                set_attributes(get_current_span(),
                                              date_range_days=(max_date - min_date).days,
                                              min_date=str(min_date),
                                              max_date=str(max_date))
                            except Exception:
                                # Log but don't fail - optimistic fallback will handle it
                                pass
                    except Exception:
                        # If we can't create separate connection, just skip pre-creation
                        # Optimistic fallback will handle chunk creation on-demand
                        pass

            # Pre-create chunks after compute completes, before draining queue
            precreate_chunks_for_queued_data()

    # Drain writer queue
    if write_queue is not None:
        with create_span("writer.drain", thread_count=len(writer_threads)):
            wait_start = time.monotonic()
            for _ in writer_threads:
                write_queue.put(stop_token)
            write_queue.join()
            for evt in writer_events:
                evt.wait()
            for t in writer_threads:
                t.join(timeout=5)
            if timings is not None and timings_lock is not None:
                elapsed = time.monotonic() - wait_start
                with timings_lock:
                    timings["writer_wait"] += elapsed

            # Writer errors are fatal - we can't trust the results if writers failed
            if writer_errors:
                error_messages = [str(e) for e in writer_errors]
                set_attributes(get_current_span(), error_count=len(writer_errors))
                raise RuntimeError(
                    f"Writer thread errors occurred during feature computation: "
                    f"{len(writer_errors)} error(s): {'; '.join(error_messages[:3])}"
                    + (f" (and {len(error_messages) - 3} more)" if len(error_messages) > 3 else "")
                )

    # Summary
    results['summary'] = {
        'total_inserted': total_inserted,
        'total_errors': len(total_errors),
    }
    if profile and timings:
        results['summary']['timing'] = {k: round(v, 6) for k, v in timings.items()}

    # Add final metrics to current span
    set_attributes(current_span,
        total_inserted=total_inserted,
        total_errors=len(total_errors),
        error_rate=len(total_errors) / max(1, total_inserted + len(total_errors))
    )
    if profile and timings:
        for key, value in timings.items():
            set_attributes(current_span, **{f"timing.{key}": value})

    return results


def _fetch_feature_definitions(
    conn: psycopg.Connection,
    function_names: Optional[List[str]] = None,
    feature_names: Optional[List[str]] = None,
) -> List[Tuple]:
    """
    Fetch active feature definitions from database.

    Returns list of tuples:
    (id, name, function_name, params, source_table, source_column, store_table, store_column)
    """
    query = """
    SELECT
        id,
        name,
        function_name,
        params,
        source_table,
        source_column,
        store_table,
        store_column
    FROM feature_definitions
    WHERE active = TRUE
      -- per-stock sweep: only stock-scope functions are dispatchable here.
      -- 'market' bodies run per-date over the cross-section (spec 011) and
      -- 'materialized' markers are written by their own pipeline (macro
      -- ingest/derive, ml predict); attempting either per symbol fails and
      -- costs a source fetch. Definitions with no registry row (code-registry
      -- functions like 'indicator') stay eligible.
      AND NOT EXISTS (
          SELECT 1 FROM feature_functions ff
          WHERE ff.name = feature_definitions.function_name
            AND ff.scope <> 'stock'
      )
    """

    params = []

    if function_names:
        placeholders = ','.join(['%s'] * len(function_names))
        query += f" AND function_name IN ({placeholders})"
        params.extend(function_names)

    if feature_names:
        placeholders = ','.join(['%s'] * len(feature_names))
        query += f" AND name IN ({placeholders})"
        params.extend(feature_names)

    query += " ORDER BY function_name, name"

    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _group_by_function_name(feature_defs: List[Tuple]) -> Dict[str, List[Tuple]]:
    """
    Group feature definitions by function_name.

    Returns: {function_name: [feature_defs]}
    """
    grouped: Dict[str, List[Tuple]] = {}

    for feature_def in feature_defs:
        func_name = feature_def[2]  # function_name column
        grouped.setdefault(func_name, []).append(feature_def)

    return grouped


def _process_function_group_with_connection(
    data_id: int,
    function_name: str,
    features: List[Tuple],
    incremental: bool,
    update_existing: bool,
    latest_by_feature: Dict[int, Optional[date]],
    feature_batch_size: int,
    writer: Optional[Callable[[List[Dict[str, Any]], Mapping[str, int], Optional[date]], int]] = None,
    timings: Optional[Dict[str, float]] = None,
    timings_lock: Optional[threading.Lock] = None,
    cache: Optional[Dict[str, Any]] = None,
    cache_lock: Optional[threading.Lock] = None,
    sync_commit: bool = False,
) -> Dict[str, Any]:
    """
    Wrapper that acquires its own connection for thread-safe parallel execution.

    Each parallel worker needs its own database connection since psycopg.Connection
    objects are NOT thread-safe.
    """
    from gefion.db import pool as db_pool

    with db_pool.get_connection() as conn:
        conn.autocommit = True
        return _process_function_group(
            conn,
            data_id,
            function_name,
            features,
            incremental=incremental,
            update_existing=update_existing,
            latest_by_feature=latest_by_feature,
            feature_batch_size=feature_batch_size,
            writer=writer,
            timings=timings,
            timings_lock=timings_lock,
            cache=cache,
            cache_lock=cache_lock,
        )


def _process_function_group(
    conn: psycopg.Connection,
    data_id: int,
    function_name: str,
    features: List[Tuple],
    incremental: bool,
    update_existing: bool,
    latest_by_feature: Dict[int, Optional[date]],
    feature_batch_size: int,
    writer: Optional[Callable[[List[Dict[str, Any]], Mapping[str, int], Optional[date]], int]] = None,
    timings: Optional[Dict[str, float]] = None,
    timings_lock: Optional[threading.Lock] = None,
    cache: Optional[Dict[str, Any]] = None,
    cache_lock: Optional[threading.Lock] = None,
) -> Dict[str, Any]:
    """
    Process all features for a given function_name.

    Args:
        cache: Optional cache for intermediate calculations shared across features
        cache_lock: Optional lock for thread-safe cache access

    Returns: {'inserted': count, 'errors': [...]}
    """
    with create_span(
        "process_function_group",
        function_name=function_name,
        data_id=data_id,
        feature_count=len(features),
        incremental=incremental
    ):
        return _process_function_group_impl(
            conn, data_id, function_name, features, incremental, update_existing,
            latest_by_feature, feature_batch_size, writer, timings, timings_lock,
            cache, cache_lock
        )


def _process_function_group_impl(
    conn: psycopg.Connection,
    data_id: int,
    function_name: str,
    features: List[Tuple],
    incremental: bool,
    update_existing: bool,
    latest_by_feature: Dict[int, Optional[date]],
    feature_batch_size: int,
    writer: Optional[Callable[[List[Dict[str, Any]], Mapping[str, int], Optional[date]], int]],
    timings: Optional[Dict[str, float]],
    timings_lock: Optional[threading.Lock],
    cache: Optional[Dict[str, Any]],
    cache_lock: Optional[threading.Lock],
) -> Dict[str, Any]:
    """Internal implementation of _process_function_group."""
    # Get compute function (DB overrides code registry)
    compute_func = _resolve_compute_function(conn, function_name)

    if not compute_func:
        return {
            'inserted': 0,
            'errors': [{
                'error': f'No compute function registered for {function_name}',
                'features': [f[1] for f in features]
            }]
        }

    # Group by source_table for efficient data fetching
    grouped_by_source = _group_by_source(features)

    total_inserted = 0
    errors = []

    for source_key, source_features in grouped_by_source.items():
        try:
            # Incremental means "write only new dates", NOT "compute from only
            # new rows": windowed functions (SMA-200, RSI-14, ...) need
            # pre-cutoff history to produce values identical to a full
            # recompute — a post-cutoff fetch starves the window and they
            # silently no-op (prod features went stale for 8 trading days,
            # 2026-07). How MUCH history the body needs is its own contract:
            # the registry row's inputs["lookback"] declaration (#120 item
            # 1b); undeclared means full history — the honest default, and
            # the only correct one for path-dependent bodies (PSAR).
            skip_before: Optional[date] = None
            if incremental:
                dates = [latest_by_feature.get(f[0]) for f in source_features if f[0] in latest_by_feature]
                dates = [d for d in dates if d is not None]
                skip_before = min(dates) if dates else None

            # Prepare compute specs from feature definitions
            compute_specs = [
                {
                    'name': f[1],  # feature name
                    'feature_id': f[0],  # feature id
                    **f[3],  # params (type, window, etc.)
                }
                for f in source_features
            ]

            lookback_rows: Optional[int] = None
            if skip_before is not None:
                from gefion.features.lookback import lookback_bars
                lookback_rows = lookback_bars(
                    _function_lookback_declaration(conn, function_name),
                    compute_specs)

            # Fetch source data
            fetch_start = time.monotonic()
            with create_span(
                "fetch.source_data",
                source_table=source_key[0],
                source_column=source_key[1],
                data_id=data_id,
                incremental=skip_before is not None,
                lookback_rows=lookback_rows or 0,
            ):
                if skip_before is not None:
                    # cheap guard: no source rows past the cutoff -> nothing
                    # to write, skip the history fetch and compute
                    new_rows = _fetch_source_data(
                        conn, data_id, source_key, source_features,
                        start_date=skip_before)
                    if not new_rows:
                        set_attributes(get_current_span(), row_count=0,
                                       up_to_date=True)
                        continue
                source_rows = _fetch_source_data(
                    conn,
                    data_id,
                    source_key,
                    source_features,
                    lookback_before=skip_before,
                    lookback_rows=lookback_rows,
                )
                set_attributes(get_current_span(), row_count=len(source_rows))
            if timings is not None and timings_lock is not None:
                elapsed = time.monotonic() - fetch_start
                with timings_lock:
                    timings["fetch"] += elapsed

            if not source_rows:
                continue

            # Call compute function
            try:
                compute_start = time.monotonic()

                with create_span(
                    "compute.execute",
                    function_name=function_name,
                    spec_count=len(compute_specs),
                    data_id=data_id
                ):
                    # Inspect function signature to determine what parameters to pass
                    sig = inspect.signature(compute_func)
                    params_to_pass = {}

                    # Always pass source_rows and compute_specs
                    # (passed as positional args, not in params_to_pass)

                    # Optional: cache and cache_lock
                    if 'cache' in sig.parameters and cache is not None:
                        params_to_pass['cache'] = cache
                    if 'cache_lock' in sig.parameters and cache_lock is not None:
                        params_to_pass['cache_lock'] = cache_lock

                    # Optional: db_conn (for meta-functions using plugins)
                    if 'db_conn' in sig.parameters:
                        params_to_pass['db_conn'] = conn

                    # Call function with appropriate parameters
                    computed_rows = compute_func(source_rows, compute_specs, **params_to_pass)
                    set_attributes(get_current_span(), result_count=len(computed_rows) if computed_rows else 0)

                if timings is not None and timings_lock is not None:
                    elapsed = time.monotonic() - compute_start
                    with timings_lock:
                        timings["compute"] += elapsed

                if not computed_rows:
                    continue

                # Build feature_map for insert
                # Map output column names to feature IDs
                # Use params.column if specified, otherwise use feature name
                with create_span("transform.build_feature_map", feature_count=len(source_features)):
                    feature_map = {}
                    for f in source_features:
                        feature_id = f[0]
                        feature_name = f[1]
                        params = f[3]  # params dict
                        # Use column name from params, or fall back to feature name
                        column_name = params.get('column', feature_name)
                        feature_map[column_name] = feature_id

                # Insert results (pipelined if writer provided)
                if writer:
                    write_start = time.monotonic()
                    with create_span(
                        "insert.enqueue",
                        row_count=len(computed_rows),
                        feature_count=len(feature_map)
                    ):
                        inserted = writer(computed_rows, feature_map,
                                          skip_before)
                    if timings is not None and timings_lock is not None:
                        elapsed = time.monotonic() - write_start
                        with timings_lock:
                            timings["write"] += elapsed
                else:
                    write_start = time.monotonic()
                    with create_span(
                        "insert.direct",
                        row_count=len(computed_rows),
                        feature_count=len(feature_map),
                        data_id=data_id
                    ):
                        inserted = insert_computed_features(
                            conn,
                            data_id=data_id,
                            rows=computed_rows,
                            feature_map=feature_map,
                            update_existing=update_existing,
                            skip_before=skip_before,
                            batch_size=feature_batch_size,
                        )
                    if timings is not None and timings_lock is not None:
                        elapsed = time.monotonic() - write_start
                        with timings_lock:
                            timings["write"] += elapsed

                total_inserted += inserted

            except Exception as exc:
                errors.append({
                    'error': str(exc),
                    'function_name': function_name,
                    'features': [f[1] for f in source_features],
                })

        except Exception as exc:
            errors.append({
                'error': str(exc),
                'source': source_key,
                'features': [f[1] for f in source_features],
            })

    return {
        'inserted': total_inserted,
        'errors': errors,
    }


def _resolve_compute_function(conn: psycopg.Connection, function_name: str) -> Optional[Callable]:
    """
    Resolve a compute function, preferring DB-registered functions over code registry.
    Cached to avoid repeated DB lookups and exec.
    """
    if function_name in _FUNCTION_CACHE:
        return _FUNCTION_CACHE[function_name]

    db_func = _load_db_function(conn, function_name)
    if db_func:
        fn, version = db_func
        _FUNCTION_CACHE[function_name] = fn
        _FUNCTION_CACHE_SOURCE[function_name] = f"db:{version}" if version else "db"
        return fn

    return None


def _load_db_function(conn: psycopg.Connection, function_name: str) -> Optional[Tuple[Callable, Optional[str]]]:
    """Load a compute function from feature_functions table if present."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT language, function_body, version
            FROM feature_functions
            WHERE enabled = TRUE AND status = 'active' AND name = %s
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1;
            """,
            (function_name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    language, body, version = row
    # Accept both "python" (preferred) and "python_expr" (legacy) for backward compatibility
    if language not in ("python_expr", "python"):
        warnings.warn(f"Ignoring feature_function '{function_name}' with unsupported language '{language}'")
        return None

    local_env = _exec_in_sandbox(body, warn_context=f"feature_function '{function_name}'")
    if local_env is None:
        return None
    fn = local_env.get("compute") or local_env.get(function_name)
    if not callable(fn):
        warnings.warn(f"feature_function '{function_name}' did not define a callable 'compute'")
        return None
    return _wrap_db_function(fn), version


class SandboxExecutionError(RuntimeError):
    """Raised when sandboxed code fails to execute or lacks required callables."""


def exec_sandboxed(body: str, *callable_names: str) -> Dict[str, Callable]:
    """Execute code in the whitelisted-import sandbox; return the named callables.

    The same execution path as AI-generated feature functions — used by the
    regime-discovery detector runtime (spec 006, R5). Raises
    SandboxExecutionError on execution failure or a missing callable.
    """
    local_env = _exec_in_sandbox(body, warn_context=None)
    if local_env is None:
        raise SandboxExecutionError("sandboxed code failed to execute")
    out: Dict[str, Callable] = {}
    for name in callable_names:
        fn = local_env.get(name)
        if not callable(fn):
            raise SandboxExecutionError(f"sandboxed code did not define a callable {name!r}")
        out[name] = fn
    return out


def _exec_in_sandbox(body: str, warn_context: Optional[str],
                     raise_errors: bool = False) -> Optional[Dict[str, Any]]:
    """Exec `body` in the restricted environment; return its locals, or None
    (with a warning when warn_context is given) on failure."""
    # Create a safe __import__ that only allows whitelisted modules
    SAFE_MODULES = {
        'numpy', 'np', 'pandas', 'pd', 'datetime', 'math', 'statistics',
        'talib', 'scipy', 'sklearn', 'json', 're', 'itertools', 'functools',
        'operator', 'collections', 'typing'
    }

    # Capture the real __import__ before we override __builtins__
    real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__

    def safe_import(name, *args, **kwargs):
        """Only allow imports of safe, pre-approved modules."""
        if name.split('.')[0] not in SAFE_MODULES:
            raise ImportError(f"Import of '{name}' is not allowed for security reasons")
        return real_import(name, *args, **kwargs)

    # Create a restricted execution environment to prevent malicious code
    # Block dangerous built-ins: file I/O, eval, exec, compile
    # But allow safe imports via safe_import function
    safe_builtins = {
        # Type constructors
        'int': int,
        'float': float,
        'str': str,
        'bool': bool,
        'list': list,
        'dict': dict,
        'tuple': tuple,
        'set': set,
        'frozenset': frozenset,
        # Utility functions
        'len': len,
        'range': range,
        'enumerate': enumerate,
        'zip': zip,
        'map': map,
        'filter': filter,
        'sum': sum,
        'min': min,
        'max': max,
        'abs': abs,
        'round': round,
        'sorted': sorted,
        'reversed': reversed,
        'any': any,
        'all': all,
        # Type checking
        'isinstance': isinstance,
        'issubclass': issubclass,
        'type': type,
        # Exceptions (needed for try/except)
        'Exception': Exception,
        'ValueError': ValueError,
        'TypeError': TypeError,
        'KeyError': KeyError,
        'IndexError': IndexError,
        'AttributeError': AttributeError,
        'ZeroDivisionError': ZeroDivisionError,
        # Other safe built-ins
        'None': None,
        'True': True,
        'False': False,
        # Safe import function (allows whitelisted modules only)
        '__import__': safe_import,
    }

    # Pre-import commonly needed modules for feature computations
    # This avoids __import__ warnings while maintaining security
    safe_modules = {}
    try:
        safe_modules['datetime'] = __import__('datetime')
        safe_modules['np'] = __import__('numpy')
        safe_modules['pd'] = __import__('pandas')
        safe_modules['numpy'] = __import__('numpy')
        safe_modules['pandas'] = __import__('pandas')
    except ImportError:
        pass  # Optional dependencies

    # Try to import optional but common libraries
    try:
        safe_modules['talib'] = __import__('talib')
    except ImportError:
        pass

    try:
        safe_modules['scipy'] = __import__('scipy')
    except ImportError:
        pass

    try:
        safe_modules['sklearn'] = __import__('sklearn')
    except ImportError:
        pass

    safe_globals = {
        '__builtins__': safe_builtins,
        **safe_modules,
    }

    local_env: Dict[str, Any] = {}
    try:
        exec(body, safe_globals, local_env)
    except Exception as exc:
        if raise_errors:
            raise
        if warn_context:
            warnings.warn(f"Failed to exec {warn_context}: {exc}")
        return None
    return local_env


def _wrap_db_function(fn: Callable) -> Callable:
    """
    Adapt a DB function to dispatcher signature (rows, specs, cache=None, cache_lock=None).

    Supports two patterns:
    - existing dispatcher-style (rows, specs, cache=None, cache_lock=None) -> list of row dicts
    - simple pandas-style functions: compute(df, cache=None, cache_lock=None, **params) -> series/iterable
      In this case, we build rows per spec with column name from spec/feature.

    Caching support:
    - If the function accepts a 'cache' parameter, it will be passed through
    - If the function accepts a 'cache_lock' parameter, it will be passed through
    - This allows features to cache expensive intermediate calculations with thread safety
    - Cache is shared across all features for a given stock
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    param_names = [p.name for p in params]
    accepts_cache = 'cache' in param_names
    accepts_cache_lock = 'cache_lock' in param_names

    if params:
        first, *rest = params
        if first.kind == inspect.Parameter.VAR_POSITIONAL:
            return fn
        if len(params) >= 2 and first.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            second = params[1]
            # Dispatcher-style signature rows/specs: pass through
            if first.name in ("rows", "source_rows") and second.name in ("specs", "features", "feature_specs"):
                return fn
            if second.name in ("specs", "features", "feature_specs"):
                return fn

    def adapter(rows: List[Dict[str, Any]], specs: List[Dict[str, Any]], cache: Optional[Dict[str, Any]] = None, cache_lock: Optional[threading.Lock] = None) -> List[Dict[str, Any]]:
        if not rows or not specs:
            return []
        try:
            import pandas as pd
        except Exception as exc:
            warnings.warn(f"feature_function pandas import failed: {exc}")
            return []
        df = pd.DataFrame(rows)
        if df.empty:
            return []
        # Normalize column names
        df.columns = [str(c) for c in df.columns]
        if "date" not in df.columns:
            return []
        out: List[Dict[str, Any]] = []
        for spec in specs:
            # Strip dispatcher-specific keys
            params = {k: v for k, v in spec.items() if k not in ("name", "feature_id")}

            # Add cache if function accepts it
            if accepts_cache and cache is not None:
                params['cache'] = cache

            # Add cache_lock if function accepts it
            if accepts_cache_lock and cache_lock is not None:
                params['cache_lock'] = cache_lock

            try:
                series = fn(df, **params)
            except Exception as exc:
                warnings.warn(f"feature_function '{fn.__name__}' execution failed: {exc}")
                continue
            if series is None:
                continue
            try:
                iterable = list(series)
            except Exception:
                continue
            col_name = spec.get("column") or spec.get("name")
            for d, v in zip(df["date"], iterable):
                out.append({"date": d, col_name: v, "source": "fx"})
        return out

    return adapter


def _latest_dates_for_features(
    conn: psycopg.Connection,
    data_id: int,
    feature_ids: List[int],
) -> Dict[int, Optional[date]]:
    """Return per-feature latest date for the given data_id.

    Uses a lateral join to read only the most recent row per feature from the
    existing (feature_id, data_id, date DESC) index.  On TimescaleDB this
    enables chunk exclusion — each lateral probe touches only the newest chunk
    instead of scanning all chunks via MAX(date).
    """
    if not feature_ids:
        return {}
    placeholders = ",".join(["%s"] * len(feature_ids))
    query = f"""
    SELECT f.id, latest.date
    FROM (SELECT unnest(ARRAY[{placeholders}]) AS id) f
    LEFT JOIN LATERAL (
        SELECT date
        FROM computed_features
        WHERE feature_id = f.id AND data_id = %s
        ORDER BY date DESC
        LIMIT 1
    ) latest ON true
    """
    params: List[Any] = feature_ids + [data_id]
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return {fid: dt for fid, dt in rows}


def _group_by_source(features: List[Tuple]) -> Dict[Tuple[str, str], List[Tuple]]:
    """
    Group features by (source_table, source_column) for efficient fetching.

    Returns: {(source_table, source_column): [features]}
    """
    grouped: Dict[Tuple[str, str], List[Tuple]] = {}

    for feature in features:
        source_table = feature[4]
        source_column = feature[5]
        key = (source_table, source_column)
        grouped.setdefault(key, []).append(feature)

    return grouped


def _fetch_source_data(
    conn: psycopg.Connection,
    data_id: int,
    source_key: Tuple[str, str],
    features: List[Tuple],
    start_date: Optional[date] = None,
    lookback_before: Optional[date] = None,
    lookback_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch source data based on source_table and source_column.

    Handles different source types:
    - stock_ohlcv: Direct column access
    - computed_features: Requires feature_id lookup

    lookback_before + lookback_rows bound the fetch to the LAST N rows at or
    before the cutoff plus everything after it (#120 item 1b) — the window
    the function declared it needs to reproduce full-history values.
    """
    source_table, source_column = source_key

    if source_table == 'computed_features':
        return _fetch_from_computed_features(
            conn, data_id, features, start_date, lookback_before, lookback_rows
        )
    elif source_table == 'stock_ohlcv':
        return _fetch_from_stock_ohlcv(
            conn, data_id, source_column, start_date, lookback_before,
            lookback_rows
        )
    else:
        # Generic table fetch
        return _fetch_from_generic_table(
            conn, data_id, source_table, source_column, start_date,
            lookback_before, lookback_rows
        )


def _fetch_from_computed_features(
    conn: psycopg.Connection,
    data_id: int,
    features: List[Tuple],
    start_date: Optional[date],
    lookback_before: Optional[date] = None,
    lookback_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch from computed_features table.

    For derivatives, params.source_feature specifies which feature to fetch.
    """
    # Extract source_feature from first feature's params
    # (all features in group should have same source)
    params = features[0][3]  # params dict
    source_feature_name = params.get('source_feature')

    if not source_feature_name:
        return []

    # Look up source feature_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM feature_definitions WHERE name = %s",
            (source_feature_name,)
        )
        row = cur.fetchone()

    if not row:
        return []

    source_feature_id = row[0]

    # Fetch computed feature values
    if lookback_before is not None and lookback_rows is not None:
        query = """
        SELECT date, value FROM (
            (SELECT date, value FROM computed_features
             WHERE data_id = %s AND feature_id = %s AND date <= %s
             ORDER BY date DESC LIMIT %s)
            UNION ALL
            (SELECT date, value FROM computed_features
             WHERE data_id = %s AND feature_id = %s AND date > %s)
        ) w ORDER BY date
        """
        query_params = [data_id, source_feature_id, lookback_before,
                        lookback_rows, data_id, source_feature_id,
                        lookback_before]
    else:
        query = """
        SELECT date, value
        FROM computed_features
        WHERE data_id = %s AND feature_id = %s
        """
        query_params = [data_id, source_feature_id]

        if start_date:
            query += " AND date > %s"
            query_params.append(start_date)

        query += " ORDER BY date"

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    return [
        {'date': row[0], 'value': row[1]}
        for row in rows
    ]


def _fetch_from_stock_ohlcv(
    conn: psycopg.Connection,
    data_id: int,
    column: str,
    start_date: Optional[date],
    lookback_before: Optional[date] = None,
    lookback_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch from stock_ohlcv table."""
    # For OHLC-based features, we fetch all price columns
    # Column parameter specifies which column is primary, but we fetch all for flexibility
    cols = "date, open, high, low, close, adjusted_close, volume"
    if lookback_before is not None and lookback_rows is not None:
        # bounded: last N rows at/before the cutoff + everything after
        query = f"""
        SELECT {cols} FROM (
            (SELECT {cols} FROM stock_ohlcv
             WHERE data_id = %s AND date <= %s
             ORDER BY date DESC LIMIT %s)
            UNION ALL
            (SELECT {cols} FROM stock_ohlcv
             WHERE data_id = %s AND date > %s)
        ) w ORDER BY date
        """
        query_params = [data_id, lookback_before, lookback_rows,
                        data_id, lookback_before]
    else:
        query = f"""
        SELECT {cols}
        FROM stock_ohlcv
        WHERE data_id = %s
        """
        query_params = [data_id]

        if start_date:
            query += " AND date > %s"
            query_params.append(start_date)

        query += " ORDER BY date"

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    return [
        {
            'date': row[0],
            'open': row[1],
            'high': row[2],
            'low': row[3],
            'close': row[4],
            'adjusted_close': row[5],
            'volume': row[6],
        }
        for row in rows
    ]


def _fetch_from_generic_table(
    conn: psycopg.Connection,
    data_id: int,
    table: str,
    column: str,
    start_date: Optional[date],
    lookback_before: Optional[date] = None,
    lookback_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch from a generic table."""
    # Compose with sql.Composed parts — str()-ing a Composed yields its repr,
    # not SQL (the bug this path carried until spec 008 began relying on it).
    if lookback_before is not None and lookback_rows is not None:
        query = sql.SQL(
            "SELECT date, {column} FROM ("
            "(SELECT date, {column} FROM {table}"
            " WHERE data_id = %s AND date <= %s ORDER BY date DESC LIMIT %s)"
            " UNION ALL "
            "(SELECT date, {column} FROM {table}"
            " WHERE data_id = %s AND date > %s)"
            ") w ORDER BY date"
        ).format(table=sql.Identifier(table), column=sql.Identifier(column))
        query_params: List[Any] = [data_id, lookback_before, lookback_rows,
                                   data_id, lookback_before]
    else:
        parts = [sql.SQL("SELECT date, {column} FROM {table} WHERE data_id = %s").format(
            table=sql.Identifier(table), column=sql.Identifier(column))]
        query_params = [data_id]

        if start_date:
            parts.append(sql.SQL(" AND date > %s"))
            query_params.append(start_date)

        parts.append(sql.SQL(" ORDER BY date"))
        query = sql.SQL(" ").join(parts)

    with conn.cursor() as cur:
        cur.execute(query, query_params)
        rows = cur.fetchall()

    # Data quality (spec 008): drop source values convicted as provider trash
    # so they never enter the feature store — the single chokepoint that keeps
    # every downstream consumer clean. Guarded: a quality-check failure must
    # never break feature computation.
    try:
        from gefion.quality import exclusions
        skip = exclusions.convicted_dates(conn, table, data_id, column)
    except Exception:  # pragma: no cover - defensive
        skip = set()

    return [
        {'date': row[0], 'value': row[1]}
        for row in rows
        if row[0] not in skip
    ]


# Register generic compute function


# --- market-level execution mode (spec 011, epic #114) --------------------------------

class MarketFunctionError(RuntimeError):
    """A market-scope body failed: sandbox refusal, raise, or wrong shape.
    Isolated per function — the caller writes NOTHING for this function."""


def run_market_function(conn, fn_row: Dict[str, Any], start=None,
                        min_stocks: int = 100,
                        itersize: int = 50_000) -> Dict[str, Any]:
    """Execute one scope='market' registry function over the stock
    cross-section, per date, in the standard sandbox (spec 011).

    The body must define `compute(rows) -> float | None` (see
    specs/011-market-dispatcher/contracts/function-contract.md). Streams ONE
    ordered query; a date's rows are grouped in memory (bounded: one date in
    flight); dates thinner than `min_stocks` never reach the body (gap by
    policy). None/NaN/inf results are gaps; raises and wrong-shaped returns
    are MarketFunctionError (write-on-success is the caller's contract).
    """
    import math

    with create_span("features.dispatcher.market_function",
                     function=fn_row["name"], min_stocks=min_stocks) as span:
        env = None
        try:
            env = _exec_in_sandbox(fn_row["function_body"], None,
                                   raise_errors=True)
        except Exception as exc:
            raise MarketFunctionError(
                f"{fn_row['name']}: body failed to load: {exc}") from exc
        compute = (env or {}).get("compute")
        if not callable(compute):
            raise MarketFunctionError(
                f"{fn_row['name']}: body must define compute(rows)")

        inputs = ((fn_row.get("inputs") or {}).get("features")
                  if fn_row.get("inputs") else None) or []
        # validate declared features and collect ids (parameterized)
        feature_ids: Dict[str, int] = {}
        with conn.cursor() as cur:
            for feat in inputs:
                if feat == "ret_20":
                    continue          # computed in-query, not a stored feature
                cur.execute("SELECT id FROM feature_definitions WHERE name = %s",
                            (feat,))
                row = cur.fetchone()
                if row is None:
                    raise MarketFunctionError(
                        f"{fn_row['name']}: declared input {feat!r} is not a "
                        f"known feature definition")
                feature_ids[feat] = row[0]

        join_sql, select_sql = "", ""
        params: Dict[str, Any] = {"start": start}
        for i, (feat, fid) in enumerate(feature_ids.items()):
            a = f"cf{i}"
            join_sql += (f" LEFT JOIN computed_features {a} ON {a}.data_id = o.data_id "
                         f"AND {a}.date = o.date AND {a}.feature_id = %({a}_id)s")
            select_sql += f", {a}.value AS f{i}"
            params[f"{a}_id"] = fid
        ret20 = "ret_20" in inputs
        ret_sql = (", o.close / NULLIF(LAG(o.close, 20) OVER "
                   "(PARTITION BY o.data_id ORDER BY o.date), 0) - 1 AS ret_20"
                   if ret20 else "")

        # ret_20 is a SQL window (LAG 20) over THIS stream — filtering the
        # stream to date > start starves the lag and history-dependent
        # bodies silently emit nothing (same defect class as #129, market
        # path). Warm the window up with 60 calendar days (≥ ~40 trading
        # days ≥ the 20-row lag) before the cutoff; emit only dates after it.
        params["warmup"] = (start - timedelta(days=60)) if start else None
        sql = f"""
            SELECT * FROM (
                SELECT o.date, s.symbol, o.close, o.high, o.low, o.volume,
                       s.sector
                       {select_sql}{ret_sql}
                FROM stock_ohlcv o
                JOIN stocks s ON s.id = o.data_id AND s.asset_type = 'Stock'
                {join_sql}
                WHERE o.close > 0
                  AND (%(warmup)s::date IS NULL OR o.date > %(warmup)s)
            ) w
            WHERE (%(start)s::date IS NULL OR w.date > %(start)s)
            ORDER BY w.date
        """
        feat_names = list(feature_ids.keys())

        def _rows_to_dicts(batch):
            out = []
            for r in batch:
                d = {"symbol": r[1], "close": float(r[2]),
                     "high": float(r[3]) if r[3] is not None else None,
                     "low": float(r[4]) if r[4] is not None else None,
                     "volume": int(r[5]) if r[5] is not None else None,
                     "sector": r[6]}
                for i, feat in enumerate(feat_names):
                    v = r[7 + i]
                    if v is not None:
                        d[feat] = float(v)
                if ret20:
                    v = r[7 + len(feat_names)]
                    if v is not None:
                        d["ret_20"] = float(v)
                out.append(d)
            return out

        values, gaps = [], 0

        def _finish(date, day_rows):
            nonlocal gaps
            if len(day_rows) < min_stocks:
                gaps += 1
                return
            try:
                result = compute(_rows_to_dicts(day_rows))
            except Exception as exc:
                raise MarketFunctionError(
                    f"{fn_row['name']}: compute raised on {date}: {exc}") from exc
            if result is None:
                gaps += 1
                return
            if isinstance(result, bool) or not isinstance(result, (int, float)):
                raise MarketFunctionError(
                    f"{fn_row['name']}: compute returned "
                    f"{type(result).__name__!r} on {date} — must be float or None")
            result = float(result)
            if not math.isfinite(result):
                gaps += 1
                return
            values.append((date, result))

        # server-side (named) cursor needs a transaction even under autocommit
        with conn.transaction(), \
             conn.cursor(name=f"market_fn_{fn_row['id']}") as cur:
            cur.itersize = itersize
            cur.execute(sql, params)
            cur_date, day_rows = None, []
            for row in cur:
                if row[0] != cur_date:
                    if cur_date is not None:
                        _finish(cur_date, day_rows)
                    cur_date, day_rows = row[0], []
                day_rows.append(row)
            if cur_date is not None:
                _finish(cur_date, day_rows)

        set_attributes(span, values=len(values), gaps=gaps)
        return {"values": values, "gaps": gaps}


def run_composite_function(conn, fn_row: Dict[str, Any],
                           start=None) -> Dict[str, Any]:
    """Execute one composite market function (spec 014): declared inputs are
    NAMED MACRO SERIES ({"series": [...]}); per trading date the body's
    compute(row) receives that date's stored values and returns float | None.

    Same honesty rules as the cross-section mode (011): a date missing ANY
    declared input is a gap that never reaches the body; None/NaN/inf are
    gaps; raises and wrong-shaped returns are MarketFunctionError
    (write-on-success stays the caller's contract). Causality is inherited
    from the stored inputs — the body sees one date at a time.
    """
    import math

    with create_span("features.dispatcher.composite_function",
                     function=fn_row["name"]) as span:
        env = None
        try:
            env = _exec_in_sandbox(fn_row["function_body"], None,
                                   raise_errors=True)
        except Exception as exc:
            raise MarketFunctionError(
                f"{fn_row['name']}: body failed to load: {exc}") from exc
        compute = (env or {}).get("compute")
        if not callable(compute):
            raise MarketFunctionError(
                f"{fn_row['name']}: body must define compute(row)")

        series = ((fn_row.get("inputs") or {}).get("series")) or []
        if not series:
            raise MarketFunctionError(
                f"{fn_row['name']}: composite declares no input series")

        with conn.cursor() as cur:
            cur.execute(
                """SELECT v.date, s.name, v.value
                   FROM macro_series_values v
                   JOIN macro_series s ON s.id = v.series_id
                   WHERE s.name = ANY(%s)
                     AND (%s::date IS NULL OR v.date > %s)
                   ORDER BY v.date""",
                (list(series), start, start))
            rows = cur.fetchall()

        by_date: Dict[Any, Dict[str, float]] = {}
        for d, name, value in rows:
            by_date.setdefault(d, {})[name] = float(value)

        values, gaps = [], 0
        for d in sorted(by_date):
            row = by_date[d]
            if len(row) < len(series):
                gaps += 1          # missing input = gap, never imputed
                continue
            try:
                result = compute(row)
            except Exception as exc:
                raise MarketFunctionError(
                    f"{fn_row['name']}: compute raised on {d}: {exc}") from exc
            if result is None:
                gaps += 1
                continue
            if isinstance(result, bool) or not isinstance(result, (int, float)):
                raise MarketFunctionError(
                    f"{fn_row['name']}: compute returned "
                    f"{type(result).__name__!r} on {d} — must be float or None")
            result = float(result)
            if not math.isfinite(result):
                gaps += 1
                continue
            values.append((d, result))

        set_attributes(span, values=len(values), gaps=gaps)
        return {"values": values, "gaps": gaps}
