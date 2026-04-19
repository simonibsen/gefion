#!/bin/bash
# Post-test trace check: query Tempo for recent spans and flag issues.
# Runs after pytest completes (PostToolUse hook on Bash with pytest).

# Only run after pytest or streamlit commands (skip git, ls, etc.)
if ! echo "$1" | grep -qE "pytest|streamlit|gefion"; then
    exit 0
fi

# Skip if Tempo isn't running
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "tempo"; then
    exit 0
fi

# Skip if OTEL was disabled
if echo "$1" | grep -q "OTEL_ENABLED=false"; then
    exit 0
fi

# Query recent traces (last 5 minutes, >500ms)
result=$(timeout 5 curl -s "http://localhost:3200/api/search?service.name=gefion&limit=20&minDuration=500ms&start=$(date -v-5M +%s 2>/dev/null || date -d '5 minutes ago' +%s 2>/dev/null)" 2>/dev/null)

if [ -z "$result" ] || echo "$result" | grep -q "error"; then
    exit 0
fi

# Parse and apply span-specific thresholds, exclude expected slow operations
issues=$(echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    traces = data.get('traces', [])
    issues = []
    for t in traces:
        name = t.get('rootTraceName', '')
        dur = t.get('durationMs', 0)

        # Skip unnamed traces
        if not name or name == 'SELECT':
            continue

        # Span-specific thresholds — every operation has one
        if name.startswith('ui.'):
            threshold = 500
        elif name.startswith('charts.'):
            threshold = 2000
        elif name.startswith('db.'):
            threshold = 500
        elif name.startswith('cli.data-update') or name.startswith('data_update'):
            threshold = 30000   # 30s — data updates should finish in under 30s per batch
        elif name.startswith('cli.experiment') or name.startswith('experiments.') or 'cycle_runner' in name:
            threshold = 60000   # 60s — individual experiment operations
        elif name.startswith('cli.ml') or name.startswith('cli.feat'):
            threshold = 30000   # 30s — ML training and feature computation
        elif name.startswith('cli.'):
            threshold = 5000
        else:
            threshold = 1000

        if dur > threshold:
            issues.append(f'{name}: {dur}ms (threshold: {threshold}ms)')

    if issues:
        print('; '.join(issues[:3]))
except:
    pass
" 2>/dev/null)

if [ -n "$issues" ]; then
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": \"Trace check: slow spans detected — $issues. Run '/gefion-perf' to investigate.\"}}"
fi
