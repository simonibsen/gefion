# Feature Specification: Contextual Page AI

**Feature Branch**: `predictions` (spec only)
**Created**: 2026-03-27
**Status**: Draft
**Input**: Contextual AI assistant input on all UI pages with page-aware context injection

## User Scenarios & Testing

### User Story 1 - Ask About Current Page Output (Priority: P1)

A user is viewing the ML Pipeline predictions page and sees trend_class predictions with unfamiliar columns (Margin, Class). They type "what does margin mean?" into the chat input at the bottom of the page. The AI responds with an explanation specific to trend classification margin (confidence gap between top-2 predicted classes), referencing the data currently on screen.

**Why this priority**: This is the core value — contextual help without leaving the page. Every other story builds on this.

**Independent Test**: Can be tested by rendering any page with the chat component, submitting a question, and verifying the response includes page-specific context.

**Acceptance Scenarios**:

1. **Given** the user is on the ML Pipeline page viewing predictions, **When** they type "what does margin mean?" and submit, **Then** the response explains margin in the context of trend classification and references the current data.
2. **Given** the user is on the Dashboard page, **When** they ask "why is AAPL showing bearish?", **Then** the response references the specific prediction data visible on the dashboard.
3. **Given** the user is on any page, **When** they ask a question unrelated to the current page, **Then** the AI still answers but notes it's not specific to the current view.

---

### User Story 2 - Run Commands From Any Page (Priority: P2)

A user is on the Features page and realizes they need to compute features for a specific symbol. Instead of navigating to AI Actions, they type "compute features for AAPL" directly in the page's chat input. The command executes and the result appears inline.

**Why this priority**: Eliminates the navigation tax of switching to AI Actions for quick operations. Builds on the existing command routing in `assistant.py`.

**Independent Test**: Can be tested by submitting a CLI command from any page's chat input and verifying execution and output display.

**Acceptance Scenarios**:

1. **Given** the user is on any page, **When** they type a gefion CLI command, **Then** the command is executed and output is displayed inline on that page.
2. **Given** the user runs a command that changes data, **When** the command completes, **Then** the page content above refreshes to reflect the change.

---

### User Story 3 - Page Context Suggests Relevant Actions (Priority: P3)

When the user opens the chat input on the ML Pipeline page and there are no quantile predictions, the AI proactively suggests: "No quantile predictions found. You could train a quantile model with `gefion ml train --model-type quantile`." The suggestions adapt based on what the page shows (empty states, errors, stale data).

**Why this priority**: Proactive guidance reduces user confusion, especially for new users or unfamiliar states. Depends on P1 context injection working first.

**Independent Test**: Can be tested by rendering a page with a known empty/error state and verifying the chat component displays relevant suggestions.

**Acceptance Scenarios**:

1. **Given** the predictions page shows no quantile predictions, **When** the user opens the chat input, **Then** a hint suggests how to generate quantile predictions.
2. **Given** the data page shows stale OHLCV data (>7 days old), **When** the user opens the chat input, **Then** a hint suggests running a data update.

---

### Edge Cases

- What happens when the AI backend (Claude) is unavailable? Show a clear error and fall back to direct CLI command execution only.
- What happens when the page context is very large (e.g., 200 rows of predictions)? Summarize rather than sending all rows — send aggregate stats (row count, date range, types present, etc.).
- What happens when the user submits while a previous response is still loading? Queue the request or show "please wait."
- What happens on pages with no meaningful data context (e.g., Settings, Documentation)? The chat still works but without page-specific context injection.

## Requirements

### Functional Requirements

- **FR-001**: Every UI page MUST have a collapsible chat input component at the bottom of the page.
- **FR-002**: The chat component MUST automatically inject the current page's context into each request, including: page name, active filters/selections, summary of displayed data (row counts, date ranges, key values), and any error states.
- **FR-003**: The chat component MUST support both natural language questions (routed to Claude with MCP tools) and direct CLI commands (executed via subprocess), using the same parsing logic as the existing AI Actions page.
- **FR-004**: The chat component MUST display responses inline on the current page without navigating away.
- **FR-005**: The chat component MUST render as a fixed-position floating bar at the bottom of the viewport — a thin input strip ("Ask about this page...") always visible regardless of scroll position. On click/focus, it expands upward into a chat panel showing conversation history and responses. It MUST NOT push page content down or require scrolling to find.
- **FR-006**: Conversation history within a page session MUST persist across Streamlit reruns (using session state), but MUST NOT persist across browser refreshes (page-level, not global).
- **FR-007**: Each page MUST define a `get_page_context()` function (or equivalent) that returns a structured summary of the current page state for context injection.
- **FR-008**: The existing AI Actions page MUST continue to work as a full-featured standalone assistant with global conversation history.

### Key Entities

- **PageContext**: Structured summary of a page's current state (page name, filters, data summary, errors). Each page produces one.
- **ChatMessage**: A single user or assistant message within a page's conversation (role, content, timestamp).
- **ChatComponent**: The reusable Streamlit component rendered at the bottom of each page, managing input, context injection, command parsing, and response display.

## Automation

- **Proposed skill**: None needed — this is a UI component, not a workflow.
- **Rationale**: The feature is interactive and page-driven; there's no repeatable CLI workflow to automate.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Users can ask a contextual question on any page and receive a relevant, page-aware response without navigating away.
- **SC-002**: The chat component loads on every page in under 0.5 seconds with no visible layout shift.
- **SC-003**: Page context is automatically included in every AI request — the user never needs to manually describe what they're looking at.
- **SC-004**: Direct CLI commands entered in the chat input execute successfully on any page, matching the behavior of the existing AI Actions page.
- **SC-005**: The floating bar is always visible at the bottom of the viewport without interfering with page content or requiring scrolling to reach.

## Assumptions

- The existing `parse_command_input()` and command routing logic from `assistant.py` can be extracted into a shared component without breaking the AI Actions page.
- Claude API access is available from the Streamlit process (same as current AI Actions implementation).
- Page context summaries can be kept under ~500 tokens to avoid excessive prompt costs.
- Streamlit's session state is sufficient for per-page conversation persistence (no database storage needed for page chat).
