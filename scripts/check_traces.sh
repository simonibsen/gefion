#!/bin/bash
# Post-test trace check: query Tempo for recent spans and flag issues.
# Runs after pytest completes (PostToolUse hook on Bash with pytest).
# Only checks if OTEL was enabled during the test run.

# Skip if Tempo isn't running
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "tempo"; then
    exit 0
fi

# Skip if OTEL was disabled (most test runs)
if echo "$1" | grep -q "OTEL_ENABLED=false"; then
    exit 0
fi

# Query recent traces from Tempo
result=$(timeout 5 curl -s "http://localhost:3200/api/search?service.name=gefion&limit=5&start=$(date -v-5M +%s 2>/dev/null || date -d '5 minutes ago' +%s 2>/dev/null)&end=$(date +%s)" 2>/dev/null)

if [ -z "$result" ] || echo "$result" | grep -q "error"; then
    exit 0  # Tempo not responding, skip silently
fi

# Check for slow spans (>1s)
slow_count=$(echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    traces = data.get('traces', [])
    slow = sum(1 for t in traces if t.get('durationMs', 0) > 1000)
    print(slow)
except: print(0)
" 2>/dev/null)

if [ "$slow_count" -gt 0 ] 2>/dev/null; then
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": \"Trace check: $slow_count slow traces (>1s) detected in recent Tempo data. Run 'gefion span-check' to investigate.\"}}"
fi
