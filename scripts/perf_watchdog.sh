#!/bin/bash
# Silent perf watchdog — only outputs when slow traces are found.
# Designed for /loop: runs quietly in the background, alerts only on issues.

result=$(.venv/bin/python scripts/perf_report.py 2>/dev/null)

# Check if there are any SLOW traces
if echo "$result" | grep -q "^SLOW"; then
    slow_count=$(echo "$result" | grep -c "^\s.*\[.*ms\]")
    worst=$(echo "$result" | grep "^\s" | head -1 | xargs)
    echo "Perf alert: $slow_count slow trace(s). Worst: $worst. Run /gefion-perf for details."
fi
# If no slow traces, output nothing — loop stays silent.
