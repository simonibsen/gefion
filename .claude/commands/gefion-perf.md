---
description: Inspect performance traces from Tempo and identify slow operations
---

## Arguments

$ARGUMENTS

## Instructions

Performance inspection skill — uses `scripts/perf_report.py` to query Tempo and identify bottlenecks.

### Workflow

1. **Check Tempo is running**:
   ```bash
   docker ps --format '{{.Names}}' | grep tempo
   ```
   If Tempo isn't running, tell the user to run `/gefion-services start`.

2. **Run the perf report script** with the appropriate flags based on the user's arguments:

   | User argument | Script command |
   |---------------|----------------|
   | *(none)* | `.venv/bin/python scripts/perf_report.py --detail` |
   | `1000` | `.venv/bin/python scripts/perf_report.py --detail 1000` |
   | `dashboard` | `.venv/bin/python scripts/perf_report.py --detail dashboard` |
   | `suggest` | `.venv/bin/python scripts/perf_report.py --suggest` |
   | `baseline` | `.venv/bin/python scripts/perf_report.py --baseline` |
   | `compare` | `.venv/bin/python scripts/perf_report.py --compare` |
   | `fix` | `.venv/bin/python scripts/perf_report.py --detail` (then investigate and fix the bottleneck) |

3. **Present the output** to the user. The script handles all formatting — just pass it through.

4. **For `fix` mode**: After showing the report, investigate the bottleneck span:
   - If it's a slow DB query: check for `MAX(date)` on hypertables, `COUNT(*)` scans, missing indexes
   - If it's a subprocess: check timeout, consider async
   - If it's computation: look for caching opportunities
   - Implement the fix, re-run the script to verify improvement

### Usage Examples

| Command | Meaning |
|---------|---------|
| `/gefion-perf` | Show slow traces with drill-down into the slowest |
| `/gefion-perf 1000` | Show traces slower than 1 second |
| `/gefion-perf dashboard` | Show traces matching "dashboard" |
| `/gefion-perf fix` | Find slowest trace and suggest/implement a fix |
| `/gefion-perf suggest` | Proactive suggestions: missing coverage, N+1 queries, cache opportunities |
| `/gefion-perf baseline` | Save current trace durations as a baseline |
| `/gefion-perf compare` | Compare current traces against saved baseline |

### Span-Specific Thresholds

The script applies these thresholds automatically:

| Span prefix | Threshold | Rationale |
|-------------|-----------|-----------|
| `ui.*` | 500ms | Page loads should feel instant |
| `db.*` | 500ms | DB operations should be fast |
| `charts.*` | 2000ms | Chart rendering has more overhead |
| `cli.*` | 5000ms | CLI commands can include I/O |
| default | 1000ms | General operations |
