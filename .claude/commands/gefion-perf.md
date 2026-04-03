---
description: Inspect performance traces from Tempo and identify slow operations
---

## Arguments

$ARGUMENTS

## Instructions

Performance inspection skill — uses the Tempo MCP server to query traces directly via TraceQL.

### Workflow

1. **Query Tempo for recent traces** using the `mcp__tempo__traceql-search` tool:

   | User argument | TraceQL query |
   |---------------|---------------|
   | *(none)* | `{duration > 100ms}` — find all slow spans |
   | `500` or `1000` | `{duration > 500ms}` or `{duration > 1000ms}` — custom threshold |
   | `dashboard` | `{span.name =~ "dashboard"}` — filter by name |
   | `ui` | `{span.name =~ "ui."}` — all UI spans |
   | `db` | `{span.name =~ "db."}` — all DB spans |
   | `experiments` | `{span.name =~ "experiment"}` — experiment spans |
   | `fix` | Find slowest, investigate, and fix |

2. **Parse the results** and present a summary table:
   - Sort by duration (slowest first)
   - Show: span name, duration, span count
   - Flag any spans exceeding thresholds (see below)

3. **For detailed investigation**, use `mcp__tempo__get-trace` with the trace ID to see the full span tree.

4. **For `fix` mode**: After identifying the slowest span:
   - Use `mcp__tempo__get-trace` to get the full trace with child spans
   - Identify the bottleneck (slow DB query, N+1 pattern, missing cache)
   - If it's a slow DB query: check for `MAX(date)` on hypertables, `COUNT(*)` scans, missing indexes
   - If it's a subprocess: check timeout, consider async
   - If it's computation: look for caching opportunities
   - Implement the fix, re-query Tempo to verify improvement

5. **For attribute exploration**, use:
   - `mcp__tempo__get-attribute-names` — discover what attributes are tracked
   - `mcp__tempo__get-attribute-values` — see values for a specific attribute

6. **For metrics**, use:
   - `mcp__tempo__traceql-metrics-instant` — point-in-time metrics (e.g., rate of slow spans)
   - `mcp__tempo__traceql-metrics-range` — metrics over time range

### Useful TraceQL Queries

```
# All slow spans (>200ms)
{duration > 200ms}

# Slow UI operations
{span.name =~ "ui." && duration > 500ms}

# Slow DB operations
{span.name =~ "db." && duration > 200ms}

# Experiment-related traces
{span.name =~ "experiment"}

# Specific operation
{span.name = "ui.status.get_system_stats"}

# Traces with errors
{status = error}

# Traces with high span count (potential N+1)
{} | count() > 50
```

### Span-Specific Thresholds

| Span prefix | Threshold | Rationale |
|-------------|-----------|-----------|
| `ui.*` | 500ms | Page loads should feel instant |
| `db.*` | 500ms | DB operations should be fast |
| `charts.*` | 2000ms | Chart rendering has more overhead |
| `cli.*` | 5000ms | CLI commands can include I/O |
| `experiments.*` | 10000ms | Experiments are expected to be slow |
| default | 1000ms | General operations |

### Trace Analysis Helper

For large traces (thousands of spans), save the trace and use the analyzer:

```bash
# After getting a trace via mcp__tempo__get-trace, the output is saved to a file
# Analyze it:
python scripts/analyze_trace.py <trace_file.json>
python scripts/analyze_trace.py <trace_file.json> --errors
python scripts/analyze_trace.py <trace_file.json> --slow 500

# Output shows: slowest spans, N+1 patterns, error details
```

Use this when `mcp__tempo__get-trace` returns a large trace that's hard to parse inline.

### Development Workflow Integration

Use `/gefion-perf` during development to:
- **After code changes**: check if affected spans improved or regressed
- **After running experiments**: see how long each experiment phase took
- **Before committing**: quick scan for new slow spans
- **When debugging**: drill into specific traces to find bottlenecks

### Usage Examples

| Command | Meaning |
|---------|---------|
| `/gefion-perf` | Show slow traces (>100ms) from the last hour |
| `/gefion-perf 500` | Show traces slower than 500ms |
| `/gefion-perf ui` | Show all UI page load traces |
| `/gefion-perf db` | Show all database operation traces |
| `/gefion-perf experiments` | Show experiment execution traces |
| `/gefion-perf fix` | Find slowest trace, investigate, and fix the bottleneck |
