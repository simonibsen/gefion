# Parallelization Implementation Guide

## Current Status: **IMPLEMENTED** ✅

## Overview

Parallelize execution of feature function groups within a single stock to leverage multiple CPU cores.

**Expected Impact**: 2-4x speedup depending on number of function groups and available cores

## Current Sequential Execution

```python
# In compute_features()
for func_name, features in grouped_by_function.items():
    func_result = _process_function_group(conn, data_id, func_name, features, cache=cache, ...)
    results[func_name] = func_result
```

**Problem**: If stock has 5 function groups taking 6 seconds each = 30 seconds total, but only 1 CPU core utilized

## Proposed Parallel Execution

```python
# In compute_features()
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

num_cores = multiprocessing.cpu_count()
max_parallel = max(2, num_cores - 2)  # Leave cores for writer threads

with ThreadPoolExecutor(max_workers=max_parallel) as executor:
    # Each worker gets its own connection from pool
    futures = {}
    for func_name, features in grouped_by_function.items():
        future = executor.submit(
            _process_function_group_with_connection,  # New wrapper function
            data_id,
            func_name,
            features,
            incremental,
            update_existing,
            latest_by_feature,
            feature_batch_size,
            writer,
            timings,
            timings_lock,
            cache,
            cache_lock,  # New: protect cache access
        )
        futures[future] = func_name

    # Collect results as they complete
    for future in as_completed(futures):
        func_name = futures[future]
        try:
            func_result = future.result()
            results[func_name] = func_result
        except Exception as exc:
            # Handle error
            results[func_name] = {'inserted': 0, 'errors': [str(exc)]}
```

## Thread-Safety Requirements

### 1. Database Connections

**Problem**: `psycopg.Connection` objects are NOT thread-safe

**Solution**: Each parallel worker must get its own connection from pool

```python
def _process_function_group_with_connection(
    data_id,
    func_name,
    features,
    # ... other params
):
    """Wrapper that acquires own connection for thread safety."""
    from gefion.db import pool as db_pool

    with db_pool.get_connection() as conn:
        conn.autocommit = True
        return _process_function_group(
            conn,
            data_id,
            func_name,
            features,
            # ... pass through params
        )
```

### 2. Cache Access

**Problem**: Python dicts are not thread-safe for concurrent writes

**Solution**: Protect cache with `threading.Lock`

```python
# In compute_features()
cache: Dict[str, Any] = {}
cache_lock = threading.Lock()

# In feature functions (updated adapter):
def adapter(rows, specs, cache=None, cache_lock=None):
    # ...
    if accepts_cache and cache is not None:
        params['cache'] = cache
        if cache_lock is not None:
            params['cache_lock'] = cache_lock

    series = fn(df, **params)
    # ...
```

**Feature function usage**:
```python
def compute(df, cache=None, cache_lock=None):
    # Thread-safe cache access
    if cache is not None and cache_lock is not None:
        with cache_lock:
            if 'ma20' in cache:
                return cache['ma20']

    # Compute
    ma20 = df['close'].rolling(20).mean()

    # Thread-safe cache write
    if cache is not None and cache_lock is not None:
        with cache_lock:
            cache['ma20'] = ma20

    return ma20
```

### 3. Timing Accumulation

**Already thread-safe**: `timings` dict is protected by `timings_lock` ✅

### 4. Writer Queue

**Already thread-safe**: `queue.Queue` is thread-safe ✅

## Connection Pool Sizing

With parallel function execution, we need more connections:

**Current formula**:
```python
max_pool = max_workers * (1 + writer_workers) + buffer
```

**Updated formula for parallel execution**:
```python
# max_workers: stocks processed in parallel (outer parallelism)
# parallel_functions: function groups in parallel (inner parallelism)
# writer_workers: writer threads per stock

max_pool = max_workers * parallel_functions * (1 + writer_workers) + buffer
```

**Example**:
- 10 stocks in parallel (max_workers)
- 4 function groups per stock in parallel (new!)
- 4 writer threads per stock
- Buffer: 5

```
max_pool = 10 * 4 * (1 + 4) + 5 = 10 * 4 * 5 + 5 = 205 connections
```

This could be too high. **Alternative**: Limit inner parallelism or use worker pool differently.

**Better approach**: Use a global pool of workers for function groups, not per-stock:

```python
# Create ONE executor for ALL stocks' function groups
with ThreadPoolExecutor(max_workers=num_cores) as function_executor:
    for data_id in stocks:
        # Submit all function groups for this stock
        for func_name, features in grouped_by_function.items():
            function_executor.submit(...)
```

This limits total parallel function executions to `num_cores`, avoiding connection explosion.

## Implementation Steps

1. ✅ **Add cache_lock parameter** to adapter and feature execution path
2. ✅ **Create wrapper function** `_process_function_group_with_connection()` to acquire own connection
3. ✅ **Replace sequential loop** with `ThreadPoolExecutor` execution
4. ⏭️ **Update connection pool sizing** to account for parallel functions (not needed - uses existing pool)
5. ✅ **Add configuration flag** `--parallel-functions` to enable (default: False for safety)
6. ⏭️ **Test thoroughly** with real workload to verify thread safety (ready for testing)

## Usage

Enable parallel function execution with the CLI flag:

```bash
# Enable parallel function execution (uses cpu_count - 2 workers)
g2 features-compute --all-features --parallel-functions

# Limit parallel function workers
g2 features-compute --all-features --parallel-functions --max-parallel-functions 4
```

**Note**: Parallel function execution is disabled by default for safety. Enable it when you have:
- Multiple function groups per stock (e.g., indicator + derivative)
- Sufficient CPU cores available
- Confirmed thread-safe feature functions

## Performance Testing Plan

1. **Baseline**: Run with `--parallel-functions=False` (current sequential)
2. **Parallel**: Run with `--parallel-functions=True --parallel-workers=4`
3. **Compare**:
   - Total execution time
   - CPU utilization (should be higher with parallel)
   - Memory usage
   - Database connection count

## Expected Results

**Test scenario**: 5578 stocks, 5 function groups/stock, 6 sec/group

- **Sequential**: 30 sec/stock × 5578 = 46.5 hours
- **Parallel (4 cores)**: 9 sec/stock × 5578 = 13.9 hours
- **Speedup**: 3.3x

Combined with **caching** (2-5x):
- Caching + Parallel: 3-6 sec/stock × 5578 = **4.6-9.3 hours**

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Connection pool exhaustion | Job fails | Increase pool size, limit parallelism |
| Race conditions in cache | Incorrect results | Thorough Lock usage, testing |
| Deadlocks | Job hangs | Careful Lock ordering, timeouts |
| Memory pressure | OOM crashes | Limit parallel workers, monitor memory |

## Recommendation

**Phase 1**: Test caching optimization first (already implemented)
- Lower risk
- Good performance gain
- Easier to debug

**Phase 2**: Implement parallelization if Phase 1 results warrant it
- Higher complexity
- Additional performance gain
- Requires careful testing

## Alternative: Multiprocessing

If ThreadPoolExecutor doesn't provide enough speedup (due to GIL), consider:

```python
from concurrent.futures import ProcessPoolExecutor

# Each process has own Python interpreter (no GIL contention)
# But: Higher memory overhead, need to pickle data
```

**Trade-off**: Processes bypass GIL but have higher overhead. Threads are lighter but GIL-limited.

Since pandas/numpy release GIL for operations, threads should work well.

---

**Status**: Implementation complete, ready for testing with production workloads
