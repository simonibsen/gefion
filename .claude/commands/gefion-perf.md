---
description: Inspect performance traces from Tempo and identify slow operations
---

## Arguments

$ARGUMENTS

## Instructions

Performance inspection skill — queries Grafana Tempo for recent traces and identifies bottlenecks.

### Workflow

1. **Check services are running**:
   ```bash
   docker ps --format '{{.Names}}' | grep tempo
   ```
   If Tempo isn't running, tell the user to run `/gefion-services start`.

2. **Query Tempo for slow traces** (>500ms by default, or threshold from args):
   ```bash
   curl -s "http://localhost:3200/api/search?service.name=gefion&limit=20&minDuration=500ms"
   ```

3. **Parse and rank by duration**:
   - Extract: rootTraceName, durationMs
   - Sort by duration descending
   - Show top 10

4. **For the slowest trace**, fetch detail:
   ```bash
   curl -s "http://localhost:3200/api/traces/{traceID}"
   ```
   Parse all spans, show the span tree with durations.

5. **Identify the bottleneck**:
   - Which span took the most time?
   - Is it a DB query? (Look for auto-instrumented `SELECT` spans)
   - Is it a subprocess? (Look for `subprocess` in span name)
   - Is it computation? (No child spans, just wall time)

6. **Suggest fix**:
   - Slow DB query → Check for missing indexes, full table scans, COUNT(DISTINCT)
   - Slow subprocess → Check timeout, consider async
   - Slow computation → Profile or cache

### Usage Examples

| Command | Meaning |
|---------|---------|
| `/gefion-perf` | Show all slow traces (>500ms) |
| `/gefion-perf 1000` | Show traces slower than 1 second |
| `/gefion-perf dashboard` | Show traces matching "dashboard" |
| `/gefion-perf fix` | Find slowest trace and suggest/implement a fix |

### Output Format

```
Performance Report
==================
Top slow traces (last 5 minutes):

  22,012ms  ui.status.get_system_stats
  17,327ms  ui.dashboard.get_page_context
  16,613ms  ui.dashboard.get_market_movers
     656ms  ui.status.get_latest_data_date

Bottleneck: ui.status.get_system_stats (22s)
  └─ SELECT COUNT(*) FROM computed_features (21.8s)
     ^^ Full table scan on hypertable — use pg_stat approximation

Suggested fix: Replace COUNT(*) with pg_stat_user_tables.n_live_tup
```

### For Other Repos

This pattern works for any project with OTEL + Tempo:
1. Instrument significant operations with `create_span()`
2. Run with `OTEL_ENABLED=true`
3. Exercise the code paths
4. Query Tempo API for `minDuration` > threshold
5. Drill into slow traces to find the bottleneck span
6. Fix and re-verify
