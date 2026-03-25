# Tasks: UI Reliability

**Input**: Design documents from `/specs/001-ui-reliability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md

**Tests**: Included — TDD is required by constitution (Principle II).

**Organization**: Tasks grouped by user story. US2 (CLI command execution) and US5 (mapping correctness) are already implemented and marked complete.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No new project setup needed — extending existing codebase.

- [ ] T001 Verify existing tests pass: run `OTEL_ENABLED=false .venv/bin/python -m pytest tests/test_ui_components.py`

---

## Phase 2: Foundational (Conversation History Module)

**Purpose**: The `history.py` module is needed by US1 (conversation history) and used by US3 (error display reads from existing errors module). Build it first.

**CRITICAL**: No user story work can begin until this phase is complete.

### Tests

- [ ] T002 [P] Write test_history_module_exists in tests/test_ui_components.py — assert `src/g2/ui/history.py` exists and has required functions
- [ ] T003 [P] Write test_history_append_exchange in tests/test_ui_components.py — verify `append_exchange()` writes an Exchange record to JSONL
- [ ] T004 [P] Write test_history_read_exchanges in tests/test_ui_components.py — verify `read_exchanges()` returns list of Exchange dicts from JSONL
- [ ] T005 [P] Write test_history_clear in tests/test_ui_components.py — verify `clear_history()` removes file and returns empty list
- [ ] T006 Write test_history_max_100_exchanges in tests/test_ui_components.py — verify appending beyond 100 truncates oldest
- [ ] T007 Run tests — verify T002-T006 FAIL (Red)

### Implementation

- [ ] T008 Create src/g2/ui/history.py with Exchange dataclass and constants (HISTORY_FILE, MAX_EXCHANGES=100)
- [ ] T009 Implement `append_exchange(prompt, mode, response, success, duration_sec)` in src/g2/ui/history.py
- [ ] T010 Implement `read_exchanges()` and `clear_history()` in src/g2/ui/history.py
- [ ] T011 Run tests — verify T002-T006 PASS (Green)

**Checkpoint**: History module complete and tested. User story implementation can begin.

---

## Phase 3: User Story 1 - AI Conversation with History (Priority: P1) MVP

**Goal**: Conversation thread with persistent history displayed on the AI Actions page.

**Independent Test**: Submit 3 prompts in sequence, refresh the browser, verify all 3 exchanges are still visible.

### Tests

- [ ] T012 [P] [US1] Write test_assistant_renders_conversation_history in tests/test_ui_components.py — assert assistant.py calls `read_exchanges` and renders history
- [ ] T013 [P] [US1] Write test_assistant_appends_exchange_on_completion in tests/test_ui_components.py — assert assistant.py calls `append_exchange` after command completes
- [ ] T014 [P] [US1] Write test_assistant_has_clear_history_button in tests/test_ui_components.py — assert "clear_history" or "Clear History" appears in assistant.py
- [ ] T015 [US1] Run tests — verify T012-T014 FAIL (Red)

### Implementation

- [ ] T016 [US1] Add conversation history rendering to `render_assistant()` in src/g2/ui/views/assistant.py — call `read_exchanges()` and display as scrollable thread above the input form
- [ ] T017 [US1] Integrate `append_exchange()` into command completion flow in src/g2/ui/views/assistant.py — capture prompt, mode, response, success, and duration after process finishes
- [ ] T018 [US1] Add "Clear History" button to src/g2/ui/views/assistant.py — call `clear_history()` and rerun
- [ ] T019 [US1] Run tests — verify T012-T014 PASS (Green)

**Checkpoint**: Conversation history works end-to-end. Users see past exchanges and can clear them.

---

## Phase 4: User Story 3 - Errors Visible in UI (Priority: P1)

**Goal**: Session errors are surfaced inline in the UI with a count indicator and expandable error list.

**Independent Test**: Trigger a command failure and verify the error is visible in the UI page.

### Tests

- [ ] T020 [P] [US3] Write test_assistant_shows_error_indicator in tests/test_ui_components.py — assert assistant.py reads from error module and displays error count
- [ ] T021 [P] [US3] Write test_assistant_has_expandable_error_list in tests/test_ui_components.py — assert assistant.py has an expander or section for listing session errors
- [ ] T022 [US3] Run tests — verify T020-T021 FAIL (Red)

### Implementation

- [ ] T023 [US3] Add error count indicator to `render_assistant()` in src/g2/ui/views/assistant.py — read `read_session_errors()` from `g2.ui.errors` and show count badge
- [ ] T024 [US3] Add expandable error list to src/g2/ui/views/assistant.py — `st.expander` with timestamped error details
- [ ] T025 [US3] Run tests — verify T020-T021 PASS (Green)

**Checkpoint**: Errors are visible in the UI. No external monitoring needed to see failures.

---

## Phase 5: User Story 4 - Navigation Rename and Reorder (Priority: P2)

**Goal**: "AI Actions" appears directly below "Dashboard" in the sidebar. Chat input is the first element on the page.

**Independent Test**: Open the UI and verify "AI Actions" is the second sidebar item and the chat input is at the top.

### Tests

- [ ] T026 [P] [US4] Write test_sidebar_ai_actions_position in tests/test_ui_components.py — assert the page list/config has "AI Actions" as the second entry
- [ ] T027 [P] [US4] Write test_assistant_renamed_to_ai_actions in tests/test_ui_components.py — assert the page title/subheader is "AI Actions" not "Ask AI / Run Command"
- [ ] T028 [P] [US4] Write test_assistant_input_before_proactive_actions in tests/test_ui_components.py — assert the form/input section appears before the proactive actions section in assistant.py
- [ ] T029 [US4] Run tests — verify T026-T028 FAIL (Red)

### Implementation

- [ ] T030 [US4] Rename view to "AI Actions" in sidebar navigation config (src/g2/ui/app.py or wherever sidebar is defined)
- [ ] T031 [US4] Reorder sidebar entries so "AI Actions" is directly after "Dashboard" in src/g2/ui/app.py
- [ ] T032 [US4] Reorder sections in `render_assistant()` in src/g2/ui/views/assistant.py — move chat input + history above proactive actions and system overview
- [ ] T033 [US4] Update subheader text from "Ask AI / Run Command" to "AI Actions" in src/g2/ui/views/assistant.py
- [ ] T034 [US4] Run tests — verify T026-T028 PASS (Green)

**Checkpoint**: Navigation is updated. AI Actions is prominent and the page layout is reordered.

---

## Phase 6: User Story 2 - CLI Command Execution (Priority: P1) DONE

**Goal**: Commands execute reliably from the UI with form submission, auto-refresh, and proper env handling.

**Status**: Already implemented on this branch. FR-004 through FR-007 are complete with tests.

- [x] T035 [US2] Form wraps input + submit button in src/g2/ui/views/assistant.py
- [x] T036 [US2] Auto-refresh output while running in src/g2/ui/views/assistant.py
- [x] T037 [US2] Run button not blocked by completed state in src/g2/ui/views/assistant.py
- [x] T038 [US2] CLAUDECODE env var stripped in src/g2/ui/views/assistant.py

---

## Phase 7: User Story 5 - Proactive Actions & Mapping Correctness (Priority: P2) DONE

**Goal**: All MCP tool and proactive action CLI mappings reference real g2 commands.

**Status**: Already implemented on this branch. FR-016 through FR-018 are complete with regression tests.

- [x] T039 [US5] Fix 8 broken MCP_TOOL_MAP entries in src/g2/ui/views/assistant.py
- [x] T040 [US5] Remove invalid query_database mapping in src/g2/ui/views/assistant.py
- [x] T041 [US5] Fix proactive action cli_cmd for health check in src/g2/ui/views/assistant.py
- [x] T042 [US5] Regression test validates all mappings against g2 --help in tests/test_ui_components.py

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and cleanup across all user stories.

- [ ] T043 [P] Write UI section in docs/USER_GUIDE.md — describe AI Actions page, conversation history, error indicators, `g2 ui` launch
- [ ] T044 [P] Update .specify/memory/progress.md with final status of all implemented features
- [ ] T045 [P] Update .specify/memory/backlog.md — move UI Reliability to Completed section
- [ ] T046 Run full test suite: `OTEL_ENABLED=false .venv/bin/python -m pytest tests/test_ui_components.py`
- [ ] T047 Manual smoke test: launch `g2 ui`, submit prompts, verify history persists across refresh, trigger error, verify error badge

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — verify baseline
- **Phase 2 (Foundational)**: Depends on Phase 1 — builds history module
- **Phase 3 (US1 - History)**: Depends on Phase 2 — uses history module
- **Phase 4 (US3 - Errors)**: Depends on Phase 2 — can run in parallel with Phase 3
- **Phase 5 (US4 - Navigation)**: No dependencies on Phase 3/4 — can run in parallel
- **Phase 6 (US2 - CLI)**: DONE
- **Phase 7 (US5 - Mappings)**: DONE
- **Phase 8 (Polish)**: Depends on Phases 3, 4, 5 completion

### User Story Dependencies

- **US1 (History)**: Depends on foundational history module (Phase 2)
- **US2 (CLI Execution)**: DONE — no remaining work
- **US3 (Error Surfacing)**: Depends on existing `g2.ui.errors` module — no new foundational work
- **US4 (Navigation)**: Independent — only touches layout and config
- **US5 (Mapping Correctness)**: DONE — no remaining work

### Parallel Opportunities

After Phase 2 completes:
- US1 (Phase 3), US3 (Phase 4), and US4 (Phase 5) can all proceed in parallel
- Within Phase 2: T002-T006 (tests) can all run in parallel
- Within Phase 5: T026-T028 (tests) can all run in parallel
- Within Phase 8: T043-T045 (docs) can all run in parallel

---

## Parallel Example: After Phase 2

```bash
# These three phases can run in parallel after the history module is built:

