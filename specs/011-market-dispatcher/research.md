# Research — Market-Level Dispatcher Mode (011)

## R1 — Scope discriminator

**Decision**: a real column, `feature_functions.scope TEXT NOT NULL DEFAULT
'stock' CHECK (scope IN ('stock','market'))`.
**Rationale**: "correct from the start" — the distinction is structural, not
decorative; tags are freeform and unenforceable, `inputs` JSONB abuse hides
semantics. A CHECKed column makes invalid states unrepresentable and shows up
in the dictionary.
**Alternatives**: `tags @> {market}` (rejected: no integrity); a JSONB key
(rejected: invisible to the schema); separate table (rejected: Simplicity —
same lifecycle, one discriminator).

## R2 — The market body contract

**Decision**: the stored body defines `compute(rows)` where `rows` is a list
of per-stock dicts for ONE date (`symbol, close, high, low, volume` + each
feature named in the row's `inputs` JSONB, e.g. `{"features":
["indicator_sma_200"]}`), returning a float or `None`. NaN/inf → treated as
None (gap). Anything else (wrong type, raise) → function-level failure.
**Rationale**: mirrors how per-stock bodies are exec'd (define-a-callable in
the sandbox namespace); list-of-dicts keeps the sandbox free of pandas
requirements while allowing numpy one-liners; one-date granularity keeps
memory bounded and the contract explainable in one sentence.
**Alternatives**: whole-history frame in one call (rejected: memory,
FR-1104); pandas DataFrame contract (rejected: heavier surface; bodies can
build arrays trivially).

## R3 — Streaming strategy

**Decision**: per derive run and function, ONE server-side cursor over
`stock_ohlcv JOIN stocks (asset_type='Stock') LEFT JOIN` each declared
feature, ordered by date, `itersize` batches; group rows per date in python;
call the body once per completed date; buffer (date, value) and bulk-insert
per N dates.
**Rationale**: single pass, bounded memory (one date in flight), the DB does
the joining it is good at; LEFT JOIN keeps stocks lacking a declared feature
visible to the body as missing keys (body decides).
**Alternatives**: per-date queries (rejected: 6,700 round trips); full
materialization (rejected: FR-1104).

## R4 — Seeding and source-of-truth semantics

**Decision**: `market_bodies.py` holds the canonical seed text; seeding is
INSERT ... ON CONFLICT (name) DO NOTHING at derive time. DB wins forever
after; a `--reseed <name>` escape hatch overwrites explicitly (operator
action, reported), never implicitly.
**Rationale**: spec's "operator edits persist across deploys" verbatim; the
explicit reseed keeps recovery honest instead of magic.
**Alternatives**: checksum-gated auto-update (rejected: silently clobbers
edits); no reseed (rejected: unrecoverable typos).

## R5 — Failure isolation and reporting

**Decision**: each function computes fully into memory-light buffers and
writes ONLY on success of its own range; failure records
(function, reason, dates-attempted) in the derive report and exit status is
non-zero if any function failed; other functions in the run are unaffected.
Sandbox refusals (ImportError from safe_import) are failures with the
sandbox's own message.
**Rationale**: FR-1108 verbatim; write-on-success gives zero-partial-garbage
without transactions spanning the stream.

## R6 — Migration equality gate

**Decision**: a test computes both implementations on the same synthetic
world (numbers chosen with exact float representations where possible) and
asserts per-date equality within 1e-9; the legacy SQL functions remain in the
tree until the gate passes in CI AND a prod spot-check (sampled dates diffed
against stored history) passes — then the SQL is deleted in the same
increment that flips derive to the dispatcher path.
**Rationale**: SC-1101; "delete only after proven equal" prevents a silent
history rewrite.

## R7 — Matching functions to series

**Decision**: convention stays name-based: market function `X` ↔ macro series
`X` ↔ feature `macro_X`; derive creates the series row and feature definition
(function_name = X) exactly as today.
**Rationale**: zero new mapping state; the fifth-hunt atoms and stored values
keep their names (FR-1109/SC-1106).
