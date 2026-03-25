# Data Model: UI Reliability

**Date**: 2026-03-18

## Entities

### Exchange

A single user prompt and its system response.

| Field | Type | Description |
|-------|------|-------------|
| timestamp | ISO 8601 datetime | When the exchange was created |
| prompt | string | The user's input (natural language or CLI command) |
| mode | string | "ai", "cli", or "mcp" — how the input was routed |
| response | string | The system's output (AI response or CLI stdout) |
| success | boolean | Whether the command/prompt completed successfully |
| duration_sec | float | How long the execution took |

**Storage**: One JSON object per line in `~/.g2/ai_history.jsonl`

**Constraints**:
- Maximum 100 exchanges retained (oldest truncated on overflow)
- File is append-only during a session; truncation happens on append when count > 100

### Session Error (existing)

Already defined in `g2.ui.errors`. No changes to the data model.

| Field | Type | Description |
|-------|------|-------------|
| timestamp | ISO 8601 datetime | When the error occurred |
| source | string | Component that produced the error |
| message | string | Error description |
| context | object | Additional metadata (key, returncode, etc.) |

**Storage**: `~/.g2/ui_errors.jsonl` (unchanged)

## Relationships

- An Exchange MAY have an associated Session Error (if the command failed and was logged)
- Session Errors exist independently of Exchanges (background process failures, condition check failures)

## State Transitions

### Exchange Lifecycle
```
[user submits] → RUNNING → COMPLETED (success=true)
                        → FAILED (success=false, error logged)
```

### Conversation History
```
[empty] → [exchanges appended] → [clear requested] → [empty]
                               → [overflow at 100] → [oldest removed]
```