# Phase 3: Conversation History (US1)
Task: "Add conversation history rendering to render_assistant() in src/g2/ui/views/assistant.py"

# Phase 4: Error Surfacing (US3)
Task: "Add error count indicator to render_assistant() in src/g2/ui/views/assistant.py"

# Phase 5: Navigation (US4)
Task: "Rename view to AI Actions and reorder sidebar in src/g2/ui/app.py"
```

Note: US1 and US3 both modify `assistant.py`, so sequential execution is safer to avoid merge conflicts. US4 touches `app.py` (sidebar config) and can truly run in parallel.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Verify baseline (T001)
2. Complete Phase 2: Build history module (T002-T011)
3. Complete Phase 3: Conversation history in UI (T012-T019)
4. **STOP and VALIDATE**: Submit prompts, refresh browser, verify persistence
5. This alone delivers significant value — users can see their interaction history

### Incremental Delivery

1. Phase 2 → History module ready
2. Phase 3 (US1) → Conversation history works → Manual test
3. Phase 4 (US3) → Errors visible in UI → Manual test
4. Phase 5 (US4) → Navigation updated → Manual test
5. Phase 8 → Docs and cleanup → Full test suite

### Recommended Order (Single Developer)

Phase 2 → Phase 3 (US1) → Phase 4 (US3) → Phase 5 (US4) → Phase 8

---

## Summary

| Metric | Count |
|--------|-------|
| Total tasks | 47 |
| New tasks (to do) | 35 |
| Already done | 8 (US2 + US5) |
| Verification tasks | 4 (test runs) |
| Tasks per story | US1: 8, US2: 4 (done), US3: 6, US4: 9, US5: 4 (done) |
| Parallel opportunities | 3 phases after foundational, plus parallel tests within phases |
| Suggested MVP | Phase 2 + Phase 3 (US1 — conversation history) |
