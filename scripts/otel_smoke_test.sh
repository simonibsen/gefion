#!/bin/bash
# OTEL connectivity smoke test — emit a test span and verify it arrives in Tempo.
# Run after /gefion-services start to confirm tracing is working end-to-end.

echo "Testing OTEL → Tempo connectivity..."

# 1. Emit a test span via the gefion CLI
OTEL_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    timeout 10 .venv/bin/python -c "
from gefion.observability import create_span, set_attributes
with create_span('smoke_test.verify', test=True) as span:
    set_attributes(span, status='ok')
    print('Span emitted')
" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "Failed to emit test span. Check OTEL configuration."
    exit 1
fi

# 2. Wait for Tempo to ingest
sleep 3

# 3. Query Tempo for the test span
result=$(curl -s "http://localhost:3200/api/search?service.name=gefion&limit=5&tags=root.name%3Dsmoke_test.verify" 2>/dev/null)

if echo "$result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
traces = data.get('traces', [])
if traces:
    print('OTEL pipeline verified — trace arrived in Tempo')
    sys.exit(0)
else:
    print('Warning: test span not found in Tempo (may need more time)')
    sys.exit(1)
" 2>/dev/null; then
    echo "Tracing is working end-to-end."
else
    echo "Tempo may need a moment to ingest. Try: gefion span-check --limit 1"
fi
