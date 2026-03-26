# Critical Bug Fix: Writer Thread Deadlock

## Summary

Fixed a critical deadlock bug in [dispatcher.py:186-237](../src/gefion/features/dispatcher.py#L186-L237) where writer threads that failed to acquire database connections would exit silently, leaving events unset and causing the main thread to wait forever.

## The Bug

### Symptoms

Production g2 process on sloth machine became completely stuck:
- No CPU usage
- No database activity
- Process unresponsive to signals
- py-spy stack trace showed:
  - MainThread waiting at [dispatcher.py:323](../src/gefion/features/dispatcher.py#L323) in `evt.wait()`
  - Worker threads stuck waiting for writer threads to complete

### Root Cause

In the `writer_loop()` function, if `db_pool.get_connection()` failed:

```python
def writer_loop():
    from gefion.db import pool as db_pool
    try:
        with db_pool.get_connection() as writer_conn:  # <-- Can fail here
            # ... process queue items and set events ...
    except Exception as exc:
        # Connection acquisition failure
        writer_errors.append(exc)  # <-- Thread exits silently!
```

**What happened:**
1. Writer thread failed to acquire connection from pool (pool exhausted)
2. Outer exception handler caught the exception
3. Thread exited immediately **without processing queue items**
4. **Events in the queue were never set**
5. Main thread waited forever at `evt.wait()`

This is a **catastrophic failure mode** that causes complete system deadlock.

### Why It Happened on Sloth

The ResourceAwareAdaptiveLimiter scaled up workers aggressively, which caused:
- High number of parallel compute workers (max_workers)
- Each worker spawning multiple writer threads (writer_workers)
- Total database connections needed: `max_workers × (1 + writer_workers)`
- Connection pool exhausted
- Writer threads failed to acquire connections
- Silent deadlock

## The Fix

Added two critical safety mechanisms:

### 1. Drain Queue on Connection Failure

```python
except Exception as exc:
    # Connection acquisition failure - critical error
    # Drain the queue and set all events to prevent deadlock
    writer_errors.append(exc)
    try:
        while True:
            item = write_queue.get_nowait()
            if item is not stop_token:
                evt = item.get("event")
                if evt:
                    evt.set()  # <-- Prevent deadlock
            write_queue.task_done()
    except queue.Empty:
        pass
```

### 2. Set Event on Write Failure

```python
except Exception as exc:
    writer_errors.append(exc)
    # Still set the event even on error to avoid deadlock
    evt = item.get("event")
    if evt:
        evt.set()  # <-- Prevent deadlock
```

## Testing

Added comprehensive tests in [test_writer_connection_failure_deadlock.py](../tests/test_writer_connection_failure_deadlock.py):

1. `test_writer_thread_event_queue_drain_on_connection_failure()`: Verifies queue is drained and all events are set when connection acquisition fails
2. `test_inner_exception_sets_event()`: Verifies events are set even when write operations fail

All tests pass without deadlocking.

## Prevention

To prevent this issue in production:

### 1. Connection Pool Sizing

Ensure connection pool size accounts for dynamic worker scaling:

```python
max_possible_connections = max_workers × (1 + max_writer_workers)
pool_size = max_possible_connections + buffer (e.g., +10)
```

### 2. Resource-Aware Scaling

The ResourceAwareAdaptiveLimiter should consider connection pool size:

```python
available_db_connections = pool.max_size - buffer
connections_per_worker = 1 + writer_workers
max_workers = available_db_connections // connections_per_worker
```

This is already implemented in [adaptive.py:154-164](../src/gefion/utils/adaptive.py#L154-L164).

### 3. Monitor Writer Errors

Check `writer_errors` list after feature computation:

```python
if writer_errors:
    warnings.warn(f"Writer thread errors: {writer_errors}")
    # Consider failing fast instead of silent degradation
```

## Related Issues

- Connection pool exhaustion under high parallelism
- Need for better error propagation from writer threads
- Silent failures in background threads

## Timeline

- **2025-12-09**: Bug identified in production (sloth machine)
- **2025-12-10**: Root cause diagnosed via py-spy stack trace
- **2025-12-10**: Fix implemented and tested
- **Status**: Fixed, ready for deployment

## Verification

To verify the fix works on sloth:

1. Kill the stuck process: `kill -9 <pid>`
2. Restart with the fix deployed
3. Monitor that writer threads no longer cause deadlocks
4. Check logs for writer connection errors (should fail fast now instead of deadlocking)

## Lessons Learned

1. **Always set events**: Any code path that exits a thread must set pending events
2. **Fail fast**: Silent failures in background threads are dangerous
3. **Test failure modes**: Test what happens when resources are exhausted
4. **Monitor background threads**: Add logging/monitoring for thread failures
5. **Connection pool sizing matters**: Dynamic scaling needs careful pool sizing
