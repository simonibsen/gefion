# Development Rules

## Core Principles

### 1. Test-Driven Development (TDD)
- Write tests FIRST, then implementation
- Every bug fix starts with a failing test
- Run full test suite before committing
- New features require tests
- Bug fixes require regression tests

### 2. Commit Messages
- NEVER mention AI tools, assistants, or automation
- Write as if you authored all changes
- Use conventional commits format: `<type>: <subject>`
- Types: `fix`, `feat`, `refactor`, `test`, `docs`, `chore`

### 3. Testing Requirements
- All tests must pass before push
- Minimum: 468 tests passing
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

## Pre-Commit Checklist

- [ ] Tests written first (TDD approach)
- [ ] All tests passing (468+ passing, 0 failed)
- [ ] Commit message reviewed (no AI tool mentions)
- [ ] Code follows existing patterns
- [ ] No unnecessary changes or refactoring

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
