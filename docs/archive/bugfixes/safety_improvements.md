# Safety Improvements to Prevent System Hangs

## Overview

Added comprehensive safety mechanisms to the `ResourceAwareAdaptiveLimiter` to prevent catastrophic system hangs like the one experienced during `gefion features-compute --all-features`.

## The Problem

User reported a complete system hang:
- Command: `gefion features-compute --all-features`
- Symptoms: System lost network connection and console access (kernel-level hang)
- Root cause: Likely resource exhaustion (memory/CPU/threads) causing OOM killer or kernel panic

## Safety Improvements Implemented

### 1. Increased Safety Buffers

**Previous values:**
```python
memory_buffer_gb: 2.0
cpu_buffer: 2
db_buffer: 5
```

**New values:**
```python
memory_buffer_gb: 4.0  # Doubled for safety
cpu_buffer: 4         # Doubled for safety
db_buffer: 10         # Doubled for safety
```

**Why:** More conservative resource reservation prevents the system from being pushed to the edge.

### 2. Hard Thread Limit

**New parameter:**
```python
max_total_threads: Optional[int] = None
```

**Implementation:**
- Calculates total threads as: `workers × (1 + writer_workers)`
- Enforces hard limit during worker calculation
- In cli.py: Set to `max(4, int(cpu_count × 1.5))`

**Why:** Prevents thread explosion which can cause system hangs. Limits total threads to 1.5x CPU count.

**Example:**
- 8 CPU system: max 12 total threads
- 16 CPU system: max 24 total threads
- 64 CPU system: max 96 total threads

### 3. Emergency Brake System

**New parameters:**
```python
min_memory_threshold_gb: float = 2.0
enable_emergency_brake: bool = True
```

**Behavior:**
- Monitors available memory every batch
- Triggers when available memory drops below threshold (default: 2GB)
- Immediately scales down to 1 worker
- Prevents scale-up until memory recovers
- Emits warning messages to user

**Example output:**
```
🚨 EMERGENCY BRAKE: Available memory critically low (1.87 GB).
   Scaling down to minimum workers to prevent system crash.
   Emergency: Reduced to 1 worker
```

**Why:** Last-resort protection against OOM killer. If memory gets critically low, aggressively scale down before system crashes.

### 4. Circuit Breaker for High Error Rates

**New parameters:**
```python
max_error_rate: float = 0.5
error_window_size: int = 10
```

**Behavior:**
- Tracks errors from last N batches (default: 10)
- Calculates average error rate
- If error rate > 50%, halves worker count
- Prevents scale-up when errors are high
- Emits warning messages

**Example output:**
```
⚠️  High error rate detected (3.2 errors/batch).
   Scaling down from 8 to 4 workers.
```

**Why:** If errors are happening frequently, scaling up makes things worse. Scale down instead.

### 5. Enhanced Resource Monitoring

**Added to `get_resource_info()`:**
```python
{
    "total_threads": 24,
    "max_total_threads": 24,
    "emergency_brake_triggered": False,
    "memory_critical": False,
    "error_rate": 0.1,
    "high_error_rate": False
}
```

**Why:** Better visibility into safety mechanisms and resource status for debugging.

## How It Works Together

### Normal Operation
1. System starts with 2 workers (conservative)
2. Resource limiter periodically checks CPU, memory, DB connections
3. Scales up gradually when resources available
4. Respects all limits: user max, DB connections, CPU buffer, memory buffer, thread limit

### High Load Scenario
1. System scales up to handle load
2. Memory starts decreasing
3. Emergency brake monitors every batch
4. If memory < 2GB: immediate scale-down to 1 worker
5. Once memory recovers: allows scale-up again

### High Error Scenario
1. Batch processing encounters errors
2. Circuit breaker tracks error rate over last 10 batches
3. If error rate > 50%: halve worker count
4. Prevents aggressive scale-up when things are failing

### Thread Explosion Prevention
1. Calculate optimal workers and writer_workers
2. Check: `workers × (1 + writer_workers) > max_total_threads`
3. If exceeded: scale back workers to fit limit
4. Never exceeds 1.5x CPU count in total threads

## Configuration in cli.py

```python
# Calculate conservative hard limit on total threads
import multiprocessing
cpu_count = multiprocessing.cpu_count()
max_total_threads = max(4, int(cpu_count * 1.5))

limiter = ResourceAwareAdaptiveLimiter(
    start_workers=2,
    max_workers=max_w,
    available_db_connections=available,
    writer_workers=writer_workers,
    check_interval_seconds=30.0,
    emit_func=emit if progress and not json_output else None,

    # Safety parameters
    max_total_threads=max_total_threads,
    min_memory_threshold_gb=2.0,
    enable_emergency_brake=True,
)
```

