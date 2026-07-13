# Implementation Plan: Sector-State Signals for Discovery

**Branch**: `013-sector-signals` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/013-sector-signals/spec.md`

## Summary

Sector-level market states become ordinary named feature series through the
spec-011 molds: the market-dispatcher cross-section gains the `sector`
column, generated DB-resident bodies (one per sector x metric, the 012
model-bodies precedent) compute sector relative strength and sector breadth
with a membership floor visible in the body, `macro derive` computes them
like every derived series, and `--series all` grows to mean "every enabled
market function in the DATABASE" so the nightly cron covers new series
forever. Discovery consumes the series by name with zero guarantee changes.
NO DDL.

## Technical Context

**Language**: Python 3.10+ (existing codebase)
**Dependencies**: psycopg (streams/upserts), existing `gefion.macro` +
`gefion.features.dispatcher` (011), numpy (tests only)
**Storage**: PostgreSQL + TimescaleDB — existing tables only
(`feature_functions` scope='market', `macro_series`, `computed_features`,
`stocks.sector`). **Zero DDL.**
**Testing**: pytest, `ENABLE_DB_TESTS=1`, canonical schema creators,
`schema.test_db_url()`
**Platform**: dev macOS + prod Linux (sloth)
**Constraints**: TDD; parameterized SQL; observability spans; membership
floor declared in the body (operator-visible); sector list discovered from
data; naming collision-free with `macro_`/`pred_` namespaces
**Scale**: 15 sectors -> <= ~30 generated bodies; initial full-history derive
~30 x one cross-section stream (measured on prod before the hunt; nightly
incremental thereafter)

## Constitution Check

- I Database-First: PASS — bodies live in `feature_functions` (generated,
  create-if-absent, DB wins after seeding; 012 precedent). No ad-hoc DDL.
- II TDD: PASS — synthetic two-sector world tests written first per
  increment; test DB isolation via `schema.test_db_url()`.
- III CLI-First: PASS — `macro seed-sectors` (new door) + existing
  `macro derive`; JSON output; MCP parity.
- IV Observability/Perf: PASS — spans on seed + derive (existing);
  initial prod derive is timed and recorded (feeds #120 evidence).
- V Presentation: PASS — docs/curriculum/routing in the same increment.
- VI Honest errors: PASS — unknown sector refuses listing known ones;
  thin days are gaps; uncomputable atoms diagnosed by existing screens.

## Project Structure

### Documentation (this feature)

```text
specs/013-sector-signals/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
└── contracts/cli.md
```

### Source Code (repository root)

```text
src/gefion/
├── macro/
│   ├── market_bodies.py      # + sector_signal_bodies(sector, slug) generator
│   └── derived.py            # 'all' = SEED_BODIES ∪ enabled DB market fns
├── features/dispatcher.py    # cross-section stream + s.sector column
└── cli.py                    # macro seed-sectors door
tests/
├── test_sector_signals.py            # NEW: two-sector synthetic world (US1)
└── test_discovery_sector_atoms.py    # NEW: e2e hunt with sector atoms (US2)
```

## Increments

1. **Dispatcher sector column** (test: rows carry `sector`, NULL sector
   present as None): add `s.sector` to the streamed SELECT + row dicts —
   one line each; all existing market bodies unaffected (they ignore
   unknown keys).
2. **Generated sector bodies** (tests: signs on planted drifts; floor ->
   gap; NULL-sector exclusion; name normalization): `sector_signal_bodies`
   in `market_bodies.py` — `sector_rs_<slug>` (median member `ret_20` minus
   median ALL-rows `ret_20`) and `sector_breadth_<slug>` (% members with
   `close > indicator_sma_200`), `MIN_MEMBERS = 30` inline in the body;
   slug = lowercase, non-alnum -> `_`.
3. **Seeding door** (tests: creates-if-absent for sectors meeting a
   `--min-members` census floor, DB wins on re-run, reports skipped-thin
   sectors, unknown `--sectors` refuses listing known): CLI
   `macro seed-sectors [--sectors CSV] [--min-members N]`; census from
   `stocks.sector` (discovered, parameterized).
4. **`derive --series all` covers the DB** (test: a planted enabled market
   fn outside SEED_BODIES is derived by 'all'; disabled ones skipped):
   change `all` expansion in the CLI to SEED_BODIES ∪ enabled scope='market'
   names — the nightly cron line then covers sector + model series with no
   crontab edits (the 012-specific derive in the top-up line becomes
   redundant-but-harmless).
5. **Discovery e2e** (test: synthetic hunt pre-registers
   `macro_sector_rs_*` tercile atoms; run completes, guarantees intact,
   uncomputed sector series -> uncomputable-proposal diagnostic): no
   discovery code changes expected — this is the SC-1303 proof.
6. **Surfaces + prod**: docs (USER_GUIDE macro section + REGIMES
   vocabulary note + DEPLOYMENT cron note), curriculum M10 aside, MCP
   parity, /gefion routing; fresh-DB suite; PR/merge; prod: seed -> timed
   full derive (recorded; #120 evidence) -> the sector hunt (task #48;
   h=20d, holdout 80wk, sector RS/breadth terciles for top-6 sectors +
   proven market vocabulary, standard feature signals) -> verdict report.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- CLI: `macro seed-sectors` (new), `macro derive` ('all' semantics grow —
  documented); MCP parity for the new door
- Docs: USER_GUIDE (sector series + naming + floors), REGIMES (sector
  vocabulary + membership-vintage caveat verbatim from spec), DEPLOYMENT
  (cron note)
- Curriculum: Module 10 aside — sector states as a new conditioning
  dimension; membership-vintage honesty
- /gefion routing: "sector rotation" / "which sectors lead" -> seed +
  derive + hunt recipe

## Complexity Tracking

- Generated bodies (<=30) over hand-written ones: uniform, DB-resident,
  operator-editable — accepted (012 precedent; alternative one-pass Python
  aggregator rejected: logic would leave the database, violating
  Constitution I and the 011 owner decision).
- Initial full-history derive is ~30 full cross-section streams; measured,
  one-time, incremental after. If painful -> #120 candidate (shared-stream
  derive), not a v1 blocker.
