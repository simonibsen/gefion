# Performance Optimization Workflow with Tempo

This guide describes how to use Grafana Tempo traces as part of your performance optimization workflow.

## Overview

Tempo provides **real-time visibility** into your Gefion pipeline's performance without guesswork. Instead of profiling with print statements or blind optimization, you can see exactly where time is spent.

## The Workflow

### 1. Baseline Measurement

Before making any changes, capture a baseline trace:

```bash
# Enable tracing
export $(cat .env.example | xargs)

# Run the operation you want to optimize
gefion feat-compute --symbols AAPL,MSFT,GOOGL --function-names indicator --profile

# Check the trace
gefion span-check
```

**What to look for:**
- Total duration of the operation
- Which spans take the most time
- Where the bottlenecks are (database, computation, I/O)

**Example output:**
```
Total spans: 24
Span hierarchy:
  ┌─ cli.feat-compute (5234ms, 10 attrs)
    └─ compute_features (4891ms, 8 attrs)
      └─ process_function_group (3421ms, 4 attrs)
        └─ insert_computed_features (2789ms, 6 attrs)
```

From this, you can see that **insert_computed_features takes 2.8s out of 5.2s total** - that's your bottleneck!

### 2. Identify Bottlenecks

Use Grafana UI for deeper analysis:

1. **Open Grafana**: http://localhost:3000
2. **Navigate to Explore** → Tempo
3. **Search**: service.name = "gefion"
4. **Click on the slowest trace**

#### Common Bottleneck Patterns

**Database writes dominate:**
- `insert_computed_features` takes >50% of total time
- Many overlapping `db.get_connection` spans = pool contention
- Solution: Increase batch size, tune connection pool

**Feature computation slow:**
- `process_function_group` with specific `function_name` is slow
- Solution: Optimize that feature calculation, consider caching

**Sequential processing:**
- Gaps in the timeline where nothing is running
- Solution: Enable parallel processing (`--parallel`)

**Connection pool exhaustion:**
- Many `db.get_connection` spans with `pool_available=0`
- Solution: Increase pool size or reduce concurrent workers

### 3. Make Changes

Based on trace analysis, make targeted changes. For example:

**Problem**: Database writes are slow (2.8s out of 5.2s)

**Hypothesis**: Small batch size causing too many round trips

**Change**:
```bash
# Increase batch size from 2000 to 10000
gefion feat-compute --symbols AAPL --function-names indicator --batch-size 10000
```

### 4. Re-measure

Run the same operation again with tracing enabled:

```bash
gefion feat-compute --symbols AAPL,MSFT,GOOGL --function-names indicator --profile --batch-size 10000

gefion span-check
```

**Compare the traces:**
```
Before: insert_computed_features (2789ms)
After:  insert_computed_features (847ms)

Improvement: 70% faster! ✓
```

### 5. Validate in Grafana

Use Grafana to compare multiple traces side-by-side:

1. Search for traces with the same operation
2. Sort by duration (slowest first vs fastest first)
3. Click on one trace, note the duration
4. Go back, click on another trace
5. Compare the waterfall views

**Look for:**
- Did the bottleneck span get faster?
- Did we introduce new bottlenecks elsewhere?
- Is the overall trace time improved?

### 6. Iterate

Repeat the process:
- Find next bottleneck
- Make hypothesis
- Make change
- Re-measure
- Compare

## Real-World Example

### Scenario: Optimizing Feature Computation for 100 Stocks

**Step 1: Baseline**
```bash
export $(cat .env.example | xargs)
time Gefion feat-compute --symbols $(cat symbols.txt) --all-features
# Real: 45m 23s
```

**Step 2: Check Tempo**

In Grafana, I see:
- `cli.feat-compute` total: 2723s (45 minutes)
- Breakdown:
  - `compute_features` calls: 2689s (98.7%)
  - `insert_computed_features`: 1890s (70% of compute time!)

**Step 3: Diagnose Database Bottleneck**

API check shows details:
```bash
gefion span-check
```

Output shows:
```
Application spans: 101
Database spans: 8,423
  └─ Many small INSERT operations
```

**Hypothesis**: Too many small database writes

**Step 4: Increase Batch Size**
```bash
time Gefion feat-compute --symbols $(cat symbols.txt) --all-features --batch-size 10000
# Real: 18m 12s  (60% faster!)
```

**Step 5: Check Tempo Again**

New breakdown:
- `insert_computed_features`: 521s (instead of 1890s)
- **73% reduction in database write time**

**Step 6: Find Next Bottleneck**

