# Implementation Plan: Contextual Page AI

**Branch**: `predictions` | **Date**: 2026-03-27 | **Spec**: `specs/002-contextual-page-ai/spec.md`

## Summary

Add a fixed-position floating chat bar to every UI page. Users can ask contextual questions or run commands without navigating to AI Actions. Each page provides a context summary (filters, data stats, empty states) that's injected into AI requests. Reuses existing command parsing and Claude routing from assistant.py.

## Technical Context

**Language/Version**: Python 3.10+ / Streamlit
**Primary Dependencies**: Streamlit (UI), claude CLI (AI routing), psycopg (DB queries)
**Storage**: Streamlit session state only (no new tables)
**Testing**: pytest (file pattern checks, compile checks, integration tests)
**Constraints**: Must not break existing AI Actions page; context summaries under ~500 tokens

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Database-First | PASS | No schema changes |
| II. TDD | PASS | Tests first for component + context functions |
| III. CLI-First | PASS | Chat routes to existing CLI commands |
| IV. Observability | PASS | Reuses existing traced CLI commands |
| V. Consistent Presentation | PASS | Single shared component, consistent styling |
| VI. Simplicity | PASS | One new component file, minimal per-view additions |

## Project Structure

### Source Code

```
src/gefion/ui/
├── components/
│   ├── chat.py              # NEW — shared chat widget + command routing
│   ├── database.py          # existing
│   └── status.py            # existing
├── views/
│   ├── dashboard.py         # MODIFY — add get_page_context()
│   ├── ml.py                # MODIFY — add get_page_context()
│   ├── data.py              # MODIFY — add get_page_context()
│   ├── features.py          # MODIFY — add get_page_context()
│   ├── charts.py            # MODIFY — add get_page_context()
│   ├── backtest.py          # MODIFY — add get_page_context()
│   ├── experiments.py       # MODIFY — add get_page_context()
│   ├── assistant.py         # MODIFY — extract shared logic to chat.py
│   ├── documentation.py     # no context needed (static docs)
│   └── settings.py          # no context needed (config page)
├── app.py                   # MODIFY — render chat after page dispatch
└── history.py               # existing, no changes

tests/
├── test_ui_chat_component.py    # NEW — chat component tests
└── test_ui_page_context.py      # NEW — page context function tests
```

## Implementation Steps

### Phase 1: Extract shared chat logic (P1 foundation)

**Tests first**: `tests/test_ui_chat_component.py`
- `test_chat_component_module_exists` — file exists and compiles
- `test_parse_command_input_extracted` — function importable from `components.chat`
- `test_render_chat_widget_exists` — render function exists
- `test_chat_css_contains_fixed_position` — CSS has `position: fixed`

**Implementation**: `src/gefion/ui/components/chat.py`
1. Extract from `assistant.py`:
   - `parse_command_input()` (lines 165-226)
   - `parse_stream_event()` (lines 88-141)
   - `MCP_TOOL_MAP` dict (lines 27-70)
   - `UI_OPERATOR_PROMPT` constant
2. Create `render_chat_widget(page_context: dict)`:
   - Fixed-position CSS bar at viewport bottom
   - Text input with placeholder "Ask about this page..."
   - On submit: inject page_context into prompt, route via parse_command_input
   - Display response inline in expandable panel
   - Manage conversation in `st.session_state[f"_chat_{page}_messages"]`
3. Update `assistant.py` to import shared logic from `components/chat.py`

### Phase 2: Page context functions (P1 core value)

**Tests first**: `tests/test_ui_page_context.py`
- `test_all_data_views_have_get_page_context` — each view with data defines the function
- `test_page_context_returns_required_keys` — returns dict with page_name, summary, filters, data_stats
- `test_page_context_handles_no_db` — graceful fallback when DB unavailable

**Implementation**: Add `get_page_context()` to each view:

| View | Context includes |
|------|-----------------|
| dashboard.py | Market movers count, system stats, bullish/bearish counts |
| ml.py | Active models, prediction counts by type, dataset info, selected filters |
| data.py | Symbol count, OHLCV date range, freshness, any running processes |
| features.py | Feature count (active/total), function count, computation coverage |
| charts.py | Selected symbol, chart type, date range |
| backtest.py | Strategy count, latest backtest date, comparison results summary |
| experiments.py | Experiment count by status (proposed/running/completed) |

### Phase 3: App integration (P2 commands work everywhere)

**Implementation**: `src/gefion/ui/app.py`
1. After page dispatch (`render_*()` call), call:
   ```python
   from gefion.ui.components.chat import render_chat_widget
   context = get_context_for_current_page(page_name)
   render_chat_widget(context)
   ```
2. `get_context_for_current_page()` dynamically imports the view's `get_page_context()` if it exists

### Phase 4: Contextual hints (P3 proactive suggestions)

**Implementation**: Extend each `get_page_context()` to include `suggestions` list:
- ML page with no quantile predictions: "Train a quantile model to see price range predictions"
- Data page with stale data: "Data is X days old — run gefion data-update"
- Features page with 0 coverage: "Run gefion feat-compute to populate features"

The chat component displays suggestions as placeholder hints when the panel is expanded and empty.

## Key Files to Modify

| File | Change | Lines affected |
|------|--------|----------------|
| `src/gefion/ui/components/chat.py` | NEW | ~200 lines |
| `src/gefion/ui/app.py` | Add chat render after dispatch | ~10 lines |
| `src/gefion/ui/views/assistant.py` | Extract shared functions to chat.py | Import changes |
| `src/gefion/ui/views/ml.py` | Add get_page_context() | ~30 lines |
| `src/gefion/ui/views/dashboard.py` | Add get_page_context() | ~20 lines |
| `src/gefion/ui/views/data.py` | Add get_page_context() | ~20 lines |
| `src/gefion/ui/views/features.py` | Add get_page_context() | ~15 lines |
| `src/gefion/ui/views/charts.py` | Add get_page_context() | ~10 lines |
| `src/gefion/ui/views/backtest.py` | Add get_page_context() | ~10 lines |
| `src/gefion/ui/views/experiments.py` | Add get_page_context() | ~10 lines |
| `tests/test_ui_chat_component.py` | NEW | ~60 lines |
| `tests/test_ui_page_context.py` | NEW | ~50 lines |

## Verification

- All existing tests pass (`pytest tests/`)
- Chat component renders on every page without errors
- Questions on ML page include prediction type/count context in AI prompt
- CLI commands entered in chat execute and display results
- AI Actions page continues to work unchanged
- Chat bar is visible at viewport bottom without scrolling
