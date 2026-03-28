#!/bin/bash
# Check that development services are running (postgres + tempo).
# Used as a Claude Code SessionStart hook — only fires during dev sessions.

missing=()

# Check PostgreSQL
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "gefion-postgres"; then
    missing+=("PostgreSQL")
fi

# Check Tempo
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "tempo"; then
    missing+=("Tempo")
fi

if [ ${#missing[@]} -gt 0 ]; then
    services=$(IFS=', '; echo "${missing[*]}")
    echo "{\"systemMessage\": \"Dev services not running: ${services}. Run /gefion-services start for observability (constitution Section IV).\"}"
fi