Now computation is the bottleneck:
- `process_function_group` for `indicator`: 1245s

**Step 7: Enable Parallelization**
```bash
time Gefion feat-compute --symbols $(cat symbols.txt) --all-features --batch-size 10000 --parallel
# Real: 6m 34s  (86% faster than original!)
```

## Advanced Techniques

### Comparing Specific Stocks

Find why AAPL is slower than MSFT:

```bash
# In Grafana Explore:
# Query 1: {service.name="gefion"} | {symbol="AAPL"}
# Query 2: {service.name="gefion"} | {symbol="MSFT"}
```

Compare the trace waterfalls to see what's different.

### Finding Slow Features

Use TraceQL to find slow feature computations:

```
{service.name="gefion" && name="process_function_group"} | duration > 1s
```

This shows all feature processing that took >1 second.

### Monitoring Over Time

Enable sampling for continuous monitoring:

```bash
# .env.monitoring
OTEL_ENABLED=true
OTEL_EXPORTER=otlp
OTEL_OTLP_ENDPOINT=http://localhost:4317
OTEL_SAMPLING_RATE=0.01  # 1% sampling = low overhead
```

Load it for production runs:
```bash
export $(cat .env.monitoring | xargs)
```

Then periodically check Tempo to see if performance is degrading.

### A/B Testing Optimizations

Test two approaches:

```bash
# Approach A: High batch size
export OTEL_SERVICE_NAME=gefion-approach-a
gefion feat-compute --symbols AAPL --batch-size 10000

# Approach B: Parallel processing
export OTEL_SERVICE_NAME=gefion-approach-b
gefion feat-compute --symbols AAPL --parallel --batch-size 2000
```

Then compare in Grafana by filtering on `service.name`.

## Quick Commands

### Before Each Optimization Session

```bash
# Start Tempo + Grafana
docker compose -f docker/tempo/docker-compose.tempo.yml up -d

# Enable full tracing
export $(cat .env.example | xargs)
```

### Run Your Operation

```bash
gefion feat-compute --symbols AAPL --function-names indicator --profile
```

### Check Results

```bash
# Via CLI
gefion span-check

# Via UI
open http://localhost:3000/explore
```

### Compare Before/After

In Grafana:
1. Search for recent traces
2. Click on "before" trace, note duration: 5234ms
3. Go back, click on "after" trace, note duration: 2891ms
4. Calculate improvement: (5234-2891)/5234 = 44.7% faster ✓

## Integration with --profile Flag

The `--profile` flag adds timing breakdowns as span attributes:

```bash
gefion feat-compute --symbols AAPL --all-features --profile
```

This adds attributes like:
- `timing.fetch`: Time spent fetching data
- `timing.compute`: Time spent computing features
- `timing.write`: Time spent writing to database

You can filter by these in Grafana:
```
{service.name="gefion"} | timing.write > 1000
```

## Best Practices

1. **Always measure before optimizing** - Don't guess where the bottleneck is
2. **Change one thing at a time** - So you know what actually helped
3. **Keep traces for comparison** - Set longer retention in production
4. **Use sampling in production** - 1% sampling gives visibility with <0.1% overhead
5. **Tag your experiments** - Use different service names or custom attributes
6. **Look at p50, p95, p99** - Not just the average or a single trace
7. **Check for regressions** - Make sure you didn't make something else slower

## Troubleshooting

**No spans appearing?**
```bash
# Check Tempo is running
docker compose -f docker/tempo/docker-compose.tempo.yml ps

# Check environment
env | grep OTEL

# Test with simple script
.venv/bin/python tests/test_otel_smoke.py
```

**Traces too slow to appear?**
```bash
# Tempo batches traces - wait 10-30 seconds then refresh
sleep 30
gefion span-check
```

**Too many traces cluttering view?**
```bash
# Stop tracing when not optimizing
unset OTEL_ENABLED

# Or use selective sampling
export OTEL_SAMPLING_RATE=0.1  # 10% of operations
```

## Next Steps

- Set up longer retention for production monitoring
- Create Grafana dashboards showing performance trends
- Add custom metrics (throughput, queue depth, etc.)
- Set up alerts on slow operations

## Resources

- [Tempo API Documentation](https://grafana.com/docs/tempo/latest/api_docs/)
- [TraceQL Query Language](https://grafana.com/docs/tempo/latest/traceql/)
- [OpenTelemetry Best Practices](https://opentelemetry.io/docs/concepts/signals/traces/)
- [Gefion Observability Guide](OBSERVABILITY.md)
