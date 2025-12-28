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

### 9. Observability by Default
Every new module should include observability from the start:

- **Tracing**: Use `create_span()` or `@traced` decorator for key functions
- **Logging**: Add `logger = logging.getLogger(__name__)` and log important events
- **Attributes**: Include relevant context (symbols, counts, durations) in spans

**Example:**
```python
from g2.observability import create_span, traced
import logging

logger = logging.getLogger(__name__)

@traced("mymodule.process")
def process_data(symbols):
    logger.info(f"Processing {len(symbols)} symbols")
    ...

# Or with context manager for more control:
def run_backtest(config):
    with create_span("backtest.run", initial_cash=config.cash) as span:
        result = _do_backtest(config)
        span.set_attribute("trade_count", len(result.trades))
        return result
```

**When to add observability:**
- Entry points (CLI commands, API handlers)
- Long-running operations (backtests, data ingestion, ML training)
- Database operations (bulk inserts, complex queries)
- External API calls (AlphaVantage, etc.)

**Span Parenting:**
Ensure child spans are properly nested under parent spans. Orphaned spans create disconnected traces that are hard to follow in Tempo/Jaeger.

```python
# GOOD: Child span is nested under parent
def process_batch(items):
    with create_span("process_batch", count=len(items)):
        for item in items:
            process_item(item)  # If this creates a span, it's a child

def process_item(item):
    with create_span("process_item", item_id=item.id):  # Automatically a child
        ...

# BAD: Orphaned spans in threads without context propagation
def process_parallel(items):
    with create_span("process_parallel"):
        with ThreadPoolExecutor() as pool:
            pool.map(process_item, items)  # Spans here are ORPHANED!

# GOOD: Propagate context to threads
from g2.ingest.universe import propagate_context

@propagate_context
def process_item_with_context(item):
    with create_span("process_item"):  # Now properly parented
        ...
```

Use `propagate_context` decorator (from `g2.ingest.universe`) when spawning work in thread pools to maintain trace hierarchy.

## Git Hooks

The project uses Git hooks to enforce development rules.

### Installation

```bash
./scripts/hooks/install.sh
```

This installs all hooks from `scripts/hooks/` to `.git/hooks/`.

### commit-msg Hook
- Rejects commits with "Claude" in author name
- Rejects commits with anthropic.com email addresses
- Rejects commits with "Co-Authored-By: Claude" in messages
- Displays development rules reminder

### pre-commit Hook
- Checks new Python files in `src/g2/` for observability imports
- Warns if files are missing `from g2.observability import` or `import logging`
- Reminds about span parenting for thread context propagation
- Currently a warning only (does not block commit)

### pre-push Hook
- Runs full test suite before push
- Aborts push if any tests fail

### prepare-commit-msg Hook
- Displays development rules reminder before commit

All hooks are stored in `scripts/hooks/` and installed to `.git/hooks/`.

## Pre-Commit Checklist

- [ ] Tests written first (TDD approach)
- [ ] All tests passing (488+ passing, 0 failed)
- [ ] Commit message reviewed (no AI tool mentions)
- [ ] Code follows existing patterns
- [ ] **Observability**: New modules have tracing/logging?
- [ ] **Span Parenting**: Child spans nested under parents? Thread context propagated?
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
