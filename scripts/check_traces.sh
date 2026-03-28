#!/bin/bash
# Post-test trace check: query Tempo for recent spans and flag issues.
# Runs after pytest completes (PostToolUse hook on Bash with pytest).

# Skip if Tempo isn't running
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "tempo"; then
    exit 0
fi

# Skip if OTEL was disabled
if echo "$1" | grep -q "OTEL_ENABLED=false"; then
    exit 0
fi

# Query recent traces with configurable threshold
# Span-name-based thresholds (ms):
#   ui.* page context/stats: 500ms
#   charts.*: 2000ms (chart rendering can be slow)
#   db.*: 500ms
#   default: 1000ms

result=$(timeout 5 curl -s "http://localhost:3200/api/search?service.name=gefion&limit=20&minDuration=500ms" 2>/dev/null)

if [ -z "$result" ] || echo "$result" | grep -q "error"; then
    exit 0
fi

# Parse and apply span-specific thresholds
issues=$(echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    traces = data.get('traces', [])
    issues = []
    for t in traces:
        name = t.get('rootTraceName', '')
        dur = t.get('durationMs', 0)
        # Span-specific thresholds
        if name.startswith('ui.'):
            threshold = 500
        elif name.startswith('charts.'):
            threshold = 2000
        elif name.startswith('db.'):
            threshold = 500
        else:
            threshold = 1000
        if dur > threshold:
            issues.append(f'{name}: {dur}ms (threshold: {threshold}ms)')
    if issues:
        print('; '.join(issues[:5]))
except:
    pass
" 2>/dev/null)

if [ -n "$issues" ]; then
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": \"Trace check: slow spans detected — $issues. Run '/gefion-perf' to investigate.\"}}"
fi
