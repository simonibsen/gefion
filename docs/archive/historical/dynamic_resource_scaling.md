# Dynamic Resource Scaling

## Overview

The Gefion feature computation system now **automatically scales workers and writer threads** based on available system resources. This eliminates the need for manual tuning and prevents system lockups from resource exhaustion.

## How It Works

The `ResourceAwareAdaptiveLimiter` monitors system resources every 30 seconds during feature computation and dynamically adjusts:

1. **max_workers**: Number of stocks processed in parallel
2. **writer_workers**: Number of writer threads per stock

### Resource Constraints Considered

The limiter considers three resource types:

1. **CPU Cores**: Limits total threads to avoid oversubscription
   - Reserves 2 cores for system/OS
   - Allows 2x oversubscription (threads = 2 × cores)

2. **Available Memory**: Calculates how many workers fit in available RAM
   - Each worker needs ~125MB + (writer_workers × 2MB)
   - Reserves 2GB buffer for OS and background processes
   - Monitors actual available memory via `psutil`

3. **Database Connections**: Respects connection pool limits
   - Each worker needs 1 + writer_workers connections
   - Reserves 5 connections for other operations

### Optimization Strategy

The limiter tries different combinations of (max_workers, writer_workers) and selects the combination that:
- Maximizes throughput: `max_workers × writer_workers`
- Respects all resource constraints
- Stays within user-specified limits

## Usage

### Automatic Scaling (Default)

```bash
# Automatically scales based on available resources
gefion features-compute --all-features
```

The system will:
- Start conservatively (50% of calculated max)
- Check resources every 30 seconds
- Scale up when resources become available
- Scale down when resources are constrained
- Emit messages when scaling decisions are made

### Example Output

```
Available connections: 100, Max workers: 10
✓ Scaling up: max workers 5 → 8 (resources available)
✓ Scaling up: writer workers 2 → 4 (resources available)
⚠️  Scaling down: max workers 8 → 6 (resource constraints)
   Reducing active workers 8 → 6
```

### Manual Override

You can still manually specify limits if needed:

```bash
# Limit to specific worker counts
gefion features-compute --all-features --max-workers 5 --writer-workers 3
```

The system will respect your limits but still monitor resources and scale down if needed.

## Benefits

### 1. Prevents System Lockups

Before:
```bash
# This would consume 10 × 150MB + 80 threads = crash!
gefion features-compute --max-workers 10 --writer-workers 8
```

Now:
```bash
# System automatically detects it can't support 10 workers
# and scales down to safe levels
gefion features-compute --all-features
# ⚠️  Scaling down: max workers 10 → 4 (resource constraints)
```

### 2. Maximizes Performance

The system automatically uses all available resources without manual tuning:

- **Low-resource system** (4 cores, 8GB RAM):
  - Automatically uses 2 workers × 2 writers = 4 parallel operations

- **High-resource system** (16 cores, 64GB RAM):
  - Automatically uses 14 workers × 6 writers = 84 parallel operations

### 3. Adapts to Changing Conditions

If you start other processes during feature computation, the system detects reduced resources and scales down automatically.

## Implementation Details

### ResourceAwareAdaptiveLimiter

Located in: [src/gefion/utils/adaptive.py](../src/gefion/utils/adaptive.py)

Key methods:
- `_calculate_optimal_workers_and_writers()`: Calculates optimal configuration
- `_update_resource_limits()`: Checks resources and adjusts limits
- `record_batch()`: Called after each batch, triggers periodic resource checks
- `get_writer_workers()`: Returns current writer_workers value

### Integration Points

1. **CLI** ([src/gefion/cli.py](../src/gefion/cli.py)):
   - Creates `ResourceAwareAdaptiveLimiter` instead of `AdaptiveLimiter`
   - Passes available DB connections to limiter
   - Retrieves dynamic writer_workers via `limiter.get_writer_workers()`

2. **Connection Pool Sizing**:
   - Pool sized generously to accommodate dynamic scaling
   - Uses max possible writer_workers (8) for pool size calculation
   - Prevents connection exhaustion during scale-up

3. **Feature Dispatcher**:
   - Receives dynamically scaled writer_workers per stock
   - No changes needed in dispatcher itself

## Configuration

### Check Interval

Default: 30 seconds

The limiter checks resources every 30 seconds via the `record_batch()` method. This is a good balance between responsiveness and overhead.

### Memory Estimates

- **Worker memory**: 125MB per worker (conservative)
- **Writer thread memory**: 2MB per writer thread
- **Memory buffer**: 2GB reserved for OS

You can override these when creating the limiter (advanced use only).

### Safety Limits

Hard limits to prevent resource exhaustion:
- Max writer_workers: 8 (reasonable upper bound)
- Connection pool buffer: 5 connections
- CPU buffer: 2 cores reserved

## Testing

Comprehensive tests in: [tests/test_resource_aware_adaptive_limiter.py](../tests/test_resource_aware_adaptive_limiter.py)

Run tests:
```bash
pytest tests/test_resource_aware_adaptive_limiter.py -v
```

## Comparison with Static Configuration

### Before (Static)

```bash
# Manual tuning required
gefion features-compute --max-workers 5 --writer-workers 2

# Too conservative = slow
# Too aggressive = crash
# Wrong for different systems
```

### After (Dynamic)

```bash
# Works optimally on any system
gefion features-compute --all-features

# Automatically:
# - Small laptop: 2 workers × 2 writers
# - Workstation: 10 workers × 6 writers
# - Adapts to background load
```

## Monitoring

The limiter emits messages when scaling:

- ✓ **Scaling up**: Resources became available
- ⚠️ **Scaling down**: Resources constrained
- Resource info available via `limiter.get_resource_info()`

## Future Enhancements

Potential improvements:
1. Machine learning to predict optimal configuration
2. Per-stock resource profiling
3. Integration with system monitoring tools
4. Cloud-aware scaling (EC2, GCP instance types)
5. Cost-aware optimization (balance speed vs. cost)

## See Also

- [Performance Optimizations Summary](optimizations_summary.md)
- [Parallelization Implementation Guide](parallelization_implementation_guide.md)
- [Feature Computation Architecture](architecture.md)
