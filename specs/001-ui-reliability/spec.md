# Feature Specification: UI Reliability

**Feature Branch**: `001-ui-reliability`
**Created**: 2026-03-15
**Updated**: 2026-03-18
**Status**: Draft
**Input**: Systematic audit and hardening of the Streamlit UI — assistant view reliability, navigation improvements, conversation history, and in-UI error surfacing.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - AI Conversation with History (Priority: P1)

A user opens the "AI Actions" page, types a natural language question or CLI command, and submits it. The response appears below. Previous exchanges persist as a scrollable conversation thread, surviving page reruns and lasting across the full UI session. History is written to disk so it survives browser refreshes and server restarts.

**Why this priority**: The AI chat is the primary workflow of the assistant view. Without history, every interaction is isolated — users can't build on previous answers or see what they've already tried.

**Independent Test**: Submit 3 prompts in sequence, refresh the browser, and verify all 3 exchanges are still visible.

**Acceptance Scenarios**:

1. **Given** a user submits a prompt, **When** the response completes, **Then** both the prompt and response are appended to the visible conversation thread.
2. **Given** a conversation has multiple exchanges, **When** the user scrolls, **Then** they can see the full history oldest-to-newest.
3. **Given** the user refreshes the browser or the server restarts, **When** the page loads, **Then** the previous conversation history is restored from disk.
4. **Given** the user wants a fresh start, **When** they click a "Clear History" button, **Then** the conversation is wiped from both the display and disk.

---

### User Story 2 - Run a CLI Command from the UI (Priority: P1)

A user types a g2 CLI command (e.g., `g2 health`) into the input and clicks Run. The command executes, output streams in real time, and the result is displayed clearly — success or failure.

**Why this priority**: This is the core interaction loop. If commands don't execute reliably, the UI is unusable.

**Independent Test**: Type any valid g2 CLI command, click Run, and verify output appears progressively until completion with a clear success/failure indicator.

**Acceptance Scenarios**:

1. **Given** a user has typed a valid CLI command, **When** they click the Run button, **Then** the command executes immediately without requiring Enter to be pressed first.
2. **Given** a command is running, **When** it produces output, **Then** the output streams to the display in near-real-time (within 2 seconds).
3. **Given** a command has completed, **When** the user types a new command, **Then** the Run button is available without needing to click Clear first.
4. **Given** a command fails with a non-zero exit code, **When** the output is displayed, **Then** the error is shown clearly and logged.

---

### User Story 3 - Errors are Visible Without External Monitoring (Priority: P1)

When something goes wrong — a command fails, a mapping is broken, or a background process crashes — the user sees the error immediately in the UI without needing Claude Code or a log file to diagnose.

**Why this priority**: Errors that are only captured in a log file are effectively invisible. Users and Claude Code should not need to poll a file to discover problems.

**Independent Test**: Trigger a command failure and verify the error is visible in the UI within the page, not just in a log file.

**Acceptance Scenarios**:

1. **Given** a background process fails, **When** the failure occurs, **Then** an error notification appears in the UI (e.g., toast, banner, or inline alert) with the actual error message.
2. **Given** multiple errors occur during a session, **When** the user looks at the UI, **Then** there is a visible indicator (e.g., error count badge) that errors have occurred.
3. **Given** errors have accumulated, **When** the user clicks the error indicator, **Then** they see a list of all session errors with timestamps and details.
4. **Given** errors are logged, **When** the session ends OR Claude Code reads the error file, **Then** the same errors are available for diagnosis.

---

### User Story 4 - AI Actions Page is Prominent in Navigation (Priority: P2)

The "AI Actions" page appears directly below "Dashboard" in the sidebar navigation, making it the second item users see. This reflects its importance as the primary interaction point.

**Why this priority**: Burying the AI chat below other views makes it feel secondary. It should be immediately accessible.

**Independent Test**: Open the UI and verify "AI Actions" is the second item in the sidebar, directly after "Dashboard".

**Acceptance Scenarios**:

1. **Given** the user opens the UI, **When** they look at the sidebar, **Then** "AI Actions" appears directly below "Dashboard".
2. **Given** the user clicks "AI Actions", **When** the page loads, **Then** the chat input is the first interactive element (above proactive suggestions and system overview).

---

### User Story 5 - Proactive Actions Execute Correctly (Priority: P2)

The assistant view suggests proactive actions based on system conditions. When the user clicks a suggested action, it runs a real, valid g2 CLI command.

**Why this priority**: Broken proactive actions (like the `system-status` bug) erode user trust and generate confusing errors.

**Independent Test**: Trigger each proactive action and verify the underlying CLI command exists and executes.

**Acceptance Scenarios**:

1. **Given** the assistant suggests a proactive action, **When** the user clicks it, **Then** the underlying CLI command executes successfully (no "command not found" errors).
2. **Given** MCP tool names are mapped to CLI commands, **When** any mapped command is invoked, **Then** the CLI command is a real, existing g2 subcommand.

---

### User Story 6 - Transparent AI Work (Priority: P2)

When an AI prompt is running, the user can toggle a "Work" view to see what Claude is doing — which MCP tools it's calling, what data it's querying, and how it's building the answer. This prevents the "stuck on Thinking..." experience and builds trust in the AI's reasoning.

