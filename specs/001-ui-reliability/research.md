# Research: UI Reliability

**Date**: 2026-03-18

## R1: Streamlit session_state durability

**Decision**: Use JSONL file at `~/.g2/ai_history.jsonl` for conversation persistence.

**Rationale**: `st.session_state` does not survive browser refresh or server restart. JSONL is already the pattern used by `g2.ui.errors` for the error log. Consistent, simple, no new dependencies.

**Alternatives considered**:
- SQLite: Adds a dependency and complexity for a single-user local tool. Rejected (YAGNI).
- Database table: Requires schema change (governance approval) for a UI-only feature. Rejected.
- Pickle/shelve: Not human-readable, harder to debug. Rejected.

## R2: Conversation history bounding

**Decision**: Cap at 100 exchanges. On append, if count exceeds 100, truncate oldest entries.

**Rationale**: Unbounded growth risks slow page loads (Streamlit re-renders all content on rerun). 100 exchanges is generous for exploratory use. The file stays small (~500KB worst case).

**Alternatives considered**:
- No cap: Risk of multi-MB files slowing the UI. Rejected.
- Configurable cap: YAGNI — hardcode 100, make it a constant that's easy to change later.

## R3: Sidebar navigation ordering in Streamlit

**Decision**: Reorder the page list in `app.py` (or wherever sidebar is constructed) so "AI Actions" is the second entry after "Dashboard".

**Rationale**: Streamlit renders sidebar items in the order they're defined. Reordering the list is sufficient — no custom CSS or hacks needed.

**Alternatives considered**:
- Custom sidebar with st.sidebar.radio: More control but fights Streamlit's native multi-page pattern. Rejected.

## R4: In-UI error surfacing approach

**Decision**: Add an error indicator to the sidebar/page header that shows session error count. Clicking it expands an error list. Errors continue to be logged to `~/.g2/ui_errors.jsonl`.

**Rationale**: Inline visibility without disrupting workflow. The existing error log file is preserved for Claude Code access. No new storage — just reading from the existing error file.

**Alternatives considered**:
- Streamlit toast notifications (`st.toast`): Good for transient alerts but messages disappear. Not sufficient alone for persistent error visibility.
- Dedicated error page: Overkill for a single-user tool. Rejected.
- Combined approach (toast + indicator): Toast for immediate notification + persistent badge. Worth considering but adds complexity — start with badge only (YAGNI).

## R5: Renaming "Ask AI / Run Command" to "AI Actions"

**Decision**: Rename the view to "AI Actions" in both the sidebar and page header. Update the subheader and caption text accordingly.

**Rationale**: "AI Actions" is concise, covers both natural language prompts and CLI commands, and positions the page as a primary action center.

**Alternatives considered**:
- "Assistant": Generic, doesn't convey the interactive/action nature.
- "Chat": Implies only conversational use, doesn't cover CLI commands.
- "Command Center": Too dramatic for a local dev tool.
