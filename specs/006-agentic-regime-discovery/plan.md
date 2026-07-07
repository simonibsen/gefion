# Implementation Plan: Agentic Regime Discovery

**Branch**: `006-agentic-regime-discovery` | **Date**: 2026-07-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-agentic-regime-discovery/spec.md`

## Summary

Let the autonomous agent *discover* regimes — enumerate principle-seeded candidate regimes
from a pre-registered bounded grammar (plus continuous-interaction tests and, gated by
fresh-holdout validation, free-form/detector candidates), fit and label them under **nested
segregation** (discovery never sees the outer holdout), test conditional edges against a
declared **signal universe** (features in v1), enter **every** test into one flat FDR family,
and record everything — candidates, limits, verdicts — in auditable ledgers. Admitted edges
accrue trust through **walk-forward grading** (probation = first fold). A standing
**negative-control suite** proves the loop finds nothing in noise, in CI.

**Technical approach**: a new `src/gefion/regimes/discovery/` package that composes existing
primitives — 005's `RegimeDefinition`/AST (`origin='machine'`), causal labels, `conditional.py`
+ `experiments.statistical.apply_fdr`, `experiments.holdout.HoldoutWindow`, the feature-function
sandbox (for detector candidates), and the probation mechanism (for grading re-tests) — plus a
new `regime_discovery` experiment type dispatched from `experiments/core.py`. Three pluggable,
pre-registered seams: `signal_source`, `grading_scheme`, `universe_filter`.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: numpy/scipy (tests + enumeration), psycopg (ledgers), existing gefion
modules (`regimes.*`, `experiments.statistical/holdout/core`, `features.dispatcher` sandbox,
probation); no new third-party dependencies planned (Simplicity)
**Storage**: PostgreSQL + TimescaleDB. New tables — `regime_discovery_runs`,
`regime_candidates` (the ledger), `discovery_diagnostics`, `regime_trust_grades` — **schema
change, PROPOSED for owner approval, not executed** (Schema Governance). Discovered
definitions reuse `regime_definitions` (`origin='machine'`).
**Testing**: pytest; synthetic-data generators for negative controls (no real data needed);
DB tests via `schema.test_db_url()` + `ENABLE_DB_TESTS`; CI runs the negative-control suite
**Target Platform**: dev machine builds/tests with synthetic data; real discovery runs on the
prod host (sloth) against the 26.7-year dataset
**Project Type**: single project (CLI-first research system)
**Performance Goals**: v1 signal_source=features keeps a discovery cycle to minutes (queries +
statistical tests, no simulation); enumeration deterministic and O(candidates); negative-control
CI job < 5 minutes (tiny synthetic universes, ≥20 seeds)
**Constraints**: nested segregation enforced by construction (FR-101/102); every test counted
(FR-104); seeded reproducibility (FR-111); fail-closed everywhere (FR-107); spans on all new
modules; parameterized SQL; `Json()` for JSONB
**Scale/Scope**: v1 budgets — primitive library M ≈ 20–40 atoms, depth K ≤ 2, per-cycle
candidate budget ≈ 50–200; family = candidates × signals(≈20) × buckets(≤3) ≈ 10⁴ tests max,
comfortably in flat-BH territory (per Clarification Q1)

## Constitution Check

*GATE: pass before Phase 0; re-check after Phase 1.*

| Principle | Status | How the plan satisfies it |
|---|---|---|
| I. Database-First | **PASS (gated)** | Discovered regimes are `regime_definitions` rows (`origin='machine'`) exported to JSON like any other; search spaces/ledgers/diagnostics live in DB. New tables are **proposed DDL only** — owner approval required before touching `schema.sql` (see contracts/sql.md). |
| II. TDD (non-negotiable) | **PASS** | Every increment lists tests before src; the negative-control suite *is* the feature's own acceptance test and runs in CI (FR-112/SC-101). |
| III. CLI-First | **PASS** | New `gefion regime discover` group; MCP tools mirror it; UI Regimes page gains a Discovery tab; `/gefion` operator skill updated (see contracts/interfaces.md — parity matrix is the source of truth). |
| IV. Observability | **PASS** | All `discovery/` modules import `gefion.observability`; spans per run/candidate batch with parent propagation; bulk paths respect the OTLP 4MB lesson (batch attributes, not per-candidate spans). |
| V. Consistent CLI Presentation | **PASS** | `output.py` helpers; `--json` parity everywhere. |
| VI. Simplicity | **PASS** | No new statistical machinery in v1 (reuses `compute_holdout_pvalue`, `apply_fdr`, `conditional_pvalues`, HAC interaction); no new sandbox (reuses feature-function execution); bootstrap deferred by clarification; three pluggable seams are interfaces only where the spec demands them. |
| Tech Constraints | **PASS** | Python 3.10+, parameterized SQL, `Json()`, no deep learning. |

No unjustified violations. Gated item: schema approval (propose-don't-execute).

## Project Structure

### Documentation (this feature)

```text
specs/006-agentic-regime-discovery/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1 (interfaces.md, cli.md, mcp.md, ui.md, sql.md)
└── tasks.md             # Phase 2 (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
src/gefion/regimes/discovery/        # NEW package
├── __init__.py
├── grammar.py            # primitive library, deterministic enumeration to depth K, candidate hashing
├── segregation.py        # DiscoveryDataContext — inner-fold-only data access; boundary assertions
├── universe.py           # pluggable universe_filter (test-ticker, asset-type, passthrough)
├── signals.py            # pluggable signal_source (v1: features → per-observation edge records)
├── edges.py              # conditional edge tests per candidate (reuses conditional.py + interaction.py)
├── ledger.py             # runs, candidate ledger, diagnostics (sample-dependent vs structural)
├── detectors.py          # expressive tier: sandboxed detector-function candidates, stability/degeneracy checks
├── freshhold.py          # fresh-holdout reserve: declaration, consumption tracking, no-reuse enforcement
├── grading.py            # pluggable grading_scheme; walk-forward default; forward-only enforcement
└── runner.py             # orchestration: pre-register → enumerate → segregate → test → FDR → ledger → verdicts

