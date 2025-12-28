# Instructions for AI Assistants

## CRITICAL RULES (Always Follow)

### 1. TDD IS MANDATORY - NO EXCEPTIONS

**HARD RULE: Before ANY edit to `src/`, you MUST first create or update a test file in `tests/`.**

**Workflow:**
1. When given a coding task, FIRST write a failing test in `tests/`
2. Run the test - verify it FAILS (red)
3. THEN implement the feature in `src/`
4. Run the test - verify it PASSES (green)
5. Only then commit both test and implementation together

**This is not optional.** The pre-commit hook will reject commits that modify `src/` without corresponding `tests/` changes.

**If you catch yourself editing `src/` without a test:**
- STOP immediately
- Write the test first
- Then continue with implementation

### 2. NEVER Mention AI Tools
**Forbidden in:**
- Commit messages
- Code comments (unless specifically requested by user)
- Documentation
- Pull request descriptions
- Git commit Co-Authored-By tags

**Forbidden terms:**
- Claude, AI, GPT, LLM, assistant
- "automated", "generated", "AI-generated"
- Any references to AI authorship

**Write commits as if the human developer wrote everything.**

### 3. When Tests Fail
**Priority order:**
1. Check if the production code has a bug → Fix the code
2. Check if the test is wrong → Fix the test
3. Check if functionality changed → Update test appropriately
4. Only retire tests if they're genuinely obsolete (document why)

**Never skip tests without clear documentation of why they're obsolete.**

### 4. Code Quality Standards (KISS)
- Fix root causes, not symptoms
- No unnecessary refactoring
- Follow existing code patterns
- Prefer editing existing files over creating new ones
- Only add what's requested, nothing extra

## Pre-Commit Requirements

Before creating any commit:
1. Run full test suite
2. Verify 468+ tests passing, 0 failed
3. Review commit message for forbidden AI terms
4. Ensure changes match what user requested

## Testing Commands

### Full test suite
```bash
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/
```

### Expected results
- 468+ passed
- ~19 skipped (retired tests with documentation)
- 0 failed
- 7 warnings (from security import blocking tests - expected)

### Run specific test
```bash
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/test_filename.py -v
```

## Project-Specific Context

### Database
- TimescaleDB on port 6432
- Extension loads at server level (can't be unloaded)
- Drop tables individually, not `DROP SCHEMA CASCADE`

### Feature Definitions
Required fields:
- `source_table`, `source_column`
- `store_table`, `store_column`, `store_type`
- `active`, `params`

### Common Patterns
- CLI error output: use `res.output` not `res.stdout` (Typer writes to stderr)
- Prepared statements: can't execute multiple SQL commands in one call
- Index names: use separate `sql.Identifier()`, not embedded in string

## Code Review Principles

### Always Prefer
- Fixing bugs in production code over changing tests
- Simple solutions over complex ones
- Existing patterns over new abstractions
- Explicit over implicit

### Avoid
- Over-engineering
- Premature optimization
- Unnecessary abstraction
- Feature creep beyond user request

## When User Says "Continue"

1. Check context for what task was in progress
2. Review any previous errors or failures
3. Continue with same approach unless it clearly failed
4. Don't restart from scratch - build on previous work

## Debugging Failed Tests

1. Read the full error message
2. Check if it's a test fixture issue (table doesn't exist, etc.)
3. Look for patterns (multiple similar failures)
4. Fix root cause, not individual symptoms
5. Verify fix with full test suite, not just one test

## Remember

- You're helping a human developer
- Write code/commits as if they wrote it
- Tests exist for a reason - respect them
- When in doubt, ask the user
- Quality over speed
