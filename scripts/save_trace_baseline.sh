#!/bin/bash
# Save current trace durations as a performance baseline.
# Usage: bash scripts/save_trace_baseline.sh [label]
#
# Saves to ~/.gefion/trace_baselines/{label}.json
# Used by /gefion-perf to detect regressions.

LABEL="${1:-$(date +%Y%m%d_%H%M%S)}"
BASELINE_DIR="$HOME/.gefion/trace_baselines"
mkdir -p "$BASELINE_DIR"

# Query Tempo for recent traces
result=$(curl -s "http://localhost:3200/api/search?service.name=gefion&limit=50" 2>/dev/null)

if [ -z "$result" ]; then
    echo "Error: Could not query Tempo. Is it running?"
    exit 1
fi

# Extract span name → duration map
python3 -c "
import sys, json
from datetime import datetime

data = json.loads('''$result''')
traces = data.get('traces', [])

baseline = {
    'label': '$LABEL',
    'timestamp': datetime.now().isoformat(),
    'spans': {}
}

for t in traces:
    name = t.get('rootTraceName', '')
    dur = t.get('durationMs', 0)
    if name:
        # Keep the fastest seen duration per span name
        if name not in baseline['spans'] or dur < baseline['spans'][name]:
            baseline['spans'][name] = dur

outfile = '$BASELINE_DIR/$LABEL.json'
with open(outfile, 'w') as f:
    json.dump(baseline, f, indent=2)

print(f'Baseline saved: {outfile}')
print(f'Spans: {len(baseline[\"spans\"])}')
for name, dur in sorted(baseline['spans'].items(), key=lambda x: -x[1]):
    print(f'  {dur:>8}ms  {name}')
"
