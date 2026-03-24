# Implementation Plan: UI Reliability

**Branch**: `001-ui-reliability` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-ui-reliability/spec.md`

## Summary

Harden the Streamlit UI assistant view: rename to "AI Actions" and promote in sidebar nav, add persistent conversation history, surface errors inline in the UI, and ensure all CLI command mappings are correct. Several fixes are already implemented (form submission, auto-refresh, CLAUDECODE stripping, mapping corrections).

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: Streamlit (UI framework), subprocess (process execution)
**Storage**: JSONL files in `~/.g2/` (conversation history, error log); PostgreSQL (system state queries)
**Testing**: pytest with OTEL_ENABLED=false; static file-content tests for UI structure
**Target Platform**: macOS / Linux (local development)
**Project Type**: CLI tool with Streamlit web UI
**Performance Goals**: Output refresh within 2 seconds of subprocess output
**Constraints**: No database schema changes; Streamlit session_state is ephemeral (must persist to disk)
**Scale/Scope**: Single-user local UI; conversation history bounded to last 100 exchanges

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. DB-First | PASS | No schema changes. Feature definitions/functions untouched. |
| II. TDD | PASS | Tests written before implementation (already demonstrated on this branch). |
| III. CLI-First | PASS | UI wraps existing CLI commands. No new functionality bypasses CLI. |
| IV. Observability | PASS | Error logging already uses structured JSONL. UI views don't need tracing (not server-side operations). |
| V. Consistent Presentation | PASS | UI is Streamlit, not CLI output. CLI presentation module not affected. |
| VI. Simplicity | PASS | JSONL for history (same pattern as errors), no new abstractions. |
| Documentation | PASS | FR-019 requires doc updates. |
| Schema Governance | PASS | No schema changes. |

No violations. No complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/001-ui-reliability/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0: resolved questions
├── data-model.md        # Phase 1: conversation & error data models
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
src/g2/ui/
├── views/
│   └── assistant.py     # Main file: rename, reorder, conversation history, error surfacing
├── components/
│   └── status.py        # Existing: system stats (unchanged)
├── errors.py            # Existing: error logging (extend for in-UI display)
└── history.py           # NEW: conversation history persistence (read/write/clear JSONL)

tests/
└── test_ui_components.py  # Extend with new tests for history, nav, errors
```

**Structure Decision**: Minimal new files. `history.py` is the only new module — keeps conversation persistence isolated from the view logic. `errors.py` already exists and gets extended. Most changes are in `assistant.py`.
