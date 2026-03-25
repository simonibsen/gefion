# Claude Code Guidelines for g2

## TDD Required (MANDATORY)

**Test-Driven Development is required for ALL code changes in this project.**

### The TDD Workflow

For any new feature, file, or code change:

1. **Write tests FIRST** - Create or modify test files in `tests/` before touching `src/`
2. **Run tests** - Verify the new tests FAIL (they test something that doesn't exist yet)
3. **Implement code** - Write the minimum code in `src/` to make tests pass
4. **Run tests again** - Verify all tests PASS
5. **Commit together** - Tests and implementation in the same commit

### What This Means in Practice

- **NEVER** create a new file in `src/g2/` without first creating its test file
- **NEVER** add a new function without first writing a test for it
- **NEVER** modify behavior without first writing a test that captures the expected change

### Example: Adding a New View

```
WRONG ORDER:
1. Create src/g2/ui/views/newview.py
2. Write render_newview() function
3. Add test later (or forget)

CORRECT ORDER:
1. Add "newview.py" to expected_views list in tests/test_ui_components.py
2. Add test_newview_has_render_function() test
3. Run pytest - see tests FAIL
4. Create src/g2/ui/views/newview.py with render_newview()
5. Run pytest - see tests PASS
```

### Enforcement Mechanisms

This project has multiple TDD enforcement layers:

1. **This file (CLAUDE.md)** - Instructions you must follow
2. **Pre-commit hook** - Blocks commits with src/ changes but no tests/ changes
3. **Claude Code PreToolUse hook** - Blocks writing to src/ before tests/
4. **Plan mode** - Plans must list test files before implementation files

### Bypassing (Use Sparingly)

If you absolutely must bypass TDD enforcement:
- Pre-commit: `git commit --no-verify` (explain why in commit message)
- Claude hook: Only for pure refactors with existing test coverage

## Plan Mode Requirements

When in plan mode, structure your plans with TDD order:

### Required Plan Structure

```markdown
# Feature Name

## Overview
Brief description of what we're building.

## Tests to Write FIRST
List test files and test cases that will be created/modified:
- `tests/test_feature.py` - test_feature_does_x, test_feature_handles_y

## Implementation Files
List source files to create/modify AFTER tests:
- `src/g2/module/feature.py` - FeatureClass, helper_function

## Implementation Steps
1. Write test_feature_does_x in tests/test_feature.py
2. Run pytest - verify it FAILS
3. Create src/g2/module/feature.py with minimal implementation
4. Run pytest - verify it PASSES
5. Write test_feature_handles_y
6. Run pytest - verify it FAILS
7. Extend implementation
8. Run pytest - verify all tests PASS

## Success Criteria
- [ ] All tests pass
- [ ] Feature works as specified
```

### Plan Review Checklist

Before exiting plan mode, verify:
- [ ] Tests section comes BEFORE implementation section
- [ ] Each implementation step is paired with a test
- [ ] Success criteria includes "All tests pass"

## Other Guidelines

### Code Style
- Follow existing patterns in the codebase
- Use type hints for all function signatures
- Add docstrings for public functions

### Observability
- New modules should import from `g2.observability`
- Use `@traced` decorator for significant operations
- Add logging with `logger = logging.getLogger(__name__)`
- Child spans MUST propagate parent context — orphaned spans are defects
- After implementing a feature, inspect its traces via `gefion span-check` before considering it complete

### Database
- Use parameterized queries (never string interpolation for SQL)
- Wrap JSONB values with `Json()` adapter for PostgreSQL

### Testing
- Database tests require `ENABLE_DB_TESTS=1` environment variable
- Tests automatically use a separate `gefion_test` database (derived from `DATABASE_URL` + `_test` suffix)
- All DB test connections MUST use `schema.test_db_url()` — never hardcode database URLs
- Use `OTEL_ENABLED=false` to disable tracing in tests
- Run full test suite: `ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" OTEL_ENABLED=false .venv/bin/python -m pytest`

## Active Technologies
- Python 3.10+ + Streamlit (UI framework), subprocess (process execution) (001-ui-reliability)
- JSONL files in `~/.g2/` (conversation history, error log); PostgreSQL (system state queries) (001-ui-reliability)

## Recent Changes
- 001-ui-reliability: Added Python 3.10+ + Streamlit (UI framework), subprocess (process execution)
