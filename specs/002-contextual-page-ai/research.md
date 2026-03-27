# Research: Contextual Page AI

## Decision 1: Fixed-position floating bar in Streamlit

**Decision**: Use CSS `position: fixed` injection via `st.markdown(unsafe_allow_html=True)` for the floating bar, with Streamlit containers for the expanded chat panel.

**Rationale**: Streamlit doesn't natively support fixed-position elements, but the codebase already uses CSS injection extensively (app.py lines 18-39, assistant.py lines 738-751). A `position: fixed; bottom: 0` container with high z-index will float above all content. The expanded panel renders as a standard Streamlit container within the fixed wrapper.

**Alternatives considered**:
- `st.sidebar` placement: Rejected — sidebar is for navigation, adding chat would make it cramped
- Render at bottom of each page (no fixed position): Rejected — requires scrolling to bottom on long pages
- Streamlit `st.modal()`: Not available in current Streamlit version
- Custom Streamlit component (JavaScript): Overkill for this, CSS injection achieves the same result

## Decision 2: Where to render the chat — app.py vs each view

**Decision**: Render in `app.py` after the page dispatch, not inside each individual view.

**Rationale**: The chat component is identical on every page — only the context differs. Rendering in `app.py` after the page's `render_*()` call means:
- One integration point, not 10
- No risk of a view forgetting to include it
- Context is gathered by calling `get_page_context()` which each view optionally defines

**Alternatives considered**:
- Each view calls `render_chat()` at bottom: Rejected — 10 integration points, easy to forget one
- Sidebar chat: Rejected — sidebar already used for navigation

## Decision 3: Context injection approach

**Decision**: Each view exports an optional `get_page_context()` function. The chat component imports and calls it. If a view doesn't define one, the chat works with just the page name.

**Rationale**: Keeps context logic colocated with the view that produces the data. Context is a summary dict (~100-300 tokens), not raw data. Views that don't need rich context (Settings, Documentation) simply don't define the function.

**Alternatives considered**:
- Global context registry: Over-engineered for 10 pages
- Pass context as argument from app.py: Would require app.py to know about each view's internals
- Scrape page content from rendered HTML: Fragile, slow, unreliable

## Decision 4: Reuse vs. new command routing

**Decision**: Extract `parse_command_input()` and the stream-JSON parsing from `assistant.py` into a shared module `ui/components/chat.py`. The AI Actions page (`assistant.py`) will import from the shared module.

**Rationale**: The existing code handles AI routing (claude -p), CLI commands, and MCP tool mapping. Duplicating this would violate DRY and create divergence. The extraction is straightforward — the functions are already well-isolated.

**Alternatives considered**:
- Duplicate the code: Rejected — maintenance burden
- Make assistant.py a library: Rejected — it's a view module, not a library

## Decision 5: Conversation scope

**Decision**: Per-page session state (`st.session_state[f"_chat_{page_name}_messages"]`). Clears on browser refresh. Separate from global AI Actions history.

**Rationale**: Page chat is ephemeral — "what does this column mean?" doesn't need to persist across sessions. The global AI Actions history (`~/.gefion/ai_history.jsonl`) continues to track full command history for auditability.

**Alternatives considered**:
- Shared history across pages: Confusing — chat about Dashboard data showing on ML page
- Persist to JSONL: Overkill for contextual Q&A