**Why this priority**: Without visibility into the agent's work, users can't tell if the AI is making progress, stuck, or doing something wrong. Transparency is key to trust.

**Independent Test**: Submit an AI prompt that triggers MCP tool calls, toggle the Work view, and verify tool call names and progress are visible while the response is being generated.

**Acceptance Scenarios**:

1. **Given** an AI prompt is running, **When** the user looks at the output area, **Then** they see a toggle or tab to switch between "Response" and "Work" views.
2. **Given** the Work view is active, **When** Claude calls an MCP tool, **Then** the tool name and a summary of its input appear in real-time.
3. **Given** the Work view is active, **When** Claude produces intermediate text, **Then** it streams progressively.
4. **Given** the AI prompt completes, **When** the user views the final result, **Then** the Response view shows the clean answer and the Work view preserves the full trace.

---

### Edge Cases

- What happens when the user submits an empty command? (Run button should be disabled)
- What happens when a command produces very large output (>10,000 lines)?
- What happens when multiple commands are submitted rapidly?
- What happens when the database is down and proactive condition checks fail?
- What happens when a process is killed mid-execution (e.g., user navigates away)?
- What happens when the conversation history file grows very large (>1000 exchanges)?
- What happens when `claude` CLI is not installed? (Graceful degradation with clear message)

## Requirements *(mandatory)*

### Functional Requirements

**Navigation & Layout**
- **FR-001**: The AI interaction page MUST be named "AI Actions" in the sidebar navigation.
- **FR-002**: "AI Actions" MUST appear directly below "Dashboard" in the sidebar order.
- **FR-003**: On the AI Actions page, the chat/command input MUST be the first interactive element, above proactive suggestions and system overview.

**Command Execution**
- **FR-004**: The input and submit button MUST be wrapped in a form so clicking submit sends the typed text without requiring Enter.
- **FR-005**: Output MUST auto-refresh while a command is running, displaying new output within 2 seconds.
- **FR-006**: The submit button MUST remain available after a previous command completes.
- **FR-007**: The environment passed to background processes MUST strip the `CLAUDECODE` variable.

**Conversation History**
- **FR-008**: Each prompt and its response MUST be appended to a conversation history.
- **FR-009**: The conversation history MUST be rendered as a scrollable thread, oldest to newest.
- **FR-010**: The conversation history MUST persist to disk so it survives browser refresh and server restart.
- **FR-011**: A "Clear History" action MUST wipe both the in-memory and on-disk history.

**Error Surfacing**
- **FR-012**: All command failures MUST be displayed inline in the UI at the point of failure.
- **FR-013**: A persistent error indicator (e.g., count badge) MUST be visible when errors have occurred during the session.
- **FR-014**: Users MUST be able to view a list of all session errors from within the UI.
- **FR-015**: Errors MUST continue to be logged to the error file for Claude Code access.

**Mapping Correctness**
- **FR-016**: Every CLI command in the MCP tool-to-CLI mapping MUST correspond to a real g2 CLI subcommand.
- **FR-017**: Every proactive action's CLI command MUST reference a real g2 subcommand.
- **FR-018**: Tests MUST validate all mappings against actual `g2 --help` output to prevent regressions.

**AI Transparency**
- **FR-020**: When an AI prompt is running, the UI MUST offer a way to view intermediate work (tool calls, progress) alongside or instead of the final response.
- **FR-021**: The AI prompt command MUST use `--output-format stream-json --verbose` to capture structured events from `claude -p` stderr.
- **FR-022**: Tool call events MUST be parsed and displayed with tool name and input summary.

**Documentation**
- **FR-019**: User-facing documentation MUST describe the AI Actions page, conversation history, error indicators, and how to launch the UI (`g2 ui`).

### Key Entities

- **Conversation**: A sequence of exchanges (prompt + response pairs) within an AI Actions session.
- **Exchange**: A single prompt from the user and the corresponding system response (AI answer or CLI output).
- **Session Error**: A timestamped record of a failure during the UI session, with source and message.

## Automation *(consider)*

- **Proposed skill**: None needed — existing `/g2-dev` covers development workflow.
- **Rationale**: UI reliability is a hardening effort, not a repeatable workflow.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of MCP tool-to-CLI mappings reference valid CLI commands (verified by automated test).
- **SC-002**: 100% of proactive actions execute without "command not found" errors (verified by automated test).
- **SC-003**: Users can type a command and submit in a single action (no Enter required).
- **SC-004**: Command output appears within 2 seconds of being produced by the subprocess.
- **SC-005**: After a command completes, a new command can be submitted without clicking Clear.
- **SC-006**: Conversation history survives browser refresh (verified by manual test).
- **SC-007**: All runtime errors are visible in the UI without requiring log file access.
- **SC-008**: "AI Actions" is the second item in sidebar navigation.
- **SC-009**: Users can see which MCP tools are being called while an AI prompt is in progress.

## Assumptions

- The `claude` CLI is expected to be installed and on PATH for AI prompt features. If unavailable, CLI commands still work and a clear message explains that AI prompts require `claude`.
- Conversation history is stored as a JSONL file in `~/.g2/` (consistent with the existing error log pattern).
- Error surfacing in the UI supplements but does not replace the existing error log file — both channels are maintained.
- The conversation history file can be bounded (e.g., last 100 exchanges) to prevent unbounded growth.
