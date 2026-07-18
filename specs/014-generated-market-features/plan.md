# Implementation Plan: Generated Market-Level Features with an Owner Gate

**Branch**: `014-generated-market-features` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/014-generated-market-features/spec.md`

## Summary

Two capabilities on top of the shipped 011 market dispatcher: (1) the cycle
runner can generate candidate market-scope function bodies which land in a
**new `market_function_candidates` table** — never in `feature_functions` —
so the "pending candidates cannot execute" invariant holds by construction;
a human reviews (code + declared inputs + provenance + seeded sandbox
dry-run) and approval promotes the body into `feature_functions` through the
existing upsert + definition-pairing path. (2) A **composite execution mode**:
market functions whose `inputs` declare named macro series (`{"series":
[...]}`) receive one row per date of stored input values and write to the
macro home — discriminated by input shape, no schema change, with cycle
refusal and topological derive ordering.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: psycopg (DB), existing `gefion.features.dispatcher`
sandbox + market executor (011), `gefion.macro` (derive/seeding, 013),
`gefion.experiments.cycle_runner` (generation), typer (CLI), streamlit (UI)
**Storage**: PostgreSQL + TimescaleDB. ONE new table —
`market_function_candidates` (candidate bodies + provenance + review state +
dry-run record). No changes to existing tables. **DDL requires owner
approval — exact DDL in data-model.md, flagged in the plan report.**
**Testing**: pytest; DB tests via `schema.test_db_url()` under
`ENABLE_DB_TESTS=1`
**Target Platform**: Linux/macOS server (dev laptop + sloth prod)
**Project Type**: single project (CLI + MCP + Streamlit UI)
**Performance Goals**: composite full-history derive in the same order as
existing derived series (minutes; realistically seconds — ~6,700 dates ×
a handful of input series); candidate dry-run < 1s; no regression to the
nightly derive.
**Constraints**: generated bodies execute only in the existing sandbox
(whitelist unchanged); a pending/rejected candidate must be unable to write
a single stored value through any path; schema change minimal (one table).
**Scale/Scope**: tens of candidates over time, single-digit composites
initially; 26y × ~20 macro series input space.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Database-First**: PASS — candidate and approved bodies live in the DB;
  git exports remain backups. Schema change follows the two-file rule
  (schema.sql + migration) and is **proposed, not executed** until owner
  approval (Schema Governance).
- **II. TDD (non-negotiable)**: PASS — every phase in tasks will list test
  files before implementation files; red-green enforced by hooks.
- **III. CLI-First**: PASS — every operation lands as `gefion macro
  candidate ...` / `gefion macro register-composite` CLI commands with
  `--json`, mirrored MCP tools, and UI queue/review rendering; `/gefion`
  operator skill reviewed for the new tools.
- **IV. Observability**: PASS — new modules import `gefion.observability`;
  candidate lifecycle + composite execution get spans with propagated parents;
  span-check after implementation.
- **V. Consistent CLI Presentation**: PASS — new commands use the existing
  `get_output`/presentation helpers; `--json` bypasses formatting.
- **VI. Simplicity**: PASS — no new `scope` value (input-shape discriminates
  composite mode); one new table is justified by the gate-by-construction
  invariant (see Complexity Tracking); no new dependencies.

**Post-design re-check (after Phase 1)**: PASS — design artifacts introduce
no additional tables, no whitelist expansion, no new dependencies.

## Project Structure

### Documentation (this feature)

```text
specs/014-generated-market-features/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (includes proposed DDL)
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── candidate-review-contract.md   # gate semantics + CLI/MCP surface
│   └── composite-function-contract.md # compute(row) contract for composites
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
sql/
├── schema.sql                                   # + market_function_candidates (owner-approved)
└── migrations/20260718_000001_market_function_candidates.sql

src/gefion/
├── macro/
│   ├── candidates.py        # NEW: candidate store, dry-run, review, promotion
│   ├── composites.py        # NEW: composite registration, input validation, cycle refusal, topo order
│   ├── market_bodies.py     # + market-scope generation templates (candidate seeds)
│   └── derived.py           # derive orchestration: composites run after inputs (topo pass)
├── features/
│   └── dispatcher.py        # + run_composite_function (per-date named-series rows);
│                            #   run_market_function untouched
├── experiments/
│   └── cycle_runner.py      # market-scope generation path → writes candidates, never feature_functions
├── cli.py                   # macro candidate list|show|approve|reject; macro register-composite
└── ui/views/                # candidates queue + review packet rendering

mcp-server/
└── server.py                # macro_candidate_* + macro_register_composite tools

tests/
├── test_market_candidates.py        # NEW: store/gate/review/promotion (DB)
├── test_market_candidate_dryrun.py  # NEW: seeded synthetic dry-run, sandbox violations
├── test_macro_composites.py         # NEW: composite exec, gaps, cycles, topo, idempotence (DB)
├── test_cycle_runner_market_gen.py  # NEW: generation path targets candidates
├── test_market_candidates_cli.py    # NEW: CLI surface + refusal messages
└── test_regime_interfaces.py        # extended: MCP/UI parity assertions
```

**Structure Decision**: single project; new code concentrates in
`gefion.macro` (candidate + composite domain logic) with execution in the
existing dispatcher module, mirroring how 011/013 split orchestration
(macro) from execution (dispatcher).

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces (FR-042 / Constitution III)**:
  - CLI: `gefion macro candidate list|show|approve|reject`,
    `gefion macro register-composite`; `macro derive` gains composite
    awareness transparently. All support `--json`.
  - MCP: `macro_candidate_list`, `macro_candidate_show`,
    `macro_candidate_approve`, `macro_candidate_reject`,
    `macro_register_composite` — thin wrappers over the CLI (approve/reject
    record the approver; they are human-directed acts exactly like the
    existing `experiment_approve` precedent, and cycles/schedulers have no
    path to them).
  - UI: candidates queue + full review packet (body, inputs, provenance,
    dry-run) rendered read-only; decisions happen through CLI/MCP.
- **Documentation**: docs/USER_GUIDE.md (new commands + gate concept),
  docs/ARCHITECTURE.md (candidate table + composite flow),
  docs/MCP_WORKFLOWS.md (review workflow), DATA_DICTIONARY regen (new
  table), README command list, `/gefion` operator skill routing update.
- **Learning materials**: `.claude/commands/gefion-learn.md` — extend the
  market-features module with an aside: "the machine proposes, a human owns
  the gate" (generation → review → promotion), and composite mode as the
  macro-of-macro door.
- **Delivery rule**: each user story lands with its surfaces + docs in the
  same increment.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New table `market_function_candidates` | The gate invariant "pending/rejected candidates cannot execute" must hold by construction | Storing candidates in `feature_functions` with a `pending` status requires every execution path (derive --series all, explicit derive, export/import, UI listings) to re-check review state forever — one missed check executes unreviewed generated code against production data; a separate table makes the unsafe state unrepresentable |
