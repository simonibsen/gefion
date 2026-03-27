# Data Model: Contextual Page AI

No new database tables. All state is in-memory via Streamlit session state.

## Entities

### PageContext

Structured summary of a page's current state. Produced by each view's `get_page_context()`.

| Field | Type | Description |
|-------|------|-------------|
| page_name | str | Display name of the current page |
| summary | str | 1-3 sentence description of what the page shows |
| filters | dict | Active filter selections (e.g. model, date, symbol) |
| data_stats | dict | Aggregate stats of displayed data (row count, date range, types) |
| empty_states | list[str] | Things that are empty or missing (e.g. "no quantile predictions") |
| errors | list[str] | Any errors currently displayed on the page |
| suggestions | list[str] | Contextual hints based on page state |

### ChatMessage

A single message in a page's conversation. Stored in `st.session_state`.

| Field | Type | Description |
|-------|------|-------------|
| role | str | "user" or "assistant" |
| content | str | Message text |
| timestamp | str | ISO timestamp |
| mode | str | "ai", "cli", or "mcp" (user messages only) |

### Session State Keys

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `_chat_{page}_messages` | list[ChatMessage] | Conversation history per page |
| `_chat_{page}_pending` | bool | Whether a request is in flight |
| `_chat_expanded` | bool | Whether the chat panel is open (global) |
