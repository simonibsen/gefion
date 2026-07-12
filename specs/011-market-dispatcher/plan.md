# Implementation Plan: Market-Level Feature Dispatcher Mode

**Branch**: `011-market-dispatcher` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)
**Epic**: #114

## Summary

Make market-level functions first-class registry citizens: Python bodies in
`feature_functions` (scope='market'), executed per-date over the stock
cross-section by the EXISTING sandbox (`_exec_in_sandbox`, dispatcher.py:942),
streamed by date batches. Migrate `breadth_sma200`/`dispersion_20` from
repo-resident SQL to DB bodies behind a numeric-equality gate; repo code
becomes seed-only. One DDL: a `scope` column on `feature_functions`
(**owner approval required before applying**).

## Technical Context

**Language**: Python 3.10+ (existing codebase) + numpy (bodies), psycopg (streaming reads/writes)
**Existing components reused**: `_exec_in_sandbox` + SAFE_MODULES whitelist
(numpy/pandas already allowed — sufficient, no expansion); `feature_functions`
registry incl. `inputs JSONB` (declared per-stock feature columns) and
`enabled`; `macro.catalog` series rows; `computed_features` storage keyed by
macro series id; `macro derive` CLI door; feat-fx lifecycle (#89).
**Storage**: ONE new column — `feature_functions.scope` ('stock'|'market',
default 'stock'). No new tables; values land exactly where they do today.
**Testing**: pytest; DB tests via `schema.test_db_url()`; fixtures call
canonical schema creators (CI collection-order lesson); synthetic worlds.
**Performance**: stream one ordered query per derive run, group per date in
python, numpy inside bodies; ≤10 min full-history on sloth (SC-1102), peak
memory = one date batch (~6k rows × declared columns).

## Constitution Check

- **I. Database-First**: PASS — this feature IS database-first (bodies move
  INTO the DB; DB becomes source of truth).
- **II. TDD**: PASS — every increment tests-first; migration-equality test is
  the acceptance gate.
- **III. CLI-First**: PASS — `macro derive` unchanged as the door;
  `feat-fx-import` gains market scope; MCP parity.
- **IV. Observability**: PASS — spans per function with timing/rows; derive
  span parents per-function spans.
- **V. Consistent CLI Presentation**: PASS — existing output component.
- **VI. Simplicity**: PASS with justification — no second executor, no new
  tables; the one new column is the minimal discriminator.
- **Schema Governance**: **PASS** — `scope` column DDL **approved by owner 2026-07-12** (data-model.md); apply via two-file rule + dictionary regen.

## Project Structure

```
src/gefion/features/dispatcher.py     # + market-mode execution path (reuses _exec_in_sandbox)
src/gefion/macro/derived.py           # becomes: seeds + orchestration (stream, batch, store); SQL bodies deleted
src/gefion/macro/market_bodies.py     # NEW: the two seed Python bodies (seeding source only)
src/gefion/cli.py                     # macro derive (dispatcher-backed), feat-fx-import scope
mcp-server/server.py                  # macro_derive unchanged; feature tools inherit scope
sql/schema.sql + sql/migrations/      # scope column (two-file rule) — AFTER approval
scripts/gen_data_dictionary.py        # column via regen
tests/test_market_dispatcher.py       # NEW: mode, sandbox, lifecycle, failure isolation
tests/test_macro_derived.py           # UPDATED: same behavioral contract, dispatcher-backed + equality gate
```

## Increments (each: tests RED → implement → GREEN → commit)

1. **DDL approval + scope column** (blocked on owner): exact DDL in
   data-model.md; on approval apply two-file rule + dictionary regen;
   `feat-fx-list` shows scope.
2. **Market execution mode**: `run_market_function(conn, fn_row, dates,
   min_stocks)` in dispatcher — per-date cross-section from ONE streamed
   query (close/high/low/volume + `inputs`-declared feature columns), calls
   the sandboxed body's `compute(rows)` per date, collects (date, value);
   NaN/inf/None → gap; wrong shape/raise → function-level failure, zero
   writes for that function.
3. **Derive orchestration**: `macro derive` iterates enabled scope='market'
   functions matched to macro series; incremental per function from its last
   stored date; disabled → skipped-and-reported; per-function child spans;
   partial-failure exit status.
4. **Seed bodies + migration gate**: two Python bodies in
   `market_bodies.py`, seeded create-if-absent (operator edits persist);
   equality test computes legacy-SQL and dispatcher outputs on the same
   synthetic world and asserts numeric equality (1e-9); THEN the legacy SQL
   path is deleted.
5. **Lifecycle integration**: enable/disable honored; validate/fix zero
   orphans; export/import round-trips scope; feat-fx-list scope column.
6. **Polish + prod**: docs (USER_GUIDE, DEVELOPMENT pattern, DATA_DICTIONARY
   regen), curriculum Module 2, span-check, fresh-DB suite, PR/merge; prod:
   deploy → migrate column → seed → `derive --full` spot-equality vs stored
   history → zero orphans confirmed.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **CLI**: `macro derive` (flags unchanged; per-function skip/fail
  reporting), `feat-fx-import` accepts market-scope bodies, `feat-fx-list`
  shows scope.
- **MCP**: `macro_derive` unchanged; feature listing payloads gain scope
  (additive).
- **Docs**: USER_GUIDE (editing a market body — no deploy needed),
  DEVELOPMENT.md (DB-is-source-of-truth + seed-only pattern),
  DATA_DICTIONARY (regen).
- **Learning**: Module 2 — market-level function code now lives in the
  database like per-stock code; repo seeds it once.
- **/gefion routing**: "change a market feature's formula" → edit body +
  `macro derive --full` (documented).

## Complexity Tracking

- Sandboxed per-date calls × ~6,700 dates: mitigated by numpy bodies (µs
  each) + one streamed query; measured against SC-1102.
- DB-resident code drift vs repo seeds: accepted by design (operator edits
  persist); existing `checksum` column available for audit.
- Migration equality on prod history: CI gate on synthetic worlds + prod
  spot-check (sampled full-history diff) before SQL deletion.
