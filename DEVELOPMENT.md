# Development Rules

## Core Principles

### 1. Test-Driven Development (TDD)
- **True Red/Green TDD**: Tests must actually fail before implementation
- Write tests FIRST with real assertions (not just `pass` stubs)
- Run the test and verify it FAILS (red) before writing implementation
- Implement code until test PASSES (green)
- Every bug fix starts with a failing test that reproduces the bug
- Run full test suite before committing
- New features require tests
- Bug fixes require regression tests

**Not acceptable:**
```python
def test_something():
    pass  # This is NOT a test
```

**Required:**
```python
def test_something():
    result = my_function(input)
    assert result == expected  # Real assertion that can fail
```

### 2. Commit Messages
- NEVER mention AI tools, assistants, or automation
- Write as if you authored all changes
- Use conventional commits format: `<type>: <subject>`
- Types: `fix`, `feat`, `refactor`, `test`, `docs`, `chore`

### 3. Testing Requirements
- All tests must pass before push
- Minimum: 488 tests passing
- Test command:
  ```bash
  ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/
  ```

### 4. Code Quality
- Fix the code, not the tests (unless tests are genuinely wrong)
- No skipping tests without clear documentation
- Retired tests need explanation of why they're obsolete

### 5. When Tests Fail
1. First investigate if the CODE has a bug
2. Only fix tests if they're testing incorrectly
3. Never skip tests to make CI pass
4. Document why tests are retired if obsolete

## Architecture Principles

### 6. KISS (Keep It Simple, Stupid)
- Prefer direct, explicit solutions over clever abstractions
- Don't build for hypothetical future requirements
- If a feature isn't used, remove it
- The simplest solution that works is usually the best

### 7. Loose Coupling
- Functions should be self-contained and independently testable
- Avoid cross-function dependencies via shared caches or globals
- Explicit is better than implicit (no magic routing or discovery)
- Feature definitions map directly to functions by name

### 8. Performance Mindset
- Keep performance top of mind, but be smart about it
- Know where the bottlenecks are before optimizing (profile first)
- I/O (DB writes, API calls) typically dominates compute time
- Premature optimization is the root of all evil - start simple, measure, then optimize
- When in doubt, benchmark: `time` commands, tracing spans, `EXPLAIN ANALYZE`

## Git Hooks

The project uses Git hooks to enforce development rules:

### commit-msg Hook
Located at `.git/hooks/commit-msg`, this hook enforces:
- Rejects commits with "Claude" in author name
- Rejects commits with anthropic.com email addresses
- Rejects commits with "Co-Authored-By: Claude" in messages

This prevents AI attribution from appearing in the git history.

### pre-push Hook
Located at `.git/hooks/pre-push`, this hook:
- Runs full test suite before push
- Aborts push if any tests fail

Both hooks are executable and run automatically.

## Pre-Commit Checklist

- [ ] Tests written first (TDD approach)
- [ ] All tests passing (488+ passing, 0 failed)
- [ ] Commit message reviewed (no AI tool mentions)
- [ ] Code follows existing patterns
- [ ] KISS: Is this the simplest solution? Remove unused complexity
- [ ] Coupling: Are functions self-contained? No implicit dependencies
- [ ] Performance: Bottlenecks identified? Optimizing the right thing?

## Quick Commands

### Run full test suite
```bash
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/
```

### Run specific test file
```bash
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/test_filename.py -v
```

### Run tests with coverage
```bash
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest tests/ --cov=src/g2
```

## Database Setup

Tests require TimescaleDB running on port 6432:
```bash
docker compose up -d
```

## Common Issues

### TimescaleDB Extension
- Extension loads at server level, can't be unloaded per-connection
- Drop tables individually instead of `DROP SCHEMA CASCADE`

### Prepared Statements
- Cannot execute multiple SQL commands in one `execute()` call
- Split multi-statement INSERTs into separate calls

### Feature Definitions
- All features require: `source_table`, `source_column`, `store_type`, `active`, `params`