src/gefion/experiments/types/regime_discovery.py   # NEW experiment type (dispatch in experiments/core.py)
src/gefion/cli.py                                  # `regime discover` subcommands
mcp-server/server.py                               # regime_discover_* tools
src/gefion/ui/views/regimes.py                     # Discovery tab (runs, ledger, verdicts)

sql/schema.sql + sql/migrations/NNNNNN_regime_discovery.sql   # PROPOSED (owner approval)

tests/
├── test_discovery_grammar.py       # enumeration exactness, determinism, hashing, depth cap
├── test_discovery_segregation.py   # inner-only access; leaked-split negative demonstration
├── test_discovery_universe.py      # filter chain, passthrough declaration, pre-registration
├── test_discovery_edges.py         # per-candidate conditional tests; family assembly
├── test_discovery_ledger.py        # candidate persistence, diagnostics tagging, reproducibility
├── test_discovery_detectors.py     # sandbox execution, stability, degeneracy, T3 accounting
├── test_discovery_freshhold.py     # reserve declaration, consumption, no-reuse
├── test_discovery_grading.py       # walk-forward folds, forward-only rule, descriptive backward
├── test_discovery_negative_control.py  # SC-101/102: zero survivors in noise; planted-regime recovery
└── test_discovery_interfaces.py    # CLI/MCP/UI parity + experiment-type dispatch
```

**Structure Decision**: one cohesive `discovery/` package under `regimes/` (discovery is a
regime concern, not an experiments concern), with a thin `regime_discovery` experiment type
adapting it into the cycle framework. Each spec-mandated pluggable seam is its own small
module; the runner composes them. Mirrors 005's file-per-capability layout.

## Increment sequencing (drives tasks.md)

Per Clarification Q5, all three tiers ship in v1 as **independently shippable increments**:

1. **Foundational**: schema (gated) + grammar + segregation + universe + ledger — the honest
   skeleton, fully synthetic-testable
2. **Tier 1 — interaction discovery**: candidates evaluated via continuous-interaction; first
   end-to-end verdicts + negative control in CI
3. **Tier 2 — grammar discovery**: enumerated compositional candidates through the conditional
   gate; the flagship SC-102 planted-regime test
4. **Grading**: walk-forward `grading_scheme` + probation integration
5. **Tier 3 — expressive**: fresh-holdout reserve + detector runtime + stability/degeneracy
6. **Surfaces, docs & learning**: experiment-type integration, CLI/MCP/UI parity, full
   documentation + curriculum updates (below)

## Interfaces, Documentation & Learning Impact *(mandatory; definition of done — every increment)*

Three-interface coverage and documentation are **not a final phase afterthought**; each
increment that adds a user-facing operation lands its CLI + MCP + UI surface and docs in the
same increment (the 005 pattern). The complete set this feature must update:

- **contracts/interfaces.md** — the CLI/MCP/UI parity matrix (source of truth; enforced by
  `test_discovery_interfaces.py`)
- **README.md** — `regime discover` commands in the CLI reference; discovery under
  Autonomous Experiments
- **docs/USER_GUIDE.md** — full `regime discover` reference
- **docs/REGIMES.md** — discovery section: threat model T1–T6, the defense stack, the three
  pluggable seams, how to read candidate + diagnostics ledgers, walk-forward trust grades
- **docs/MCP_WORKFLOWS.md** — the `regime_discover_*` tools
- **docs/DATA_DICTIONARY.md** — regenerate after schema approval (`gen_data_dictionary.py --write`)
- **specs/004-autonomous-experiments** cross-reference — `regime_discovery` as a new
  experiment type with its stricter gate
- **`.claude/commands/gefion.md`** (operator skill) — discovery mode + tool routing + honesty
  rules (never present an unadmitted candidate as a finding)
- **Learning materials — `.claude/commands/gefion-learn.md`**: extend the curriculum with a
  **Module 10 — Agentic Regime Discovery**: concepts (the six traps and why discovery without
  guardrails is a false-positive machine; nested segregation; counting the losers; trust that
  accrues forward), Do (run a bounded synthetic discovery, read the candidate ledger, find the
  refused low-power candidates in the diagnostics ledger), Checkpoint (learner explains why a
  backward era-slice can never raise a trust grade). Concept-first per the curriculum's rules,
  with linked terms.
- **tests/test_docs_drift.py** — must pass for all new commands and MCP tools

## Complexity Tracking

> No constitution violations require justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