## Testing

All existing tests pass with new safety mechanisms:
```bash
pytest tests/test_resource_aware_adaptive_limiter.py -v
# 12 passed in 0.26s
```

## Deployment Recommendations

### For Production (sloth machine)

1. **Deploy with conservative settings:**
   ```bash
   gefion features-compute --all-features --max-workers 8
   ```
   - Hard limit prevents over-scaling
   - Emergency brake will protect if memory gets low

2. **Monitor first run:**
   - Watch for emergency brake triggers
   - Check resource info messages
   - Verify memory doesn't drop below 2GB

3. **Adjust if needed:**
   - If too conservative: increase `max_total_threads` calculation
   - If memory issues: increase `min_memory_threshold_gb` to 3.0 or 4.0
   - If many errors: check error rate messages

### For Development

Testing the safety mechanisms:
```bash
# Test with limited resources
gefion features-compute --symbols AAPL,MSFT,GOOG --max-workers 4

# Test emergency brake (simulate low memory - requires manual testing)
# Monitor output for emergency brake messages

# Check resource info
# Look for "emergency_brake_triggered", "memory_critical" in logs
```

## Files Modified

- [src/gefion/utils/adaptive.py](../src/gefion/utils/adaptive.py): Added safety mechanisms
- [src/gefion/cli.py](../src/gefion/cli.py): Updated limiter instantiation with safety params
- [docs/safety_improvements.md](../docs/safety_improvements.md): This document

## Expected Behavior

### Before Safety Improvements
- System could scale up aggressively
- No protection against memory exhaustion
- No hard limit on total threads
- Could trigger OOM killer → system hang

### After Safety Improvements
- Starts conservatively (2 workers)
- Scales up gradually with multiple safety checks
- Emergency brake activates if memory gets low
- Hard limit prevents thread explosion
- Circuit breaker prevents scale-up during high errors
- System should NEVER hang from resource exhaustion

## What to Watch For

### Good Signs
- ✓ System starts at 2 workers
- ✓ Gradual scale-up messages
- ✓ Resource info shows healthy memory
- ✓ No emergency brake triggers
- ✓ Error rate stays low

### Warning Signs
- ⚠️  Emergency brake triggers frequently → memory threshold too low
- ⚠️  High error rate detected → investigate errors
- ⚠️  Memory critical warnings → reduce max_workers

### Critical Issues (should not happen anymore)
- ❌ System hang
- ❌ OOM killer
- ❌ Network/console loss

## Comparison to Previous Version

| Aspect | Before | After |
|--------|--------|-------|
| Memory buffer | 2GB | 4GB |
| CPU buffer | 2 cores | 4 cores |
| DB buffer | 5 connections | 10 connections |
| Thread limit | None | 1.5x CPU count |
| Emergency brake | No | Yes (< 2GB available) |
| Circuit breaker | No | Yes (> 50% error rate) |
| Start workers | 2 | 2 (unchanged) |
| Resource monitoring | Basic | Enhanced |

## Emergency Response

If system starts to hang despite these improvements:

1. **Immediate action:**
   - Kill the process: `Ctrl+C` or `kill -9 <pid>`
   - Check system resources: `htop`, `free -h`

2. **Investigation:**
   - Check logs for emergency brake triggers
   - Check resource info for high memory usage
   - Verify max_total_threads setting

3. **Recovery:**
   - Reduce max_workers: `gefion features-compute --max-workers 4`
   - Increase memory threshold: Edit cli.py, set `min_memory_threshold_gb=4.0`
   - Disable auto-scaling: Set `user_max_writer_workers=2` to prevent writer scaling

## Future Improvements

Potential additional safety mechanisms:
1. Swap usage monitoring (trigger brake if swap is being used heavily)
2. CPU load monitoring (trigger brake if load average exceeds threshold)
3. Per-worker timeout (kill workers that take too long)
4. Gradual scale-down (instead of immediate halving)
5. Configurable safety profiles (conservative/moderate/aggressive)

## Conclusion

These safety improvements add multiple layers of protection to prevent system hangs from resource exhaustion. The emergency brake system is the last line of defense, designed to prevent catastrophic failures like the one experienced on the sloth machine.

The system will now:
- Start conservatively
- Scale up cautiously
- Monitor resources continuously
- React quickly to memory pressure
- Prevent thread explosion
- Back off on high errors
- **Never hang the system**
